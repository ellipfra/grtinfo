#!/usr/bin/env python3
"""
Rewards-related functions for grtinfo CLI tools

This module provides functions to query indexing rewards from
both on-chain contracts and The Graph Network subgraph.
"""

from typing import Dict, List, Optional
import requests

from contracts import (
    REWARDS_MANAGER, STAKING, SUBGRAPH_SERVICE,
    PPM_BASE, GRT_DECIMALS
)

# Check if web3 is available
try:
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False


def get_accrued_rewards(
    allocation_id: str,
    rpc_url: str = "https://arb1.arbitrum.io/rpc"
) -> Optional[float]:
    """Get accrued rewards for an allocation from the RewardsManager contract
    
    Calls getRewards(address _rewardsIssuer, address _allocationID) on RewardsManager.
    Tries both Staking (legacy) and SubgraphService (new Horizon) as rewards issuers.
    
    Args:
        allocation_id: The allocation ID (0x...)
        rpc_url: Arbitrum RPC endpoint URL
    
    Returns:
        Total accrued rewards in GRT, or None if failed/unavailable
    """
    if not HAS_WEB3:
        return None
    
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Function selector for getRewards(address,address)
        selector = Web3.keccak(text="getRewards(address,address)")[:4].hex()
        
        # Try both rewards issuers (legacy Staking and new SubgraphService)
        for issuer in [STAKING, SUBGRAPH_SERVICE]:
            try:
                calldata = selector + issuer[2:].lower().zfill(64) + allocation_id[2:].lower().zfill(64)
                result = w3.eth.call({
                    "to": Web3.to_checksum_address(REWARDS_MANAGER),
                    "data": f"0x{calldata}"
                })
                rewards_wei = int(result.hex(), 16)
                if rewards_wei > 0:
                    return rewards_wei / (10 ** GRT_DECIMALS)
            except:
                continue
        
        return 0.0
        
    except Exception:
        return None


def get_indexer_reward_cut(indexer_id: str, network_url: str) -> Optional[float]:
    """Get the indexer's reward cut from the network subgraph
    
    The reward cut is the percentage of indexing rewards kept by the indexer
    (vs distributed to delegators).
    
    Args:
        indexer_id: Indexer address (0x...)
        network_url: URL of The Graph Network subgraph
    
    Returns:
        Reward cut as decimal (e.g., 0.265 = 26.5%), or None if failed
    """
    try:
        url = network_url.rstrip('/')
        
        query = """
        query GetIndexer($id: String!) {
            indexer(id: $id) {
                indexingRewardCut
            }
        }
        """
        response = requests.post(
            url,
            json={'query': query, 'variables': {'id': indexer_id.lower()}},
            timeout=10
        )
        response.raise_for_status()
        data = response.json().get('data', {})
        indexer = data.get('indexer')
        
        if indexer:
            # indexingRewardCut is in PPM (parts per million)
            cut_ppm = int(indexer.get('indexingRewardCut', 0))
            return cut_ppm / PPM_BASE
        
        return None
        
    except Exception:
        return None


def calculate_reward_split(
    total_rewards: float,
    reward_cut: float
) -> Dict[str, float]:
    """Calculate how rewards are split between indexer and delegators
    
    Args:
        total_rewards: Total rewards in GRT
        reward_cut: Indexer's reward cut as decimal (0.0 to 1.0)
    
    Returns:
        Dict with 'indexer' and 'delegators' shares in GRT
    """
    indexer_share = total_rewards * reward_cut
    delegator_share = total_rewards * (1 - reward_cut)
    
    return {
        'indexer': indexer_share,
        'delegators': delegator_share,
        'total': total_rewards
    }


def get_legacy_rewards_from_events(
    allocation_id: str,
    indexer_id: str,
    from_block: int,
    to_block: int,
    rpc_url: str = "https://arb1.arbitrum.io/rpc"
) -> Optional[int]:
    """Get legacy allocation rewards from HorizonRewardAssigned events
    
    This is used for allocations closed before the Horizon upgrade
    where indexingRewards in the subgraph shows 0.
    
    Args:
        allocation_id: The allocation ID
        indexer_id: The indexer address
        from_block: Start block for event search
        to_block: End block for event search
        rpc_url: Arbitrum RPC endpoint URL
    
    Returns:
        Total rewards in wei, or None if failed
    """
    if not HAS_WEB3:
        return None
    
    try:
        from contracts import HORIZON_REWARD_ASSIGNED_TOPIC, pad_address
        
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Filter for HorizonRewardAssigned events for this allocation
        alloc_topic = pad_address(allocation_id)
        
        logs = w3.eth.get_logs({
            "address": REWARDS_MANAGER,
            "topics": [
                HORIZON_REWARD_ASSIGNED_TOPIC,
                None,  # indexer (any)
                alloc_topic  # allocation ID
            ],
            "fromBlock": from_block,
            "toBlock": to_block
        })
        
        total_rewards = 0
        for log in logs:
            # Data contains the amount (uint256)
            amount = int(log.data.hex(), 16)
            total_rewards += amount
        
        return total_rewards
        
    except Exception:
        return None


def get_rewards_batch(
    allocation_ids: List[str],
    rpc_url: str = "https://arb1.arbitrum.io/rpc",
    max_workers: int = 5
) -> Dict[str, Optional[float]]:
    """Get accrued rewards for multiple allocations in parallel
    
    Uses a shared Web3 instance to avoid opening too many connections.
    
    Args:
        allocation_ids: List of allocation IDs
        rpc_url: Arbitrum RPC endpoint URL
        max_workers: Maximum number of parallel requests (default 5 to avoid file descriptor limits)
    
    Returns:
        Dict mapping allocation_id to rewards (or None if failed)
    """
    if not HAS_WEB3:
        return {alloc_id: None for alloc_id in allocation_ids}
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    
    results = {}
    
    # Create a single shared Web3 instance with a session that handles connection pooling
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 30}))
    
    # Pre-compute the selector once
    selector = Web3.keccak(text="getRewards(address,address)")[:4].hex()
    
    # Thread-local storage not needed since Web3 HTTPProvider handles sessions internally
    # But we use a lock to avoid potential race conditions
    lock = threading.Lock()
    
    def fetch_rewards_with_shared_w3(alloc_id: str) -> tuple:
        """Fetch rewards using the shared Web3 instance"""
        try:
            for issuer in [STAKING, SUBGRAPH_SERVICE]:
                try:
                    calldata = selector + issuer[2:].lower().zfill(64) + alloc_id[2:].lower().zfill(64)
                    result = w3.eth.call({
                        "to": Web3.to_checksum_address(REWARDS_MANAGER),
                        "data": f"0x{calldata}"
                    })
                    rewards_wei = int(result.hex(), 16)
                    if rewards_wei > 0:
                        return (alloc_id, rewards_wei / (10 ** GRT_DECIMALS))
                except:
                    continue
            return (alloc_id, 0.0)
        except Exception:
            return (alloc_id, None)
    
    # Limit workers to avoid "too many open files" errors
    effective_workers = min(max_workers, 5)
    
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {
            executor.submit(fetch_rewards_with_shared_w3, alloc_id): alloc_id
            for alloc_id in allocation_ids
        }
        
        for future in as_completed(futures):
            try:
                alloc_id, rewards = future.result()
                results[alloc_id] = rewards
            except Exception:
                alloc_id = futures[future]
                results[alloc_id] = None
    
    return results

