#!/usr/bin/env python3
"""
indexerinfo - Display indexer information from The Graph Network

Usage:
    indexerinfo <search_term>
    
Search by:
    - Partial ENS name (e.g., "ellipfra", "pinax")
    - Partial address (e.g., "0xf92f", "f92f430")
    - Partial URL (e.g., "staked.cloud")

Configuration:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.grtinfo/config.json (key "network_subgraph_url")
"""

import sys
import json
import argparse
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
from pathlib import Path

try:
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

# Import shared modules
from common import (
    Colors, terminal_link, format_deployment_link,
    format_tokens, format_tokens_short, format_percentage,
    format_timestamp, format_duration, print_section, allocation_anchor
)
from config import get_network_subgraph_url, get_ens_subgraph_url, get_rpc_url
from contracts import HorizonStakingClient, RewardsManagerClient, AllocationResizeClient
from ens_client import ENSClient
from sync_status import IndexerStatusClient, format_sync_status as _format_sync_status
from logger import setup_logging, get_logger
from rewards import get_rewards_batch, calculate_reward_split

log = get_logger(__name__)


def get_subgraph_id_from_deployment(deployment: Dict) -> Optional[str]:
    """Extract subgraph ID from deployment data"""
    versions = deployment.get('versions', [])
    if versions:
        return versions[0].get('subgraph', {}).get('id')
    return None


class TheGraphClient:
    """Client to query The Graph Network subgraph"""
    
    def __init__(self, network_subgraph_url: str):
        self.network_subgraph_url = network_subgraph_url.rstrip('/')
        self._session = requests.Session()
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query"""
        try:
            response = self._session.post(
                self.network_subgraph_url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                return {}
            return data.get('data', {})
        except Exception as e:
            print(f"{Colors.RED}Query error: {e}{Colors.RESET}", file=sys.stderr)
            return {}
    
    def search_indexers(self, search_term: str) -> List[Dict]:
        """Search for indexers by partial name, address or URL"""
        results = []
        search_lower = search_term.lower()
        
        # If it looks like an address (starts with 0x or is hex)
        if search_lower.startswith('0x') or all(c in '0123456789abcdef' for c in search_lower):
            # Search by address prefix using range query
            addr_search = search_lower if search_lower.startswith('0x') else f"0x{search_lower}"
            # Pad to create a range: 0x8bbe -> 0x8bbe0000... to 0x8bbeffff...
            addr_min = addr_search.ljust(42, '0')
            addr_max = addr_search.ljust(42, 'f')
            
            query = f"""
            {{
                indexers(
                    where: {{ id_gte: "{addr_min}", id_lte: "{addr_max}" }}
                    first: 10
                    orderBy: stakedTokens
                    orderDirection: desc
                ) {{
                    id
                    url
                    stakedTokens
                    delegatedTokens
                    allocatedTokens
                    indexingRewardCut
                    queryFeeCut
                    indexingRewardEffectiveCut
                    queryFeeEffectiveCut
                    delegatorShares
                    allocationCount
                }}
            }}
            """
            result = self.query(query)
            results.extend(result.get('indexers', []))
        
        # Search by URL containing the term
        if not results:
            query = """
            query SearchByUrl($search: String!) {
                indexers(
                    where: { url_contains: $search }
                    first: 10
                    orderBy: stakedTokens
                    orderDirection: desc
                ) {
                    id
                    url
                    stakedTokens
                    delegatedTokens
                    allocatedTokens
                    indexingRewardCut
                    queryFeeCut
                    indexingRewardEffectiveCut
                    queryFeeEffectiveCut
                    delegatorShares
                    allocationCount
                }
            }
            """
            result = self.query(query, {'search': search_lower})
            results.extend(result.get('indexers', []))
        
        return results
    
    def get_indexer_details(self, indexer_id: str) -> Optional[Dict]:
        """Get detailed information about an indexer"""
        query = """
        query GetIndexer($id: String!) {
            indexer(id: $id) {
                id
                url
                stakedTokens
                delegatedTokens
                delegatedCapacity
                delegatedThawingTokens
                allocatedTokens
                availableStake
                tokenCapacity
                lockedTokens
                unstakedTokens
                indexingRewardCut
                queryFeeCut
                indexingRewardEffectiveCut
                queryFeeEffectiveCut
                delegatorShares
                delegatorIndexingRewards
                delegatorQueryFees
                delegationExchangeRate
                allocationCount
                totalAllocationCount
                createdAt
            }
        }
        """
        result = self.query(query, {'id': indexer_id.lower()})
        return result.get('indexer')

    def get_indexer_operators(self, indexer_id: str) -> List[Dict]:
        """Get operators authorized to act on behalf of the indexer"""
        query = """
        query GetOperators($id: String!) {
            graphAccount(id: $id) {
                operators {
                    id
                }
            }
        }
        """
        result = self.query(query, {'id': indexer_id.lower()})
        account = result.get('graphAccount') or {}
        return account.get('operators', [])
    
    def get_indexer_allocations(self, indexer_id: str, hours: int = 48) -> Tuple[List[Dict], List[Dict]]:
        """Get active allocations and recent closed allocations for an indexer"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # Active allocations
        active_query = """
        query GetActiveAllocations($indexer: String!) {
            allocations(
                where: { indexer: $indexer, status: Active }
                orderBy: createdAt
                orderDirection: desc
                first: 100
            ) {
                id
                allocatedTokens
                createdAt
                status
                subgraphDeployment {
                    ipfsHash
                    signalledTokens
                    versions(first: 1, orderBy: createdAt, orderDirection: desc) {
                        subgraph { id }
                    }
                }
            }
        }
        """
        active_result = self.query(active_query, {'indexer': indexer_id.lower()})
        active = active_result.get('allocations', [])
        
        # Recent closed allocations (include isLegacy field)
        closed_query = f"""
        {{
            allocations(
                where: {{ indexer: "{indexer_id.lower()}", status: Closed, closedAt_gte: {cutoff_time} }}
                orderBy: closedAt
                orderDirection: desc
                first: 100
            ) {{
                id
                allocatedTokens
                createdAt
                closedAt
                status
                indexingRewards
                isLegacy
                subgraphDeployment {{
                    ipfsHash
                    signalledTokens
                    versions(first: 1, orderBy: createdAt, orderDirection: desc) {{
                        subgraph {{ id }}
                    }}
                }}
            }}
        }}
        """
        closed_result = self.query(closed_query)
        closed = closed_result.get('allocations', [])
        
        return active, closed
    
    def get_indexer_poi_submissions(self, indexer_id: str, hours: int = 48) -> List[Dict]:
        """Get POI submissions (reward collections) for an indexer"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        query = f"""
        {{
            poiSubmissions(
                where: {{ 
                    allocation_: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    presentedAtTimestamp_gte: {cutoff_time}
                }}
                orderBy: presentedAtTimestamp
                orderDirection: desc
                first: 100
            ) {{
                id
                presentedAtTimestamp
                allocation {{
                    id
                    status
                    allocatedTokens
                    indexingRewards
                    subgraphDeployment {{
                        ipfsHash
                        versions(first: 1, orderBy: createdAt, orderDirection: desc) {{
                            subgraph {{ id }}
                        }}
                    }}
                }}
            }}
        }}
        """
        result = self.query(query)
        return result.get('poiSubmissions', [])
    
    def get_top_allocations(self, indexer_id: str, limit: int = 10) -> List[Dict]:
        """Get top allocations by size for an indexer"""
        query = f"""
        {{
            allocations(
                where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                orderBy: allocatedTokens
                orderDirection: desc
                first: {limit}
            ) {{
                id
                allocatedTokens
                createdAt
                status
                isLegacy
                poiCount
                latestPoiPresentedAt
                subgraphDeployment {{
                    ipfsHash
                    signalledTokens
                    versions(first: 1, orderBy: createdAt, orderDirection: desc) {{
                        subgraph {{ id }}
                    }}
                }}
            }}
        }}
        """
        result = self.query(query)
        return result.get('allocations', [])
    
    def get_network_stats(self) -> Dict:
        """Get network-wide statistics for APR calculation"""
        query = """
        {
            graphNetwork(id: "1") {
                totalTokensAllocated
                totalTokensSignalled
                networkGRTIssuancePerBlock
            }
        }
        """
        result = self.query(query)
        return result.get('graphNetwork', {})

    def get_total_signalled_tokens(self) -> int:
        """Sum signalledTokens over all subgraph deployments (in wei)

        graphNetwork.totalTokensSignalled under-counts: it ignores curator query fees
        deposited into curation pools by Curation.collect(). The RewardsManager dilutes
        issuance over the curation contract's whole GRT balance, which matches the sum
        of every deployment's signalledTokens. Returns 0 if the query fails.
        """
        total = 0
        last_id = ""
        batch_size = 1000

        while True:
            query = f"""
            {{
                subgraphDeployments(
                    where: {{ id_gt: "{last_id}", signalledTokens_gt: 0 }}
                    orderBy: id
                    orderDirection: asc
                    first: {batch_size}
                ) {{
                    id
                    signalledTokens
                }}
            }}
            """
            result = self.query(query)
            batch = result.get('subgraphDeployments', [])
            if not batch:
                break
            for d in batch:
                total += int(d.get('signalledTokens', '0'))
            last_id = batch[-1]['id']
            if len(batch) < batch_size:
                break

        return total

    def get_all_active_allocations(self, indexer_id: str) -> List[Dict]:
        """Get all active allocations with signal data for APR calculation"""
        all_allocations = []
        skip = 0
        batch_size = 1000

        while True:
            query = f"""
            {{
                allocations(
                    where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    first: {batch_size}
                    skip: {skip}
                ) {{
                    allocatedTokens
                    subgraphDeployment {{
                        signalledTokens
                        stakedTokens
                        deniedAt
                    }}
                }}
            }}
            """
            result = self.query(query)
            batch = result.get('allocations', [])
            if not batch:
                break
            all_allocations.extend(batch)
            if len(batch) < batch_size:
                break
            skip += batch_size

        return all_allocations
    
    def get_all_active_allocation_ids(self, indexer_id: str) -> List[str]:
        """Get all active allocation IDs for an indexer"""
        all_ids = []
        skip = 0
        batch_size = 1000

        while True:
            query = f"""
            {{
                allocations(
                    where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    first: {batch_size}
                    skip: {skip}
                ) {{
                    id
                }}
            }}
            """
            result = self.query(query)
            batch = result.get('allocations', [])
            if not batch:
                break
            all_ids.extend([a['id'] for a in batch if a.get('id')])
            if len(batch) < batch_size:
                break
            skip += batch_size

        return all_ids
    
    def get_all_active_allocations_with_created(self, indexer_id: str) -> List[Dict]:
        """Get all active allocations with their IDs and creation timestamps"""
        all_allocations = []
        skip = 0
        batch_size = 1000

        while True:
            query = f"""
            {{
                allocations(
                    where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    first: {batch_size}
                    skip: {skip}
                ) {{
                    id
                    createdAt
                    allocatedTokens
                    isLegacy
                    poiCount
                    latestPoiPresentedAt
                }}
            }}
            """
            result = self.query(query)
            batch = result.get('allocations', [])
            if not batch:
                break
            all_allocations.extend(batch)
            if len(batch) < batch_size:
                break
            skip += batch_size

        return all_allocations
    
    def get_delegation_events(self, indexer_id: str, hours: int = 48) -> Tuple[List[Dict], List[Dict]]:
        """Get recent delegation/undelegation events for an indexer"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # Get recent delegations (based on lastDelegatedAt)
        delegation_query = f"""
        {{
            delegatedStakes(
                where: {{ indexer: "{indexer_id.lower()}", lastDelegatedAt_gte: {cutoff_time} }}
                orderBy: lastDelegatedAt
                orderDirection: desc
                first: 100
            ) {{
                id
                delegator {{ id }}
                stakedTokens
                shareAmount
                createdAt
                lastDelegatedAt
            }}
        }}
        """
        delegations = self.query(delegation_query).get('delegatedStakes', [])
        
        # Get recent undelegations (based on lastUndelegatedAt)
        # lockedTokens = amount in thawing period after undelegation
        undelegation_query = f"""
        {{
            delegatedStakes(
                where: {{ indexer: "{indexer_id.lower()}", lastUndelegatedAt_gte: {cutoff_time} }}
                orderBy: lastUndelegatedAt
                orderDirection: desc
                first: 100
            ) {{
                id
                delegator {{ id }}
                stakedTokens
                lockedTokens
                shareAmount
                lastUndelegatedAt
            }}
        }}
        """
        undelegations = self.query(undelegation_query).get('delegatedStakes', [])
        
        return delegations, undelegations


