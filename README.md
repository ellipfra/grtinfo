# subinfo

CLI tool to analyze TheGraph allocations and curation signals for a given subgraph deployment.

## Features

- **Subgraph Metadata**: Network, reward proportion vs network average
- **Curation Signal**: Current signal amount, new deployment detection
- **Signal Changes**: Recent signal additions/removals, upgrade detection
- **Active Allocations**: Current indexer allocations with duration
- **Allocation Timeline**: Chronological view of allocations/unallocations with warnings for indexers with high unallocated stake
- **ENS Resolution**: Automatically resolves indexer addresses to ENS names

## Installation

```bash
# Clone the repository
git clone https://github.com/ellipfra/subinfo.git
cd subinfo

# Run the installation script
./install.sh

# Or install manually
pip3 install -r requirements.txt
pip3 install -e .
```

## Configuration

Before using subinfo, you need to configure your TheGraph Network subgraph URL.

### Option 1: Environment Variables

```bash
export THEGRAPH_NETWORK_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmNetworkSubgraphHash"
export MY_INDEXER_ID="0xYourIndexerAddress"  # Optional: for highlighting your allocations
export ENS_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmENSSubgraphHash"  # Optional
```

### Option 2: Configuration File

Edit `~/.subinfo/config.json`:

```json
{
  "network_subgraph_url": "https://your-graph-node/subgraphs/id/QmNetworkSubgraphHash",
  "my_indexer_id": "0xYourIndexerAddress",
  "ens_subgraph_url": "https://your-graph-node/subgraphs/id/QmENSSubgraphHash"
}
```

## Usage

```bash
subinfo <subgraph_hash>
```

### Options

- `--url URL`: Override the network subgraph URL
- `--hours N`: Number of hours for history (default: 48)

### Example

```bash
subinfo QmYourSubgraphHash
```

### Output

```
Subgraph: QmYourSubgraphHash
Network: https://your-graph-node/...

Subgraph Metadata
------------------------------------------------------------
Network: mainnet
Reward Proportion: 150.25%

Curation Signal
------------------------------------------------------------
Total signal: 10,000.00 GRT

Signal Changes (48h)
------------------------------------------------------------
  [+]  2025-12-12 08:26  0xabcd1234...         500.00 GRT

Active Allocations
------------------------------------------------------------
  ★    my-indexer.eth (0x1234..)     100,000.00 GRT  2025-12-11 12:00  Active (1d 2h)
       other-indexer.eth (0x5678..)   50,000.00 GRT  2025-12-10 08:00  Active (2d 6h)

Total: 150,000.00 GRT

Allocations/Unallocations Timeline (48h)
------------------------------------------------------------
  [+] ★  2025-12-11 12:00  my-indexer.eth (0x1234..)    100,000.00 GRT  Active
  [-]    2025-12-10 18:00  leaving-indexer (0x9abc..)    25,000.00 GRT  closed ⚠ 45% unallocated

Total allocated: 100,000.00 GRT | Total unallocated: 25,000.00 GRT
```

### Legend

- `★` Your indexer (configured via `MY_INDEXER_ID`)
- `[+]` Allocation created
- `[-]` Allocation closed (unallocation)
- `[↑]` Subgraph upgrade (signal transferred to new deployment)
- `⚠ XX% unallocated` Warning: indexer has high proportion of stake not allocated

## License

MIT License - see [LICENSE](LICENSE) file.

