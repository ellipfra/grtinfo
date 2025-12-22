#!/usr/bin/env python3
"""
Configuration management for grtinfo CLI tools

This module handles loading configuration from:
1. Environment variables (highest priority)
2. Config file at ~/.grtinfo/config.json
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional


CONFIG_FILE = Path.home() / '.grtinfo' / 'config.json'


def _load_config() -> dict:
    """Load config file, returns empty dict on error"""
    if not CONFIG_FILE.exists():
        return {}
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            content = f.read().strip()
            if content:
                return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"Error: Config file {CONFIG_FILE} is not valid JSON: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Unable to load config: {e}", file=sys.stderr)
    
    return {}


def get_network_subgraph_url(required: bool = True) -> Optional[str]:
    """Get network subgraph URL from environment variable or config
    
    Args:
        required: If True, exits with error if URL not found
    
    Returns:
        URL string or None if not required and not found
    """
    # Priority 1: Environment variable
    env_url = os.environ.get('THEGRAPH_NETWORK_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Priority 2: Config file
    config = _load_config()
    url = config.get('network_subgraph_url') or config.get('subgraph_url')
    if url:
        return url.rstrip('/')
    
    # Not found
    if required:
        print("Error: No TheGraph Network subgraph URL configured.", file=sys.stderr)
        print("Please set THEGRAPH_NETWORK_SUBGRAPH_URL environment variable", file=sys.stderr)
        print("or create ~/.grtinfo/config.json with 'network_subgraph_url' key.", file=sys.stderr)
        sys.exit(1)
    
    return None


def get_ens_subgraph_url() -> Optional[str]:
    """Get ENS subgraph URL from environment variable or config
    
    Returns:
        URL string or None if not configured
    """
    # Priority 1: Environment variable
    env_url = os.environ.get('ENS_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Priority 2: Config file
    config = _load_config()
    url = config.get('ens_subgraph_url')
    if url:
        return url.rstrip('/')
    
    # Priority 3: Try to build from network URL
    network_url = get_network_subgraph_url(required=False)
    if network_url and '/subgraphs/id/' in network_url:
        base_url = network_url.split('/subgraphs/id/')[0] + '/subgraphs/id'
        return f"{base_url}/QmcE8RpWtsiN5hkJKdfCXGfTDoTgPEjMbQwnjLPfThT7kZ"
    
    return None


def get_rpc_url() -> Optional[str]:
    """Get RPC URL from environment variable or config
    
    Returns:
        URL string or None if not configured
    """
    # Priority 1: Environment variable
    env_url = os.environ.get('RPC_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Priority 2: Config file
    config = _load_config()
    url = config.get('rpc_url')
    if url:
        return url.rstrip('/')
    
    return None


def get_my_indexer_id() -> Optional[str]:
    """Get user's indexer ID from environment variable or config
    
    Returns:
        Indexer address (lowercase) or None if not configured
    """
    # Priority 1: Environment variable
    env_indexer = os.environ.get('MY_INDEXER_ID')
    if env_indexer:
        return env_indexer.lower()
    
    # Priority 2: Config file
    config = _load_config()
    indexer_id = config.get('my_indexer_id')
    if indexer_id:
        return indexer_id.lower()
    
    return None


def get_analytics_subgraph_url() -> Optional[str]:
    """Get analytics subgraph URL from environment variable or config
    
    Returns:
        URL string or None if not configured
    """
    # Priority 1: Environment variable
    env_url = os.environ.get('ANALYTICS_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Priority 2: Config file
    config = _load_config()
    url = config.get('analytics_subgraph_url')
    if url:
        return url.rstrip('/')
    
    return None

