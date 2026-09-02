#!/usr/bin/env python3
"""Reconcile grtinfo's indexing-rewards formula against the chain.

The chain is the source of truth; grtinfo is the reconciliation target. This script
checks the two assumptions behind indexerinfo's "Instant APR":

1. rewards(alloc) per L1 block = allocatedIssuancePerBlock
                                 * signal(deployment) / curationBalance
                                 * allocation / staked(deployment)
   compared with RewardsManager.getRewards() accrual over a block window
   (ratio must be 1.000 - there is no protocol cut on indexing rewards).
2. sum(subgraphDeployments.signalledTokens) == GRT.balanceOf(Curation)
   (the subgraph signal must match the RewardsManager's dilution denominator).

Usage:
    python3 scripts/reconcile_rewards.py [--indexer 0x...] [--hours 3] [--top 3] [--tolerance 0.001]

Exit code 1 if any check deviates by more than the tolerance.
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import get_network_subgraph_url, get_rpc_url, get_my_indexer_id  # noqa: E402
from contracts import (  # noqa: E402
    REWARDS_MANAGER, SUBGRAPH_SERVICE, RewardsManagerClient, ContractCallClient,
)

GET_REWARDS_SELECTOR = "0x779bcb9b"  # getRewards(address,address)
ARBITRUM_BLOCKS_PER_HOUR = 4 * 3600


class Reconciler(ContractCallClient):
    def rpc(self, method, params):
        r = requests.post(self.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                          timeout=30).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"]

    def l1_block(self, l2_block: int) -> int:
        return int(self.rpc("eth_getBlockByNumber", [hex(l2_block), False])["l1BlockNumber"], 16)

    def get_rewards_at(self, allocation_id: str, block: int) -> int:
        data = GET_REWARDS_SELECTOR + self._encode_address(SUBGRAPH_SERVICE) + self._encode_address(allocation_id)
        return self._decode_uint256(self._eth_call(REWARDS_MANAGER, data, hex(block)))


def graphql(url, query):
    r = requests.post(url, json={"query": query}, timeout=60).json()
    if "errors" in r:
        raise RuntimeError(r["errors"])
    return r["data"]


def sum_signal(url) -> int:
    total, last_id = 0, ""
    while True:
        data = graphql(url, '{ subgraphDeployments(first: 1000, orderBy: id, orderDirection: asc, '
                            'where: { id_gt: "%s", signalledTokens_gt: 0 }) { id signalledTokens } }' % last_id)
        batch = data["subgraphDeployments"]
        if not batch:
            break
        total += sum(int(d["signalledTokens"]) for d in batch)
        last_id = batch[-1]["id"]
        if len(batch) < 1000:
            break
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--indexer", default=None, help="indexer address (default: my_indexer_id from config)")
    ap.add_argument("--hours", type=float, default=3, help="accrual window in hours (default 3)")
    ap.add_argument("--top", type=int, default=3, help="number of largest allocations to check")
    ap.add_argument("--tolerance", type=float, default=0.001, help="max relative deviation (default 0.1%%)")
    args = ap.parse_args()

    rpc_url = get_rpc_url()
    subgraph_url = get_network_subgraph_url()
    indexer = (args.indexer or get_my_indexer_id() or "").lower()
    if not rpc_url or not indexer:
        print("rpc_url and an indexer address are required (config or --indexer)", file=sys.stderr)
        return 2

    rec = Reconciler(rpc_url)
    rm = RewardsManagerClient(rpc_url)
    ok = True

    # --- Check 2: subgraph signal vs curation balance -------------------------
    curation_balance = rm.get_curation_balance()
    signal_sum = sum_signal(subgraph_url)
    dev = abs(signal_sum - curation_balance) / curation_balance
    flag = "OK " if dev <= args.tolerance else "FAIL"
    ok &= dev <= args.tolerance
    print(f"[{flag}] signal: subgraph sum {signal_sum / 1e18:,.2f} GRT vs curation balance "
          f"{curation_balance / 1e18:,.2f} GRT (dev {dev * 100:.4f}%)")

    # --- Check 1: per-allocation accrual --------------------------------------
    issuance = rm.get_issuance_per_block()
    if not issuance:
        print("could not read issuance from the RewardsManager", file=sys.stderr)
        return 2
    issuance_per_block = issuance["allocated"]
    min_signal = rm.get_minimum_subgraph_signal() or 0

    latest = int(rec.rpc("eth_blockNumber", []), 16)
    b2 = latest - 50
    b1 = b2 - int(args.hours * ARBITRUM_BLOCKS_PER_HOUR)
    l1_delta = rec.l1_block(b2) - rec.l1_block(b1)
    print(f"window: L2 blocks {b1}..{b2}, {l1_delta} L1 blocks, issuance {issuance_per_block / 1e18:.3f} GRT/L1 block"
          + (f" ({issuance['allocated'] / issuance['total'] * 100:.1f}% of {issuance['total'] / 1e18:.3f})"
             if issuance["total"] else ""))

    allocs = graphql(subgraph_url, '{ allocations(first: %d, orderBy: allocatedTokens, orderDirection: desc, '
                                   'where: { indexer: "%s", status: Active }) { id allocatedTokens createdAt '
                                   'subgraphDeployment { id signalledTokens stakedTokens deniedAt } } }'
                     % (args.top, indexer))["allocations"]
    if not allocs:
        print(f"no active allocations for {indexer}", file=sys.stderr)
        return 2

    for a in allocs:
        dep = a["subgraphDeployment"]
        alloc, sig, staked = int(a["allocatedTokens"]), int(dep["signalledTokens"]), int(dep["stakedTokens"])
        claimable = int(dep.get("deniedAt") or 0) == 0 and sig >= min_signal
        on_chain = rec.get_rewards_at(a["id"], b2) - rec.get_rewards_at(a["id"], b1)
        expected = (issuance_per_block * l1_delta * sig / curation_balance * alloc / staked) if (claimable and staked) else 0
        if expected == 0:
            passed = on_chain == 0
            ratio_str = "expected 0 (denied / below min signal)"
        else:
            ratio = on_chain / expected
            passed = abs(ratio - 1) <= args.tolerance
            ratio_str = f"ratio {ratio:.4f}"
        ok &= passed
        print(f"[{'OK ' if passed else 'FAIL'}] {a['id'][:10]} {alloc / 1e18:>13,.0f} GRT | "
              f"on-chain {on_chain / 1e18:>10,.4f} | formula {expected / 1e18:>10,.4f} | {ratio_str}")

    print("RECONCILED" if ok else "DEVIATION DETECTED: fix grtinfo, not the chain")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
