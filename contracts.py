#!/usr/bin/env python3
"""
Contract addresses and constants for The Graph on Arbitrum One

This module centralizes all smart contract addresses and function selectors
used by grtinfo CLI tools.
"""

# =============================================================================
# Contract Addresses (Arbitrum One)
# =============================================================================

# Core Graph Protocol contracts
REWARDS_MANAGER = "0x971B9d3d0Ae3ECa029CAB5eA1fB0F72c85e6a525"
STAKING = "0x00669A4CF01450B64E8A2A20E9b1fcb71E61eF03"
SUBGRAPH_SERVICE = "0xB2Bb92D0dE618878e438b55d5846cFEcD9301105"
GRT_TOKEN = "0x9623063377AD1B27544C965cCd7342f7EA7e88C7"

# Legacy/alternate names for compatibility
STAKING_CONTRACT = STAKING
REWARDS_CONTRACT = REWARDS_MANAGER


# =============================================================================
# Event Topics (keccak256 hashes of event signatures)
# =============================================================================

# HorizonRewardAssigned(address indexed indexer, address indexed allocationID, uint256 amount)
HORIZON_REWARD_ASSIGNED_TOPIC = "0xa111914d7f2ea8beca61d12f1a1f38c5533de5f1823c3936422df4404ac2ec68"

# AllocationResized(address indexed indexer, address indexed allocationId, bytes32 indexed subgraphDeploymentId, uint256 newTokens, uint256 oldTokens)
ALLOCATION_RESIZED_TOPIC = "0x6db4a6f9be2d5e72eb2a2af2374ac487971bf342a261ba0bc1cf471bf2a2c31f"


# =============================================================================
# Function Selectors (first 4 bytes of keccak256 of function signature)
# =============================================================================

# RewardsManager.getRewards(address _rewardsIssuer, address _allocationID) returns (uint256)
GET_REWARDS_SELECTOR = "0x0e6f0a5e"

# Staking.getDelegation(address _indexer, address _delegator) returns (uint256 shares, uint256 tokensLocked, uint256 tokensLockedUntil)
GET_DELEGATION_SELECTOR = "0x15049a5a"

# HorizonStaking.getTokensAvailable(address serviceProvider, address verifier, uint32 delegationRatio) returns (uint256)
GET_TOKENS_AVAILABLE_SELECTOR = "0x872d0489"

# HorizonStaking.getDelegationPool(address serviceProvider, address verifier) returns tuple
GET_DELEGATION_POOL_SELECTOR = "0x561285e4"

# SubgraphService.getDelegationRatio() returns (uint32)
GET_DELEGATION_RATIO_SELECTOR = "0x1ebb7c30"


# =============================================================================
# Network Constants
# =============================================================================

# GRT token decimals
GRT_DECIMALS = 18

# PPM (parts per million) - used for reward cuts
PPM_BASE = 1_000_000

# Default thawing period in epochs (approximately 28 days)
DEFAULT_THAWING_PERIOD = 28

# Epoch duration in seconds (approximately 24 hours on Arbitrum)
EPOCH_DURATION_SECONDS = 86400  # 24 hours

# Maximum allocation age in epochs before rewards expire
MAX_ALLOCATION_EPOCHS = 28


# =============================================================================
# Helper Functions
# =============================================================================

def to_checksum_address(address: str) -> str:
    """Convert address to checksum format (simple implementation)
    
    For proper checksum, use web3.Web3.to_checksum_address()
    This is a fallback that just ensures 0x prefix and lowercase.
    """
    addr = address.lower()
    if not addr.startswith('0x'):
        addr = '0x' + addr
    return addr


def pad_address(address: str) -> str:
    """Pad address to 32 bytes for use in event topic filters

    Args:
        address: Ethereum address (with or without 0x prefix)

    Returns:
        0x-prefixed 64-character hex string (32 bytes)
    """
    addr = address.lower()
    if addr.startswith('0x'):
        addr = addr[2:]
    return '0x' + addr.zfill(64)


# =============================================================================
# HorizonStakingClient - Workaround for subgraph tokenCapacity bug
# =============================================================================
# Issue: https://github.com/graphprotocol/graph-network-subgraph/issues/323
#
# The subgraph's tokenCapacity can be stale because delegationExchangeRate
# is not updated when rewards accumulate in the delegation pool. This client
# fetches the accurate value directly from the HorizonStaking contract.

import requests
import time
import logging
from typing import Optional, List, Dict


class HorizonStakingClient:
    """Client for fetching staking data directly from the HorizonStaking contract.

    This is a workaround for the subgraph's tokenCapacity being out of sync
    with the contract's getTokensAvailable value.
    """

    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self._delegation_ratio: Optional[int] = None

    def _eth_call(self, to: str, data: str) -> Optional[str]:
        """Make an eth_call to the contract."""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": to, "data": data}, "latest"],
            "id": 1,
        }
        try:
            response = requests.post(self.rpc_url, json=payload, timeout=10)
            response.raise_for_status()
            result = response.json()
            if "error" in result:
                return None
            return result.get("result")
        except requests.RequestException:
            return None

    def _encode_address(self, address: str) -> str:
        """Encode an address as a 32-byte hex string."""
        return address.lower().replace("0x", "").zfill(64)

    def _encode_uint32(self, value: int) -> str:
        """Encode a uint32 as a 32-byte hex string."""
        return hex(value)[2:].zfill(64)

    def _decode_uint256(self, hex_data: str) -> int:
        """Decode a uint256 from hex string."""
        if not hex_data or hex_data == "0x":
            return 0
        return int(hex_data, 16)

    def get_delegation_ratio(self) -> int:
        """Get the delegation ratio from the SubgraphService contract."""
        if self._delegation_ratio is not None:
            return self._delegation_ratio

        result = self._eth_call(SUBGRAPH_SERVICE, GET_DELEGATION_RATIO_SELECTOR)
        if result:
            self._delegation_ratio = self._decode_uint256(result)
            return self._delegation_ratio
        return 16  # Default fallback

    def get_tokens_available(self, indexer_address: str) -> Optional[int]:
        """Get the tokens available for an indexer from the contract.

        Returns the value in wei (not GRT), or None if the call fails.
        """
        delegation_ratio = self.get_delegation_ratio()

        data = (
            GET_TOKENS_AVAILABLE_SELECTOR
            + self._encode_address(indexer_address)
            + self._encode_address(SUBGRAPH_SERVICE)
            + self._encode_uint32(delegation_ratio)
        )

        result = self._eth_call(STAKING, data)
        if result:
            return self._decode_uint256(result)
        return None


