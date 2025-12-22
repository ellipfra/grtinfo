#!/usr/bin/env python3
"""
Base GraphQL client for grtinfo CLI tools

Provides a reusable GraphQL client with session management,
error handling, and timeout configuration.
"""

import sys
from typing import Dict, Optional
import requests

from common import Colors


class GraphQLClient:
    """Base GraphQL client with error handling and session management
    
    This class provides the foundation for all GraphQL-based clients
    in the grtinfo tools (network subgraph, ENS, analytics, etc.)
    """
    
    def __init__(self, url: str, timeout: int = 30, silent_errors: bool = False):
        """Initialize GraphQL client
        
        Args:
            url: GraphQL endpoint URL
            timeout: Request timeout in seconds (default 30)
            silent_errors: If True, don't print errors to stderr
        """
        self.url = url.rstrip('/')
        self._session = requests.Session()
        self._timeout = timeout
        self._silent_errors = silent_errors
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query
        
        Args:
            query: GraphQL query string
            variables: Optional dict of query variables
        
        Returns:
            Dict containing the 'data' portion of the response,
            or empty dict on error
        """
        try:
            response = self._session.post(
                self.url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json'},
                timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
            
            if 'errors' in data:
                if not self._silent_errors:
                    errors = data['errors']
                    for error in errors:
                        msg = error.get('message', 'Unknown GraphQL error')
                        print(f"{Colors.RED}GraphQL error: {msg}{Colors.RESET}", file=sys.stderr)
                return {}
            
            return data.get('data', {})
            
        except requests.exceptions.Timeout:
            if not self._silent_errors:
                print(f"{Colors.RED}Query timeout after {self._timeout}s{Colors.RESET}", file=sys.stderr)
            return {}
        except requests.exceptions.ConnectionError:
            if not self._silent_errors:
                print(f"{Colors.RED}Connection error to {self.url}{Colors.RESET}", file=sys.stderr)
            return {}
        except requests.exceptions.HTTPError as e:
            if not self._silent_errors:
                print(f"{Colors.RED}HTTP error: {e}{Colors.RESET}", file=sys.stderr)
            return {}
        except Exception as e:
            if not self._silent_errors:
                print(f"{Colors.RED}Query error: {e}{Colors.RESET}", file=sys.stderr)
            return {}
    
    def close(self):
        """Close the session"""
        self._session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class NetworkSubgraphClient(GraphQLClient):
    """Client for The Graph Network subgraph
    
    Provides common queries used across multiple tools.
    """
    
    def get_indexer_details(self, indexer_id: str) -> Optional[Dict]:
        """Get basic indexer information"""
        query = """
        query GetIndexer($id: String!) {
            indexer(id: $id) {
                id
                url
                stakedTokens
                delegatedTokens
                allocatedTokens
                indexingRewardCut
                queryFeeCut
                delegatorShares
                allocationCount
            }
        }
        """
        result = self.query(query, {'id': indexer_id.lower()})
        return result.get('indexer')
    
    def get_indexers_urls(self, indexer_ids: list) -> Dict[str, str]:
        """Get URLs for multiple indexers
        
        Args:
            indexer_ids: List of indexer addresses
        
        Returns:
            Dict mapping indexer_id (lowercase) to URL
        """
        if not indexer_ids:
            return {}
        
        unique_ids = list(set(id.lower() for id in indexer_ids if id))
        results = {}
        
        # Query in batches of 100
        batch_size = 100
        for i in range(0, len(unique_ids), batch_size):
            batch = unique_ids[i:i+batch_size]
            query = """
            query GetIndexersUrls($ids: [String!]!) {
                indexers(where: { id_in: $ids }) {
                    id
                    url
                }
            }
            """
            result = self.query(query, {'ids': batch})
            for indexer in result.get('indexers', []):
                indexer_id = indexer.get('id', '').lower()
                url = indexer.get('url')
                if url:
                    results[indexer_id] = url
        
        return results
    
    def get_deployment_info(self, ipfs_hash: str) -> Optional[Dict]:
        """Get subgraph deployment information by IPFS hash"""
        query = """
        query GetDeployment($ipfsHash: String!) {
            subgraphDeployments(where: { ipfsHash: $ipfsHash }, first: 1) {
                id
                ipfsHash
                signalledTokens
                stakedTokens
                createdAt
                manifest {
                    network
                }
                versions(first: 1, orderBy: createdAt, orderDirection: desc) {
                    subgraph {
                        id
                    }
                }
            }
        }
        """
        result = self.query(query, {'ipfsHash': ipfs_hash})
        deployments = result.get('subgraphDeployments', [])
        return deployments[0] if deployments else None

