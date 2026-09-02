# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

grtinfo is a set of CLI tools for analyzing The Graph Network indexers, delegators, allocations, and curation signals. It queries The Graph Network subgraph via GraphQL and optionally interacts with Arbitrum smart contracts via web3.

## Commands

### Installation
```bash
pip install -r requirements.txt
pip install -e .
```

### Running the tools
```bash
# Analyze a subgraph deployment
subinfo <subgraph_ipfs_hash>

# Get indexer information (search by ENS, address, or URL)
indexerinfo <search_term>

# Get delegator portfolio
delegatorinfo <address_or_ens>
```

### Running tests
```bash
pytest                      # Run all tests
pytest tests/test_common.py # Run single test file
pytest -v                   # Verbose output
```

## Architecture

The codebase consists of three main CLI tools that share common infrastructure:

### CLI Tools (entry points in setup.py)
- `subinfo.py` - Analyzes subgraph deployments: allocations, signals, rewards, timeline
- `indexerinfo.py` - Displays indexer details: stake, allocations, APR, activity
- `delegatorinfo.py` - Shows delegator portfolio: stakes, profits, thawing status

### Shared Modules
- `config.py` - Configuration from env vars (`THEGRAPH_NETWORK_SUBGRAPH_URL`, etc.) or `~/.grtinfo/config.json`
- `common.py` - ANSI colors, terminal hyperlinks (OSC 8), token formatting utilities
- `graphql_client.py` - Base `GraphQLClient` class with session/error handling, extended by `NetworkSubgraphClient`
- `ens_client.py` - ENS name resolution with disk cache (`~/.grtinfo/ens_cache.json`)
- `rewards.py` - On-chain reward queries via web3 (calls RewardsManager contract)
- `contracts.py` - Arbitrum contract addresses and constants (REWARDS_MANAGER, STAKING, etc.)
- `sync_status.py` - `IndexerStatusClient` to query Graph Node `/status` endpoints
- `logger.py` - Colored console logging with configurable verbosity

### Data Flow
1. User runs CLI command with argument (subgraph hash, indexer search term, delegator address)
2. Tool loads config from env vars or `~/.grtinfo/config.json`
3. Queries The Graph Network subgraph via GraphQL
4. Optionally queries on-chain data via Arbitrum RPC (web3)
5. Resolves ENS names if configured
6. Formats and displays colored terminal output

### Configuration Priority
1. Environment variables (highest): `THEGRAPH_NETWORK_SUBGRAPH_URL`, `MY_INDEXER_ID`, `ENS_SUBGRAPH_URL`, `RPC_URL`
2. Config file: `~/.grtinfo/config.json`

### Indexing Rewards Issuance
Since the GIP-0086/0088 upgrade, the RewardsManager only mints the share of protocol issuance that the IssuanceAllocator assigns to it (`getAllocatedIssuancePerBlock()`); the rest goes to other targets (GIP-0089 Innovation Allocation, 20% since 2026-08-31). The subgraph's `networkGRTIssuancePerBlock` is the raw pre-split value. Always use `RewardsManagerClient.get_issuance_per_block()` from `contracts.py` for reward/APR projections, with the subgraph value only as a fallback.

### Token Amounts
All token amounts from the subgraph are in wei (18 decimals). Use `format_tokens()` from `common.py` for display.

### Terminal Output
Output uses ANSI colors via the `Colors` class. Clickable hyperlinks use OSC 8 escape sequences (can be disabled with `NO_HYPERLINKS=1`).