# =============================================================================
# AllocationResizeClient - Fetch AllocationResized events from on-chain
# =============================================================================

log = logging.getLogger(__name__)


class AllocationResizeClient:
    """Client to fetch AllocationResized events from the SubgraphService contract via RPC.

    The AllocationResized event is not exposed as an entity in the network subgraph,
    so we query it directly via eth_getLogs.
    """

    # Arbitrum: ~0.25s per block = 4 blocks/second
    BLOCKS_PER_SECOND = 4

    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self._session = requests.Session()

    def _rpc_call(self, method: str, params: list):
        """Make a JSON-RPC call."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        response = self._session.post(self.rpc_url, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if "error" in result:
            raise Exception(result["error"])
        return result.get("result")

    def _get_block_number(self) -> int:
        result = self._rpc_call("eth_blockNumber", [])
        return int(result, 16)

    def _get_block_timestamps(self, block_numbers: List[int]) -> Dict[int, int]:
        """Fetch timestamps for a set of block numbers via batch RPC."""
        if not block_numbers:
            return {}
        unique_blocks = sorted(set(block_numbers))
        # Batch RPC request
        batch = [
            {
                "jsonrpc": "2.0",
                "method": "eth_getBlockByNumber",
                "params": [hex(bn), False],
                "id": i,
            }
            for i, bn in enumerate(unique_blocks)
        ]
        try:
            response = self._session.post(self.rpc_url, json=batch, timeout=30)
            response.raise_for_status()
            results = response.json()
        except Exception as e:
            log.debug(f"Failed to batch-fetch block timestamps: {e}")
            return {}

        timestamps = {}
        for res in results:
            if "result" in res and res["result"]:
                block = res["result"]
                bn = int(block["number"], 16)
                timestamps[bn] = int(block["timestamp"], 16)
        return timestamps

    def _fetch_resizes(self, topics: list, hours: int = 48) -> List[Dict]:
        """Core logic: fetch AllocationResized events matching the given topics."""
        try:
            current_block = self._get_block_number()
        except Exception as e:
            log.debug(f"Failed to get block number: {e}")
            return []

        from_block = max(0, current_block - int(hours * 3600 * self.BLOCKS_PER_SECOND))

        try:
            logs = self._rpc_call("eth_getLogs", [{
                "address": SUBGRAPH_SERVICE,
                "topics": topics,
                "fromBlock": hex(from_block),
                "toBlock": hex(current_block),
            }])
        except Exception as e:
            log.warning(f"Failed to fetch AllocationResized logs: {e}")
            return []

        if not logs:
            return []

        # Parse events
        events = []
        block_numbers = []
        for entry in logs:
            block_num = int(entry["blockNumber"], 16)
            block_numbers.append(block_num)

            indexer = "0x" + entry["topics"][1][-40:]
            alloc_id = "0x" + entry["topics"][2][-40:]
            deployment_id = entry["topics"][3]

            data = entry["data"]
            # Remove 0x prefix, data = newTokens (32 bytes) + oldTokens (32 bytes)
            data_hex = data[2:] if data.startswith("0x") else data
            new_tokens = int(data_hex[:64], 16)
            old_tokens = int(data_hex[64:128], 16)

            events.append({
                "block_number": block_num,
                "indexer": indexer,
                "alloc_id": alloc_id,
                "deployment_id": deployment_id,
                "new_tokens": new_tokens,
                "old_tokens": old_tokens,
            })

        # Fetch block timestamps
        timestamps = self._get_block_timestamps(block_numbers)
        for event in events:
            event["timestamp"] = timestamps.get(event["block_number"], 0)

        # Filter out events where timestamp could not be resolved
        if timestamps:
            events = [e for e in events if e["timestamp"] > 0]
        else:
            # All timestamps failed — estimate from block number
            now = int(time.time())
            for event in events:
                blocks_ago = current_block - event["block_number"]
                event["timestamp"] = now - int(blocks_ago / self.BLOCKS_PER_SECOND)

        return events

    def get_resizes_by_deployment(self, deployment_hex_id: str, hours: int = 48) -> List[Dict]:
        """Fetch AllocationResized events for a specific deployment.

        Args:
            deployment_hex_id: The deployment ID as 0x-prefixed bytes32 hex string
            hours: Number of hours of history to fetch
        """
        # deployment_id is already bytes32, use as topic3
        topics = [ALLOCATION_RESIZED_TOPIC, None, None, deployment_hex_id]
        return self._fetch_resizes(topics, hours)

    def get_resizes_by_indexer(self, indexer_id: str, hours: int = 48) -> List[Dict]:
        """Fetch AllocationResized events for a specific indexer.

        Args:
            indexer_id: The indexer address (0x-prefixed)
            hours: Number of hours of history to fetch
        """
        indexer_topic = pad_address(indexer_id)
        topics = [ALLOCATION_RESIZED_TOPIC, indexer_topic]
        return self._fetch_resizes(topics, hours)

