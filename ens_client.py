#!/usr/bin/env python3
"""
ENS (Ethereum Name Service) client for grtinfo CLI tools

This module provides ENS name resolution with caching.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional
import requests


class ENSClient:
    """Client to resolve ENS names from a subgraph
    
    Features:
    - Single address resolution
    - Batch address resolution
    - Search by partial ENS name
    - Persistent disk cache with TTL
    """
    
    def __init__(self, ens_subgraph_url: str, cache_ttl: int = 86400):
        """Initialize ENS client
        
        Args:
            ens_subgraph_url: URL of the ENS subgraph
            cache_ttl: Cache time-to-live in seconds (default 24 hours)
        """
        self.ens_subgraph_url = ens_subgraph_url.rstrip('/')
        self._session = requests.Session()
        self._cache: Dict[str, dict] = {}
        self._cache_file = Path.home() / '.grtinfo' / 'ens_cache.json'
        self._cache_ttl = cache_ttl
        self._load_cache()
    
    def _load_cache(self):
        """Load ENS cache from disk"""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    cache_data = json.load(f)
                    now = time.time()
                    for addr, entry in cache_data.items():
                        if isinstance(entry, dict) and 'name' in entry and 'timestamp' in entry:
                            # Check TTL
                            if now - entry['timestamp'] < self._cache_ttl:
                                self._cache[addr] = entry
                        elif isinstance(entry, str) or entry is None:
                            # Old format - convert
                            self._cache[addr] = {'name': entry, 'timestamp': now - self._cache_ttl + 3600}
        except:
            pass
    
    def _save_cache(self):
        """Save ENS cache to disk"""
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, 'w') as f:
                json.dump(self._cache, f, indent=2)
        except:
            pass
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query against the ENS subgraph"""
        try:
            response = self._session.post(
                self.ens_subgraph_url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                return {}
            return data.get('data', {})
        except:
            return {}
    
    def resolve_address(self, address: str) -> Optional[str]:
        """Resolve a single Ethereum address to its ENS name
        
        Args:
            address: Ethereum address (0x...)
        
        Returns:
            ENS name or None if not found
        """
        if not address or address == 'Unknown':
            return None
        
        address_lower = address.lower()
        
        # Check cache
        if address_lower in self._cache:
            entry = self._cache[address_lower]
            if isinstance(entry, dict):
                return entry.get('name')
            return entry
        
        # Query subgraph
        query = """
        query ResolveAddress($address: String!) {
            domains(
                where: { resolvedAddress: $address }
                first: 1
                orderBy: createdAt
                orderDirection: desc
            ) {
                name
            }
        }
        """
        
        try:
            result = self.query(query, {'address': address_lower})
            domains = result.get('domains', [])
            if domains:
                name = domains[0].get('name')
                if name:
                    self._cache[address_lower] = {'name': name, 'timestamp': time.time()}
                    self._save_cache()
                    return name
        except:
            pass
        
        # Cache negative result
        self._cache[address_lower] = {'name': None, 'timestamp': time.time()}
        self._save_cache()
        return None
    
    def resolve_addresses_batch(self, addresses: List[str]) -> Dict[str, Optional[str]]:
        """Resolve multiple addresses in a single query
        
        Args:
            addresses: List of Ethereum addresses
        
        Returns:
            Dict mapping address (lowercase) to ENS name or None
        """
        results = {}
        addresses_lower = [addr.lower() for addr in addresses if addr and addr != 'Unknown']
        
        if not addresses_lower:
            return results
        
        # Check cache first
        to_query = []
        for addr in addresses_lower:
            if addr in self._cache:
                entry = self._cache[addr]
                if isinstance(entry, dict):
                    results[addr] = entry.get('name')
                else:
                    results[addr] = entry
            else:
                to_query.append(addr)
        
        # Return early if all cached
        if not to_query:
            return results
        
        # Batch query uncached addresses
        query = """
        query ResolveAddresses($addresses: [String!]!) {
            domains(
                where: { resolvedAddress_in: $addresses }
                first: 100
            ) {
                name
                resolvedAddress { id }
            }
        }
        """
        
        try:
            result = self.query(query, {'addresses': to_query})
            domains = result.get('domains', [])
            
            for domain in domains:
                addr = domain.get('resolvedAddress', {}).get('id', '').lower()
                name = domain.get('name')
                if addr and name:
                    results[addr] = name
                    self._cache[addr] = {'name': name, 'timestamp': time.time()}
            
            # Cache negative results
            for addr in to_query:
                if addr not in results:
                    results[addr] = None
                    self._cache[addr] = {'name': None, 'timestamp': time.time()}
            
            self._save_cache()
        except:
            pass
        
        return results
    
    def search_by_ens(self, partial_name: str) -> List[Dict]:
        """Search for ENS names containing the partial name
        
        Args:
            partial_name: Partial ENS name to search for
        
        Returns:
            List of dicts with 'name' and 'resolvedAddress' keys
        """
        query = """
        query SearchENS($search: String!) {
            domains(
                where: { name_contains: $search }
                first: 20
                orderBy: createdAt
                orderDirection: desc
            ) {
                name
                resolvedAddress { id }
            }
        }
        """
        result = self.query(query, {'search': partial_name.lower()})
        return result.get('domains', [])
    
    def resolve_name(self, name: str) -> Optional[str]:
        """Resolve an ENS name to its Ethereum address
        
        Args:
            name: ENS name to resolve (e.g., "vitalik.eth" or "vitalik")
        
        Returns:
            Ethereum address or None if not found
        """
        name_lower = name.lower()
        
        # Try exact match first
        query = """
        query ResolveName($name: String!) {
            domains(
                where: { name: $name }
                first: 1
            ) {
                resolvedAddress { id }
            }
        }
        """
        result = self.query(query, {'name': name_lower})
        domains = result.get('domains', [])
        if domains:
            resolved = domains[0].get('resolvedAddress', {})
            if resolved:
                return resolved.get('id')
        
        # If not found and name doesn't end with .eth, try adding .eth
        if not name_lower.endswith('.eth'):
            name_with_eth = name_lower + '.eth'
            result = self.query(query, {'name': name_with_eth})
            domains = result.get('domains', [])
            if domains:
                resolved = domains[0].get('resolvedAddress', {})
                if resolved:
                    return resolved.get('id')
        
        # If still not found, try partial match (name contains the search term)
        # This helps find names like "ellipfra-indexer.eth" when searching for "ellipfra"
        query_partial = """
        query ResolveNamePartial($search: String!) {
            domains(
                where: { name_contains: $search }
                first: 20
                orderBy: createdAt
                orderDirection: desc
            ) {
                name
                resolvedAddress { id }
            }
        }
        """
        result = self.query(query_partial, {'search': name_lower})
        domains = result.get('domains', [])
        if domains:
            # Prefer names that start with the search term (exact prefix match)
            # This helps find "ellipfra-indexer.eth" when searching for "ellipfra"
            for domain in domains:
                domain_name = domain.get('name', '').lower()
                resolved = domain.get('resolvedAddress', {})
                if resolved and resolved.get('id'):
                    # Prefer names that start with the search term followed by a separator
                    if domain_name.startswith(name_lower + '-') or domain_name.startswith(name_lower + '.'):
                        return resolved.get('id')
                    # Also accept exact prefix match
                    if domain_name.startswith(name_lower):
                        return resolved.get('id')
            # Last resort: return first result
            if domains[0].get('resolvedAddress', {}).get('id'):
                return domains[0].get('resolvedAddress', {}).get('id')
        
        return None