class LegacyRewardsClient:
    """Client to fetch legacy allocation rewards from on-chain events"""
    
    # HorizonRewardAssigned event signature
    # event HorizonRewardAssigned(address indexed indexer, address indexed allocationID, uint256 amount)
    HORIZON_REWARD_TOPIC = "0xa111914d7f2ea8beca61d12f1a1f38c5533de5f1823c3936422df4404ac2ec68"
    # RewardsManager contract on Arbitrum One
    REWARDS_MANAGER = "0x971B9d3d0Ae3ECa029CAB5eA1fB0F72c85e6a525"
    
    def __init__(self, rpc_url: str):
        if not HAS_WEB3:
            raise ImportError("web3 library is required for legacy rewards fetching")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    def get_rewards_for_allocation(self, allocation_id: str, from_block: int, to_block: int) -> int:
        """Get total rewards for a specific allocation from HorizonRewardAssigned events"""
        try:
            # Pad allocation ID to 32 bytes for topic filter
            alloc_topic = "0x" + allocation_id.lower()[2:].zfill(64)
            
            logs = self.w3.eth.get_logs({
                "address": self.REWARDS_MANAGER,
                "topics": [
                    self.HORIZON_REWARD_TOPIC,
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
        except Exception as e:
            return 0
    
    def get_rewards_for_allocations(self, allocations: List[Dict], indexer_id: str) -> Dict[str, int]:
        """Get rewards for multiple allocations efficiently using batch requests"""
        if not allocations:
            return {}
        
        rewards_map = {}
        
        # Get current block for the "to" block
        try:
            current_block = self.w3.eth.block_number
        except:
            return rewards_map
        
        # Pad indexer ID for topic filter
        indexer_topic = "0x" + indexer_id.lower()[2:].zfill(64)
        
        # Find the earliest creation block among allocations
        earliest_created = min(int(a.get('createdAt', 0)) for a in allocations)
        # Convert timestamp to approximate block (Arbitrum: ~0.25s per block)
        # Go back a bit further to be safe
        from_block = max(0, current_block - int((time.time() - earliest_created) / 0.25) - 10000)
        
        try:
            # Get all HorizonRewardAssigned events for this indexer
            logs = self.w3.eth.get_logs({
                "address": self.REWARDS_MANAGER,
                "topics": [
                    self.HORIZON_REWARD_TOPIC,
                    indexer_topic  # indexer
                ],
                "fromBlock": from_block,
                "toBlock": current_block
            })
            
            # Parse logs and map to allocations
            for log in logs:
                allocation_id = "0x" + log.topics[2].hex()[-40:]
                amount = int(log.data.hex(), 16)
                
                if allocation_id not in rewards_map:
                    rewards_map[allocation_id] = 0
                rewards_map[allocation_id] += amount
            
        except Exception as e:
            pass
        
        return rewards_map


def format_sync_status(status: Optional[Dict]) -> str:
    """Format sync status as a colored indicator (wrapper using local Colors)"""
    return _format_sync_status(status, Colors)


def main():
    parser = argparse.ArgumentParser(
        description='Display indexer information from The Graph Network',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    indexerinfo ellipfra           # Search by partial ENS name
    indexerinfo 0xf92f             # Search by address prefix
    indexerinfo staked.cloud       # Search by URL
        """
    )
    parser.add_argument('search_term', help='Search term (ENS name, address, or URL)')
    parser.add_argument('--hours', type=int, default=48, help='Hours of history to show (default: 48)')
    parser.add_argument(
        '-r', '--rewards',
        action='store_true',
        help='Calculate total accrued rewards from all allocations (requires RPC)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase verbosity (use -v for info, -vv for debug)'
    )
    
    args = parser.parse_args()
    
    # Setup logging based on verbosity
    setup_logging(verbosity=args.verbose)
    
    network_url = get_network_subgraph_url()
    if not network_url:
        print("Error: Network subgraph URL not configured.", file=sys.stderr)
        sys.exit(1)
    
    ens_url = get_ens_subgraph_url()
    
    client = TheGraphClient(network_url)
    ens_client = ENSClient(ens_url) if ens_url else None
    
    # Search for indexers
    indexers = []
    
    # First try ENS search if it doesn't look like an address
    search_term = args.search_term
    if ens_client and not search_term.startswith('0x') and not all(c in '0123456789abcdef' for c in search_term.lower()):
        ens_results = ens_client.search_by_ens(search_term)
        seen_addrs = {}
        for domain in ens_results:
            resolved = domain.get('resolvedAddress', {})
            if resolved:
                addr = resolved.get('id')
                if addr:
                    ens_name = domain.get('name')
                    if addr in seen_addrs:
                        # Append ENS name to existing entry
                        existing = seen_addrs[addr]
                        existing.setdefault('ens_names', []).append(ens_name)
                    else:
                        # Check if this address is an indexer
                        indexer = client.get_indexer_details(addr)
                        if indexer:
                            indexer['ens_names'] = [ens_name] if ens_name else []
                            indexer['ens_name'] = ens_name
                            seen_addrs[addr] = indexer
                            indexers.append(indexer)
    
    # Then try direct search
    if not indexers:
        indexers = client.search_indexers(search_term)
    
    if not indexers:
        print(f"{Colors.RED}No indexer found matching '{search_term}'{Colors.RESET}")
        sys.exit(1)
    
    if len(indexers) > 1:
        print(f"{Colors.YELLOW}Multiple indexers found:{Colors.RESET}")
        for i, idx in enumerate(indexers[:10]):
            addr = idx.get('id', '')
            ens_names = idx.get('ens_names', [])
            if not ens_names:
                ens = (ens_client.resolve_address(addr) if ens_client else None)
                if ens:
                    ens_names = [ens]
            url = (idx.get('url') or '')[:40]
            stake = format_tokens_short(idx.get('stakedTokens', '0'))
            name_display = f"{', '.join(ens_names)} " if ens_names else ""
            print(f"  {i+1}. {name_display}({addr[:10]}...) - {stake} GRT - {url}")
        
        try:
            choice = input(f"\n{Colors.CYAN}Select indexer (1-{min(len(indexers), 10)}): {Colors.RESET}")
            idx = int(choice) - 1
            if 0 <= idx < len(indexers):
                indexer = indexers[idx]
            else:
                sys.exit(1)
        except (ValueError, EOFError):
            sys.exit(1)
    else:
        indexer = indexers[0]
    
    # Get full details if we only have partial info
    indexer_id = indexer.get('id')
    if not indexer.get('createdAt'):
        indexer = client.get_indexer_details(indexer_id) or indexer
    
    # Resolve ENS name
    ens_name = indexer.get('ens_name') or (ens_client.resolve_address(indexer_id) if ens_client else None)
    
    # Display header
    if ens_name:
        print(f"{Colors.BOLD}Indexer:{Colors.RESET} {Colors.BRIGHT_CYAN}{ens_name}{Colors.RESET} ({indexer_id})")
    else:
        print(f"{Colors.BOLD}Indexer:{Colors.RESET} {Colors.CYAN}{indexer_id}{Colors.RESET}")
    
    if indexer.get('url'):
        print(f"{Colors.DIM}{indexer['url']}{Colors.RESET}")

    # Operators
    operators = client.get_indexer_operators(indexer_id)
    if operators:
        print_section("Operators")
        for op in operators:
            op_addr = op.get('id', '?')
            # Resolve ENS name for operator
            op_ens = ens_client.resolve_address(op_addr) if ens_client else None
            addr_link = terminal_link(f"https://arbiscan.io/address/{op_addr}", op_addr)
            if op_ens:
                print(f"  {Colors.BRIGHT_CYAN}{op_ens}{Colors.RESET} ({addr_link})")
            else:
                print(f"  {addr_link}")

    # Stake information
    print_section("Stake")
    self_stake = int(indexer.get('stakedTokens', '0'))
    delegated = int(indexer.get('delegatedTokens', '0'))
    delegated_capacity = int(indexer.get('delegatedCapacity', '0'))
    delegated_thawing = int(indexer.get('delegatedThawingTokens', '0'))
    allocated = int(indexer.get('allocatedTokens', '0'))
    available_stake = int(indexer.get('availableStake', '0'))
    token_capacity = int(indexer.get('tokenCapacity', '0'))

    # Workaround: fetch accurate tokenCapacity from contract
    # The subgraph's tokenCapacity can be stale due to delegationExchangeRate not being updated
    # See: https://github.com/graphprotocol/graph-network-subgraph/issues/323
    rpc_url = get_rpc_url()
    self_stake_thawing = 0
    # Active provisioned self-stake from the contract. This is the real basis for the
    # delegation cap, not stakedTokens (which ignores thawing and unprovisioned stake).
    provision_active = None
    delegation_ratio = 16  # protocol default; overridden by the contract when RPC is available
    if rpc_url:
        staking_client = HorizonStakingClient(rpc_url)
        contract_capacity = staking_client.get_tokens_available(indexer_id)
        if contract_capacity is not None and contract_capacity != token_capacity:
            log.debug(f"Using contract tokenCapacity ({contract_capacity}) instead of subgraph ({token_capacity})")
            token_capacity = contract_capacity
        provision = staking_client.get_provision(indexer_id)
        if provision:
            self_stake_thawing = provision.get('tokensThawing', 0)
            provision_active = provision.get('tokens', 0) - self_stake_thawing
        delegation_ratio = staking_client.get_delegation_ratio()

    # Delegations in thawing = delegated - delegatedCapacity
    delegations_thawing = delegated - delegated_capacity

    # token_capacity already includes thawing delegations (from contract or subgraph)
    # No need to add delegated_thawing again - that would be double-counting
    total_stake = token_capacity if token_capacity > 0 else (self_stake + delegated)
    # Calculate remaining directly (can be negative if over-allocated)
    remaining = total_stake - allocated
    remaining_pct = (remaining / total_stake * 100) if total_stake > 0 else 0
    
    # Delegation capacity: the protocol caps useful delegation at delegation_ratio x the
    # active provisioned self-stake (provision_active), NOT total stakedTokens. The contract's
    # getTokensAvailable() only counts min(active delegation, ratio x provision_active) toward
    # allocation capacity; delegation above that cap earns nothing. Fall back to stakedTokens
    # as an approximation when the contract is unavailable.
    delegation_basis = provision_active if provision_active is not None else self_stake
    max_delegation = delegation_basis * delegation_ratio
    # Compare against active delegation (delegated_capacity = delegated - thawing), which is
    # what the contract actually counts.
    delegation_remaining = max(0, max_delegation - delegated_capacity)
    delegation_overflow = max(0, delegated_capacity - max_delegation)
    delegation_used_pct = (delegated_capacity / max_delegation * 100) if max_delegation > 0 else 0
    
    # Show the ACTIVE self stake (provision minus thawing), mirroring the Delegated line
    # which already displays delegated_capacity. This keeps the lines additive:
    # active self stake + active delegation = Total (tokenCapacity).
    self_stake_active = provision_active if provision_active is not None else self_stake - self_stake_thawing
    self_stake_str = f"{Colors.BRIGHT_GREEN}{format_tokens(str(self_stake_active))}{Colors.RESET}"
    if self_stake_thawing > 0:
        self_stake_str += f" {Colors.DIM}({format_tokens(str(self_stake))} total, {format_tokens(str(self_stake_thawing))} thawing){Colors.RESET}"
    print(f"  Self stake:      {self_stake_str}")
    delegated_str = f"{Colors.BRIGHT_CYAN}{format_tokens(str(delegated_capacity))}{Colors.RESET} / {format_tokens(str(max_delegation))} ({delegation_used_pct:.0f}%)"
    if delegations_thawing > 0:
        delegated_str += f" {Colors.DIM}({format_tokens(str(delegations_thawing))} thawing){Colors.RESET}"
    print(f"  Delegated:       {delegated_str}")
    if delegation_remaining > 0:
        print(f"  Delegation room: {Colors.BRIGHT_GREEN}{format_tokens(str(delegation_remaining))}{Colors.RESET}")
    else:
        over_str = f" {Colors.DIM}({format_tokens(str(delegation_overflow))} over cap){Colors.RESET}" if delegation_overflow > 0 else ""
        print(f"  Delegation room: {Colors.BRIGHT_RED}FULL{Colors.RESET}{over_str}")
    total_str = f"{Colors.BOLD}Total:           {format_tokens(str(total_stake))}{Colors.RESET}"
    if self_stake_thawing > 0 or delegations_thawing > 0:
        total_str += f" {Colors.DIM}(allocation capacity, thawing excluded){Colors.RESET}"
    print(f"  {total_str}")
    print(f"  Allocated:       {format_tokens(str(allocated))}")
    if remaining < 0:
        # Over-allocated - show warning
        print(f"  Remaining:       {Colors.BRIGHT_RED}{format_tokens(str(remaining))} ({remaining_pct:.1f}%) ⚠ OVER-ALLOCATED{Colors.RESET}")
        if self_stake_thawing > 0 or delegations_thawing > 0:
            # Capacity drops as soon as a thaw is initiated, but existing allocations stay
            # open. SubgraphService then resizes each allocation to 0 on its next POI
            # (AllocationHandler._collectIndexingRewards -> _isOverAllocated) until the
            # tracker is back under capacity. Fix: addToProvision or close allocations first.
            print(f"  {Colors.DIM}(thawing reduced capacity below open allocations: each POI will resize its allocation to 0 until back under capacity){Colors.RESET}")
    else:
        remaining_color = Colors.BRIGHT_GREEN if remaining_pct < 10 else (Colors.BRIGHT_YELLOW if remaining_pct > 30 else Colors.DIM)
        print(f"  Remaining:       {remaining_color}{format_tokens(str(remaining))} ({remaining_pct:.1f}%){Colors.RESET}")
    
    # Reward cuts
    # Raw cut applies to total rewards, but effective cut on delegators is different
    # Formula: rawcut = 1 - (1 - effective) * delegated / (delegated + stake)
    # Solving for effective: effective = 1 - (1 - rawcut) * (delegated + stake) / delegated
    # Use raw delegated + self_stake (not token_capacity) for this calculation
    print_section("Reward Cuts")
    reward_cut_ppm = int(indexer.get('indexingRewardCut', 0))
    query_cut_ppm = int(indexer.get('queryFeeCut', 0))
    
    raw_reward_cut = reward_cut_ppm / 1_000_000  # Convert PPM to decimal
    raw_query_cut = query_cut_ppm / 1_000_000
    
    # Calculate effective cut on delegators using active delegation (excluding thawing)
    net_delegated = delegated - delegated_thawing
    raw_total = self_stake + net_delegated
    if net_delegated > 0:
        effective_reward_cut = 1 - (1 - raw_reward_cut) * raw_total / net_delegated
        effective_query_cut = 1 - (1 - raw_query_cut) * raw_total / net_delegated
    else:
        effective_reward_cut = raw_reward_cut
        effective_query_cut = raw_query_cut
    
    print(f"  Indexing rewards: {Colors.BRIGHT_CYAN}{raw_reward_cut*100:.1f}%{Colors.RESET} raw, {Colors.BRIGHT_YELLOW}{effective_reward_cut*100:.1f}%{Colors.RESET} effective on delegators")
    print(f"  Query fees:       {Colors.BRIGHT_CYAN}{raw_query_cut*100:.1f}%{Colors.RESET} raw, {Colors.BRIGHT_YELLOW}{effective_query_cut*100:.1f}%{Colors.RESET} effective on delegators")
    
    # APY via subgraph time-travel
    # Delegator: exchange rate (delegatedTokens - delegatedThawingTokens) / delegatorShares
    # Indexer: direct from indexerIndexingRewards + (queryFeesCollected - delegatorQueryFees)
    if rpc_url and delegated > 0:
        from subinfo import get_current_block_number
        current_block = get_current_block_number(rpc_url)

        if current_block is not None:
            blocks_per_day = 4 * 3600 * 24  # Arbitrum ~4 blocks/sec
            _apy_fragment = (
                "delegatedTokens delegatorShares delegatedThawingTokens "
                "indexerIndexingRewards queryFeesCollected delegatorQueryFees stakedTokens "
                "rewardsDestination"
            )

            def _sg_snapshot(block_num=None):
                block_clause = f', block: {{ number: {block_num} }}' if block_num else ''
                q = f'{{ indexer(id: "{indexer_id.lower()}"{block_clause}) {{ {_apy_fragment} }} }}'
                result = client.query(q)
                idx = result.get('indexer')
                if not idx:
                    return None
                snap = {k: float(idx.get(k, '0')) for k in [
                    'delegatedTokens', 'delegatorShares', 'delegatedThawingTokens',
                    'indexerIndexingRewards', 'queryFeesCollected', 'delegatorQueryFees', 'stakedTokens',
                ]}
                snap['rewardsDestination'] = idx.get('rewardsDestination')
                return snap

            snap_now = _sg_snapshot()
            if snap_now and snap_now['delegatorShares'] > 0:
                current_rate = (snap_now['delegatedTokens'] - snap_now['delegatedThawingTokens']) / snap_now['delegatorShares']

                # Indexer rewards compound only when rewardsDestination is unset (address(0))
                # When set, rewards are sent externally → use linear APR instead of compound APY
                rewards_dest = snap_now.get('rewardsDestination')
                idx_compounds = not rewards_dest or rewards_dest == '0x0000000000000000000000000000000000000000'

                na = f"{Colors.BOLD}{{}}d:{Colors.RESET} {Colors.DIM}N/A{Colors.RESET}"
                deleg_parts, deleg_qf_parts = [], []
                idx_parts, idx_qf_parts = [], []

                def _fmt_annualized(yld, days, compound, decimals=2):
                    if compound:
                        val = ((1 + yld) ** (365 / days) - 1) * 100
                    else:
                        val = yld * (365 / days) * 100
                    color = Colors.BRIGHT_GREEN if val >= 0 else Colors.BRIGHT_RED
                    return f"{Colors.BOLD}{days}d:{Colors.RESET} {color}{val:.{decimals}f}%{Colors.RESET}", val

                for days in [30, 60, 90]:
                    past_block = max(0, current_block - (days * blocks_per_day))
                    snap_past = _sg_snapshot(past_block)
                    if not snap_past or snap_past['delegatorShares'] == 0:
                        for lst in [deleg_parts, deleg_qf_parts, idx_parts, idx_qf_parts]:
                            lst.append(na.format(days))
                        continue

                    # Delegator total APY from exchange rate (exact, always compounds in pool)
                    past_rate = (snap_past['delegatedTokens'] - snap_past['delegatedThawingTokens']) / snap_past['delegatorShares']
                    if past_rate > 0:
                        d_apy = ((current_rate / past_rate) ** (365 / days) - 1) * 100
                        color = Colors.BRIGHT_GREEN if d_apy >= 0 else Colors.BRIGHT_RED
                        deleg_parts.append(f"{Colors.BOLD}{days}d:{Colors.RESET} {color}{d_apy:.2f}%{Colors.RESET}")
                    else:
                        deleg_parts.append(na.format(days))

                    # Delegator QF APY from cumulative counters (always compounds in pool)
                    net_deleg_past = snap_past['delegatedTokens'] - snap_past['delegatedThawingTokens']
                    if net_deleg_past > 0:
                        dqf_yield = (snap_now['delegatorQueryFees'] - snap_past['delegatorQueryFees']) / net_deleg_past
                        s, _ = _fmt_annualized(dqf_yield, days, compound=True)
                        deleg_qf_parts.append(s)
                    else:
                        deleg_qf_parts.append(na.format(days))

                    # Indexer: APY if compounding, APR if rewards sent externally
                    stake_past = snap_past['stakedTokens']
                    if stake_past > 0:
                        idx_indexing = snap_now['indexerIndexingRewards'] - snap_past['indexerIndexingRewards']
                        idx_query = (snap_now['queryFeesCollected'] - snap_past['queryFeesCollected']) - (snap_now['delegatorQueryFees'] - snap_past['delegatorQueryFees'])

                        s, _ = _fmt_annualized((idx_indexing + idx_query) / stake_past, days, compound=idx_compounds, decimals=1)
                        idx_parts.append(s)
                        s, _ = _fmt_annualized(idx_query / stake_past, days, compound=idx_compounds, decimals=1)
                        idx_qf_parts.append(s)
                    else:
                        idx_parts.append(na.format(days))
                        idx_qf_parts.append(na.format(days))

                if any('N/A' not in p for p in deleg_parts + idx_parts):
                    idx_label = "APY" if idx_compounds else "APR"
                    print_section(f"Historical (Delegators APY, Indexer {idx_label})")
                    print(f"  Delegators:    {' | '.join(deleg_parts)}")
                    if any('0.00%' not in p and 'N/A' not in p for p in deleg_qf_parts):
                        print(f"    of which QF: {' | '.join(deleg_qf_parts)}")
                    print(f"  Indexer:       {' | '.join(idx_parts)}")
                    if any('0.0%' not in p and 'N/A' not in p for p in idx_qf_parts):
                        print(f"    of which QF: {' | '.join(idx_qf_parts)}")

    # Estimated APR calculation (based on current allocations)
    print_section("Instant APR (current allocations)")
    network_stats = client.get_network_stats()
    all_allocations = client.get_all_active_allocations(indexer_id)
    
    if network_stats and all_allocations:
        # Network data
        # The subgraph's networkGRTIssuancePerBlock is the RewardsManager's raw (legacy)
        # issuancePerBlock. Since GIP-0086/0088 the RewardsManager only mints the share
        # the IssuanceAllocator assigns to it (getAllocatedIssuancePerBlock); the rest
        # goes to other targets (GIP-0089 Innovation Allocation). Read it on-chain so
        # the APR follows any governance change to the split.
        subgraph_issuance = int(network_stats.get('networkGRTIssuancePerBlock', '0'))
        issuance = RewardsManagerClient(rpc_url).get_issuance_per_block() if rpc_url else None
        if issuance:
            issuance_per_block = issuance['allocated'] / 1e18
            total_issuance = issuance['total'] / 1e18
            if total_issuance > 0 and issuance['allocated'] != issuance['total']:
                rewards_share = issuance['allocated'] / issuance['total'] * 100
                print(f"  Issuance:         {Colors.BRIGHT_CYAN}{issuance_per_block:,.3f} GRT/block{Colors.RESET} "
                      f"= {rewards_share:.1f}% of {total_issuance:,.3f} "
                      f"{Colors.DIM}({100 - rewards_share:.1f}% redirected by IssuanceAllocator){Colors.RESET}")
            else:
                print(f"  Issuance:         {Colors.BRIGHT_CYAN}{issuance_per_block:,.3f} GRT/block{Colors.RESET}")
            if subgraph_issuance and subgraph_issuance != issuance['raw']:
                log.debug(f"Subgraph issuance ({subgraph_issuance}) differs from on-chain raw ({issuance['raw']})")
        else:
            issuance_per_block = subgraph_issuance / 1e18
            print(f"  Issuance:         {issuance_per_block:,.3f} GRT/block "
                  f"{Colors.BRIGHT_YELLOW}(subgraph value: ignores the IssuanceAllocator split, APR may be overestimated){Colors.RESET}")
        # Sum the deployments' signal directly: graphNetwork.totalTokensSignalled misses the
        # curator query fees sitting in the curation pools, which do dilute the issuance.
        signalled_sum = client.get_total_signalled_tokens()
        if signalled_sum <= 0:
            signalled_sum = int(network_stats.get('totalTokensSignalled', '0'))
        total_signal_network = signalled_sum / 1e18

        # Ethereum blocks per year (~12s per block)
        eth_blocks_per_year = 2_628_000
        # 1% of every distribution is burned by GraphPayments.collect() in Horizon
        annual_issuance = issuance_per_block * eth_blocks_per_year * 0.99

        # Calculate expected rewards by summing each allocation's contribution
        # Formula: reward = annual_issuance × (signal_subgraph / total_signal_network) × (allocation / staked_on_subgraph)
        total_alloc = 0
        total_expected_rewards = 0
        
        for a in all_allocations:
            deployment = a.get('subgraphDeployment', {}) or {}
            alloc = int(a.get('allocatedTokens', '0')) / 1e18
            signal = int(deployment.get('signalledTokens', '0')) / 1e18
            staked = int(deployment.get('stakedTokens', '0')) / 1e18
            denied = int(deployment.get('deniedAt', '0') or 0) > 0

            total_alloc += alloc

            # Denied deployments mint no rewards, but their signal still dilutes the issuance
            if denied:
                continue

            if staked > 0 and total_signal_network > 0:
                # Subgraph's share of total rewards
                subgraph_share = signal / total_signal_network
                # Indexer's share of this subgraph's rewards
                indexer_share_of_subgraph = alloc / staked
                # Expected reward for this allocation
                alloc_reward = annual_issuance * subgraph_share * indexer_share_of_subgraph
                total_expected_rewards += alloc_reward
        
        # Convert stake values from wei to GRT for APR calculation
        # Self stake net of thawing: thawing tokens no longer count toward the provision
        self_stake_grt = (self_stake - self_stake_thawing) / 1e18
        net_delegated_grt = (delegated - delegated_thawing) / 1e18

        # Calculate APRs (use net delegated excluding thawing, since thawing tokens don't earn rewards)
        if total_expected_rewards > 0:
            indexer_rewards = total_expected_rewards * raw_reward_cut
            delegator_rewards = total_expected_rewards * (1 - raw_reward_cut)

            apr_indexer = (indexer_rewards / self_stake_grt) * 100 if self_stake_grt > 0 else 0
            apr_delegators = (delegator_rewards / net_delegated_grt) * 100 if net_delegated_grt > 0 else 0
            
            print(f"  Expected rewards: {Colors.BRIGHT_CYAN}{total_expected_rewards:,.0f} GRT/year{Colors.RESET}")
            print(f"  Indexer share ({raw_reward_cut*100:.1f}%): {indexer_rewards:,.0f} GRT/year")
            print(f"  Delegator share ({(1-raw_reward_cut)*100:.1f}%): {delegator_rewards:,.0f} GRT/year")
            print(f"  APR Indexer:    {Colors.BRIGHT_GREEN}{apr_indexer:.1f}%{Colors.RESET}")
            print(f"  APR Delegators: {Colors.BRIGHT_GREEN}{apr_delegators:.2f}%{Colors.RESET}")
        else:
            print(f"  {Colors.DIM}Unable to calculate APR{Colors.RESET}")
    else:
        print(f"  {Colors.DIM}Unable to fetch network data{Colors.RESET}")
    
    # Accrued rewards (on-chain) - only if --rewards flag is set
    if args.rewards:
        rpc_url = get_rpc_url()
        if not rpc_url:
            print_section("Accrued Rewards")
            print(f"  {Colors.DIM}RPC URL not configured. Set RPC_URL or add rpc_url to config.{Colors.RESET}")
        elif not HAS_WEB3:
            print_section("Accrued Rewards")
            print(f"  {Colors.DIM}web3 library not installed. Run: pip install web3{Colors.RESET}")
        else:
            # Get allocations with creation timestamps
            allocations_with_created = client.get_all_active_allocations_with_created(indexer_id)
            if allocations_with_created:
                allocation_ids = [a['id'] for a in allocations_with_created if a.get('id')]
                
                print_section(f"Accrued Rewards ({len(allocation_ids)} allocations)")
                print(f"  {Colors.DIM}Fetching rewards from smart contract...{Colors.RESET}", end='', flush=True)
                
                rewards_map = get_rewards_batch(allocation_ids, rpc_url, max_workers=5)
                
                # Calculate totals
                total_rewards = sum(r for r in rewards_map.values() if r is not None and r > 0)
                successful = sum(1 for r in rewards_map.values() if r is not None)
                failed = len(allocation_ids) - successful
                
                # Clear the "Fetching..." line
                print(f"\r{' ' * 60}\r", end='')
                
                if total_rewards > 0:
                    split = calculate_reward_split(total_rewards, raw_reward_cut)
                    print(f"  Total accrued:     {Colors.BRIGHT_CYAN}{total_rewards:,.0f} GRT{Colors.RESET}")
                    print(f"  Indexer share:     {Colors.BRIGHT_GREEN}{split['indexer']:,.0f} GRT{Colors.RESET} ({raw_reward_cut*100:.1f}%)")
                    print(f"  Delegator share:   {Colors.DIM}{split['delegators']:,.0f} GRT{Colors.RESET} ({(1-raw_reward_cut)*100:.1f}%)")
                    if failed > 0:
                        print(f"  {Colors.DIM}⚠ {failed} allocations failed to fetch{Colors.RESET}")
                    
                    # Build histogram by days until the allocation goes stale.
                    # Since Horizon the deadline is anchored on the last POI
                    # presentation (maxPOIStaleness), not on the creation date;
                    # legacy allocations still expire maxAllocationEpochs after
                    # creation. See common.allocation_deadline.
                    from contracts import (
                        EPOCH_DURATION_SECONDS, MAX_ALLOCATION_EPOCHS,
                        MAX_POI_STALENESS_SECONDS,
                    )
                    from common import allocation_deadline
                    now = datetime.now().timestamp()
                    max_alloc_seconds = MAX_ALLOCATION_EPOCHS * EPOCH_DURATION_SECONDS
                    try:
                        max_poi_staleness = HorizonStakingClient(rpc_url).get_max_poi_staleness() if rpc_url else MAX_POI_STALENESS_SECONDS
                    except Exception:
                        max_poi_staleness = MAX_POI_STALENESS_SECONDS

                    # Group rewards by whole days remaining until stale
                    epoch_buckets = {}  # days_remaining -> (total_rewards, count)

                    for alloc in allocations_with_created:
                        alloc_id = alloc.get('id', '').lower()
                        created_at = int(alloc.get('createdAt', 0))
                        reward = rewards_map.get(alloc_id) or rewards_map.get(alloc_id.lower()) or 0

                        if created_at > 0 and reward and reward > 0:
                            deadline, _ = allocation_deadline(alloc, max_poi_staleness, max_alloc_seconds)
                            # Days remaining (can be negative if already stale)
                            days_remaining = int((deadline - now) // EPOCH_DURATION_SECONDS)

                            if days_remaining not in epoch_buckets:
                                epoch_buckets[days_remaining] = {'rewards': 0, 'count': 0}
                            epoch_buckets[days_remaining]['rewards'] += reward
                            epoch_buckets[days_remaining]['count'] += 1

                    # Display histogram
                    if epoch_buckets:
                        print(f"\n  {Colors.BOLD}Rewards by days until stale:{Colors.RESET}")
                        print(f"  {Colors.DIM}(Horizon: {max_poi_staleness // 86400}d since last POI; legacy: 28 epochs since creation){Colors.RESET}")
                        
                        max_reward = max(b['rewards'] for b in epoch_buckets.values()) if epoch_buckets else 0
                        bar_width = 30
                        
                        # Group epochs into buckets: exp, 1-6d, 7d, 8d, 9-14d, 15-21d, 22-28d
                        # Note: negative epochs means allocation is past max age (should have been closed)
                        # Use -9999 to catch all expired allocations regardless of how old
                        bucket_ranges = [(-9999, 0), (1, 6), (7, 7), (8, 8), (9, 14), (15, 21), (22, 28)]
                        bucket_labels = ["exp", "1-6d", "7d", "8d", "9-14d", "15-21d", "22-28d"]
                        
                        for (start, end), label_text in zip(bucket_ranges, bucket_labels):
                            # For expired+0d bucket, sum all epochs <= 0
                            if start < -100:
                                bucket_rewards = sum(v['rewards'] for k, v in epoch_buckets.items() if k <= 0)
                                bucket_count = sum(v['count'] for k, v in epoch_buckets.items() if k <= 0)
                            else:
                                bucket_rewards = sum(epoch_buckets.get(e, {}).get('rewards', 0) for e in range(start, end + 1))
                                bucket_count = sum(epoch_buckets.get(e, {}).get('count', 0) for e in range(start, end + 1))
                            
                            # Color based on urgency
                            if end <= 0:
                                color = Colors.BRIGHT_RED  # Expired or expiring today - CRITICAL
                                prefix = "⚠️ "  # emoji (2 visual cells) + space = 3 visual cells
                            elif end <= 7:
                                color = Colors.BRIGHT_YELLOW  # 1-7 days - soon
                                prefix = "⏰ "  # emoji (2 visual cells) + space = 3 visual cells
                            else:
                                color = Colors.BRIGHT_GREEN  # Safe
                                prefix = "   "  # 3 spaces to match emoji + space
                            
                            bar_len = min(bar_width, int((bucket_rewards / max_reward) * bar_width)) if max_reward > 0 and bucket_rewards > 0 else 0
                            bar = '█' * bar_len + '░' * (bar_width - bar_len)
                            
                            # Consistent formatting: prefix (3 cells) + label (6 chars right-aligned) + bar
                            if bucket_rewards > 0:
                                print(f"  {prefix}{label_text:>6} {color}{bar}{Colors.RESET} {bucket_rewards:>10,.0f} GRT ({bucket_count:>3})")
                            else:
                                print(f"  {prefix}{label_text:>6} {Colors.DIM}{bar}{Colors.RESET}          - GRT")
                else:
                    print(f"  {Colors.DIM}No accrued rewards found (all allocations may be newly opened){Colors.RESET}")
            else:
                print_section("Accrued Rewards")
                print(f"  {Colors.DIM}No active allocations found{Colors.RESET}")
    
    # Allocation stats
    print_section("Allocations")
    active_count = indexer.get('allocationCount', 0)
    total_count = indexer.get('totalAllocationCount', 0)
    print(f"  Active: {Colors.BRIGHT_GREEN}{active_count}{Colors.RESET} | Total: {total_count}")
    
    # Get allocation history
    active_allocs, closed_allocs = client.get_indexer_allocations(indexer_id, args.hours)
    poi_submissions = client.get_indexer_poi_submissions(indexer_id, args.hours)
    recent_delegations, recent_undelegations = client.get_delegation_events(indexer_id, args.hours)
    
    # Enrich legacy allocation rewards from on-chain events if RPC is available
    legacy_rewards_map = {}
    rpc_url = get_rpc_url()
    if rpc_url and HAS_WEB3:
        legacy_allocs = [a for a in closed_allocs if a.get('isLegacy') and int(a.get('indexingRewards', '0')) == 0]
        if legacy_allocs:
            try:
                legacy_client = LegacyRewardsClient(rpc_url)
                legacy_rewards_map = legacy_client.get_rewards_for_allocations(legacy_allocs, indexer_id)
            except Exception:
                pass

    # Fetch allocation resizes via RPC
    allocation_resizes = []
    if rpc_url:
        try:
            resize_client = AllocationResizeClient(rpc_url)
            allocation_resizes = resize_client.get_resizes_by_indexer(indexer_id, args.hours)
        except Exception:
            pass

    # Build timeline
    events = []
    
    # Recent allocations (created in the period)
    cutoff = datetime.now() - timedelta(hours=args.hours)
    cutoff_ts = int(cutoff.timestamp())
    for alloc in active_allocs:
        created_ts = int(alloc.get('createdAt', 0))
        if datetime.fromtimestamp(created_ts) >= cutoff:
            deployment = alloc.get('subgraphDeployment', {})
            events.append({
                'type': 'allocate',
                'timestamp': created_ts,
                'tokens': alloc.get('allocatedTokens', '0'),
                'subgraph': deployment.get('ipfsHash', '?'),
                'subgraph_id': get_subgraph_id_from_deployment(deployment)
            })
    
    # Closed allocations
    for alloc in closed_allocs:
        deployment = alloc.get('subgraphDeployment', {})
        # Use on-chain rewards for legacy allocations if available
        rewards = int(alloc.get('indexingRewards', '0'))
        alloc_id = alloc.get('id', '').lower()
        if alloc.get('isLegacy') and rewards == 0 and alloc_id in legacy_rewards_map:
            rewards = legacy_rewards_map[alloc_id]
        events.append({
            'type': 'unallocate',
            'timestamp': int(alloc.get('closedAt', 0)),
            'tokens': alloc.get('allocatedTokens', '0'),
            'rewards': rewards,
            'subgraph': deployment.get('ipfsHash', '?'),
            'subgraph_id': get_subgraph_id_from_deployment(deployment),
            'is_legacy': alloc.get('isLegacy', False)
        })
    
    # POI submissions (collections)
    for poi in poi_submissions:
        alloc = poi.get('allocation', {})
        if alloc.get('status') == 'Active':
            deployment = alloc.get('subgraphDeployment', {})
            events.append({
                'type': 'collect',
                'timestamp': int(poi.get('presentedAtTimestamp', 0)),
                'tokens': alloc.get('allocatedTokens', '0'),
                'rewards': int(alloc.get('indexingRewards', '0')),
                'subgraph': deployment.get('ipfsHash', '?'),
                'subgraph_id': get_subgraph_id_from_deployment(deployment)
            })
    
    # Current active delegation = shareAmount x delegationExchangeRate.
    # NOTE: DelegatedStake.stakedTokens is the cumulative lifetime amount delegated (deposits),
    # NOT the current balance, so it must not be used as the displayed delegation amount.
    exchange_rate = float(indexer.get('delegationExchangeRate') or 0)

    # Recent delegations
    for stake in recent_delegations:
        delegator_id = stake.get('delegator', {}).get('id', '?')
        shares = int(stake.get('shareAmount', '0') or 0)
        balance_tokens = int(shares * exchange_rate)  # current active delegation (wei)
        created_at = int(stake.get('createdAt') or 0)
        delegated_at = int(stake.get('lastDelegatedAt') or 0)
        # If createdAt is within the period, it's a new delegation (initial amount)
        # Otherwise it's an increase to existing delegation (total shown)
        is_new = created_at >= cutoff_ts
        events.append({
            'type': 'delegate',
            'timestamp': delegated_at,
            'tokens': balance_tokens,
            'delegator': delegator_id,
            'is_new': is_new
        })

    # Recent undelegations - use lockedTokens (amount in thawing)
    # Remaining = current ACTIVE delegation = shareAmount x delegationExchangeRate.
    for stake in recent_undelegations:
        delegator_id = stake.get('delegator', {}).get('id', '?')
        locked_tokens = stake.get('lockedTokens', '0')  # Amount being undelegated (thawing)
        shares = int(stake.get('shareAmount', '0') or 0)
        remaining_tokens = int(shares * exchange_rate)  # current active delegation (wei)
        undelegated_at = int(stake.get('lastUndelegatedAt') or 0)
        events.append({
            'type': 'undelegate',
            'timestamp': undelegated_at,
            'tokens': locked_tokens,  # Show the undelegated amount
            'remaining': remaining_tokens,  # Keep track of remaining
            'delegator': delegator_id
        })
    
    # Allocation resizes - map alloc_id to deployment info from all known allocations
    if allocation_resizes:
        alloc_deployment_map = {}
        for alloc in active_allocs + closed_allocs:
            aid = alloc.get('id', '').lower()
            dep = alloc.get('subgraphDeployment', {})
            alloc_deployment_map[aid] = dep
        for resize in allocation_resizes:
            dep = alloc_deployment_map.get(resize['alloc_id'].lower(), {})
            events.append({
                'type': 'resize',
                'timestamp': resize['timestamp'],
                'tokens': str(resize['new_tokens']),
                'old_tokens': str(resize['old_tokens']),
                'subgraph': dep.get('ipfsHash', '?'),
                'subgraph_id': get_subgraph_id_from_deployment(dep)
            })

    # Separate allocation events from delegation events
    allocation_events = [e for e in events if e['type'] in ('allocate', 'unallocate', 'collect', 'resize')]
    delegation_events = [e for e in events if e['type'] in ('delegate', 'undelegate')]

    if allocation_events:
        print_section(f"Allocation Activity ({args.hours}h)")
        allocation_events.sort(key=lambda x: x['timestamp'], reverse=True)

        for event in allocation_events[:20]:
            ts = format_timestamp(str(event['timestamp']))
            tokens = format_tokens_short(event['tokens'])

            if event['type'] == 'allocate':
                symbol = f"{Colors.BRIGHT_GREEN}+{Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                details = f"{tokens} GRT"
            elif event['type'] == 'unallocate':
                symbol = f"{Colors.BRIGHT_RED}-{Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                rewards = event.get('rewards', 0) / 1e18
                is_legacy = event.get('is_legacy', False)
                legacy_marker = f" {Colors.DIM}(legacy){Colors.RESET}" if is_legacy else ""
                rewards_str = f" → {rewards:,.0f} GRT{legacy_marker}" if rewards > 0 else ""
                details = f"{tokens} GRT{rewards_str}"
            elif event['type'] == 'collect':
                symbol = f"{Colors.BRIGHT_CYAN}${Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                rewards = event.get('rewards', 0) / 1e18
                details = f"{rewards:,.0f} GRT collected"
            elif event['type'] == 'resize':
                symbol = f"{Colors.BRIGHT_YELLOW}~{Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                old = float(event['old_tokens']) / 1e18
                new = float(event['tokens']) / 1e18
                diff = new - old
                sign = "+" if diff > 0 else ""
                details = f"{new:,.0f} GRT \u2190 {old:,.0f} ({sign}{diff:,.0f})"
            else:
                continue

            print(f"  [{symbol}] {Colors.DIM}{ts}{Colors.RESET}  {target}  {details}")

    if delegation_events:
        print_section(f"Delegation Activity ({args.hours}h)")
        delegation_events.sort(key=lambda x: x['timestamp'], reverse=True)

        for event in delegation_events[:15]:
            ts = format_timestamp(str(event['timestamp']))
            tokens = format_tokens_short(event['tokens'])

            if event['type'] == 'delegate':
                symbol = f"{Colors.BRIGHT_MAGENTA}↑{Colors.RESET}"
                target = event.get('delegator', '?')
                is_new = event.get('is_new', False)
                if is_new:
                    details = f"{Colors.BRIGHT_MAGENTA}+{tokens} GRT delegated{Colors.RESET}"
                else:
                    details = f"{Colors.BRIGHT_MAGENTA}now {tokens} GRT (increased){Colors.RESET}"
            elif event['type'] == 'undelegate':
                symbol = f"{Colors.YELLOW}↓{Colors.RESET}"
                target = event.get('delegator', '?')
                thawing_tokens = format_tokens_short(event['tokens'])
                remaining = int(event.get('remaining', '0')) / 1e18
                remaining_str = f"{remaining:,.0f}" if remaining >= 1 else "0"
                details = f"{Colors.YELLOW}{thawing_tokens} GRT thawing, {remaining_str} remaining{Colors.RESET}"
            else:
                continue

            print(f"  [{symbol}] {Colors.DIM}{ts}{Colors.RESET}  {target}  {details}")
    
    # Active allocations summary - use dedicated query for true top allocations
    top_allocs = client.get_top_allocations(indexer_id, 10)
    if top_allocs:
        # Get sync status from indexer's public status endpoint
        indexer_url = indexer.get('url')
        sync_statuses = {}
        status_error = None
        status_client = None
        
        if indexer_url:
            status_client = IndexerStatusClient(timeout=15)
            sync_statuses = status_client.get_all_deployments_status(indexer_url)
            if not sync_statuses and status_client.last_error:
                status_error = status_client.last_error
        else:
            status_error = "No indexer URL in network subgraph"
        
        print_section("Top Active Allocations")
        if status_error:
            print(f"  {Colors.DIM}⚠ Sync status unavailable: {status_error}{Colors.RESET}")
        for alloc in top_allocs:
            deployment = alloc.get('subgraphDeployment', {})
            subgraph_hash = deployment.get('ipfsHash', '?')
            subgraph_id = get_subgraph_id_from_deployment(deployment)
            subgraph = format_deployment_link(subgraph_hash, subgraph_id) if subgraph_hash != '?' else subgraph_hash
            tokens = format_tokens(alloc.get('allocatedTokens', '0'))
            signal = int(deployment.get('signalledTokens', '0')) / 1e18
            # Time since the staleness anchor: the last POI presentation, or
            # the creation date when no POI has been presented yet. The raw
            # allocation age is not what bounds rewards since Horizon.
            anchor_ts, anchor_kind = allocation_anchor(alloc)
            age = format_duration(int(datetime.now().timestamp()) - anchor_ts) if anchor_ts > 0 else '?'
            anchor_label = 'since POI' if anchor_kind == 'poi' else 'no POI'

            # Get sync status for this deployment
            sync_status = sync_statuses.get(subgraph_hash)
            sync_indicator = format_sync_status(sync_status) if sync_statuses else ""

            if sync_indicator:
                print(f"  {subgraph}  {tokens:>12}  {Colors.DIM}{age:>8} {anchor_label:<9}{Colors.RESET}  {sync_indicator}")
            else:
                print(f"  {subgraph}  {tokens:>12}  {Colors.DIM}{age:>8} {anchor_label:<9}  signal: {signal:,.0f}{Colors.RESET}")
    
    print()


if __name__ == '__main__':
    main()

