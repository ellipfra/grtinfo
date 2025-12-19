#!/bin/bash
# Installation script for grtinfo

set -e

echo "Installing grtinfo..."

# Create config directory if needed
mkdir -p ~/.grtinfo

# Copy example config if config doesn't exist
if [ ! -f ~/.grtinfo/config.json ]; then
    cp config.example.json ~/.grtinfo/config.json
    echo "Configuration file created at ~/.grtinfo/config.json"
    echo "Please edit it with your TheGraph Network subgraph URL."
fi

# Install dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

# Install the package
echo "Installing package..."
pip3 install -e .

echo ""
echo "Installation complete!"
echo ""
echo "Usage:"
echo "  subinfo <subgraph_hash>      - Analyze subgraph allocations and signals"
echo "  indexerinfo <search_term>    - Display indexer information"
echo "  delegatorinfo <address>      - Display delegator portfolio"
echo ""
echo "Before using, configure your TheGraph Network subgraph URL:"
echo "  export THEGRAPH_NETWORK_SUBGRAPH_URL=\"https://your-graph-node/subgraphs/id/QmNetworkHash\""
echo "  or edit ~/.grtinfo/config.json"
