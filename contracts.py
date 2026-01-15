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
from typing import Optional


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

