#!/usr/bin/env python3
"""
Tests for parsing subgraph and RPC data

These tests verify that the tools correctly handle various response formats
from The Graph Network subgraph and RPC endpoints, including edge cases.
"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures.graphql_responses import (
    INDEXER_FULL_RESPONSE, INDEXER_MINIMAL_RESPONSE, INDEXER_WITH_NULL_URL,
    INDEXER_NOT_FOUND, INDEXER_OVER_ALLOCATED, INDEXER_WHALE,
    ALLOCATIONS_ACTIVE, ALLOCATIONS_EMPTY, ALLOCATIONS_CLOSED_WITH_LEGACY,
    ALLOCATIONS_MISSING_DEPLOYMENT,
    DELEGATIONS_RESPONSE, DELEGATIONS_EMPTY,
    NETWORK_STATS,
    SUBGRAPH_DEPLOYMENT_FOUND, SUBGRAPH_DEPLOYMENT_NOT_FOUND,
    GRAPHQL_ERROR_SCHEMA, GRAPHQL_ERROR_VARIABLE,
    POI_SUBMISSIONS, POI_SUBMISSIONS_EMPTY
)

from tests.fixtures.rpc_responses import (
    STATUS_HEALTHY_SYNCED, STATUS_SYNCING, STATUS_FALSE_SYNCED,
    STATUS_FAILED, STATUS_NULL_CHAINS, STATUS_EMPTY_CHAINS,
    STATUS_NULL_BLOCKS, STATUS_MULTIPLE_DEPLOYMENTS, STATUS_EMPTY,
    SINGLE_REWARD_LOG, MULTIPLE_REWARDS_SAME_ALLOCATION,
    REWARDS_MULTIPLE_ALLOCATIONS, EMPTY_LOGS,
    PROVISION_NO_THAWING, PROVISION_WITH_THAWING,
)

from contracts import (HorizonStakingClient, RewardsManagerClient,
                       RewardsEligibilityClient, derive_eligibility)


class TestIndexerDataParsing:
    """Tests for parsing indexer data from subgraph responses"""
    
    def test_parse_full_indexer_data(self):
        """Test parsing complete indexer response"""
        indexer = INDEXER_FULL_RESPONSE["data"]["indexer"]
        
        # Verify all expected fields are present
        assert indexer["id"] == "0xf92f430dd8567b0d466358c79594ab58d919a6d4"
        assert int(indexer["stakedTokens"]) / 1e18 == pytest.approx(7500862, rel=1e-6)
        assert int(indexer["indexingRewardCut"]) == 265000  # 26.5% in PPM
        assert indexer["url"] == "https://graph-l2prod.ellipfra.com/"
    
    def test_parse_minimal_indexer_data(self):
        """Test parsing indexer with only required fields"""
        indexer = INDEXER_MINIMAL_RESPONSE["data"]["indexer"]
        
        assert indexer["id"] is not None
        assert int(indexer["stakedTokens"]) / 1e18 == pytest.approx(1000, rel=1e-6)
        assert int(indexer["delegatedTokens"]) == 0
    
    def test_handle_null_url(self):
        """Test handling indexer with null URL"""
        indexer = INDEXER_WITH_NULL_URL["data"]["indexer"]
        
        # Should be able to access without error
        url = indexer.get("url")
        assert url is None
        
        # Code should handle this gracefully
        display_url = url if url else "No URL"
        assert display_url == "No URL"
    
    def test_handle_indexer_not_found(self):
        """Test handling when indexer doesn't exist"""
        indexer = INDEXER_NOT_FOUND["data"]["indexer"]
        assert indexer is None
    
    def test_detect_over_allocated_indexer(self):
        """Test detecting over-allocated indexer (negative remaining)"""
        indexer = INDEXER_OVER_ALLOCATED["data"]["indexer"]
        
        total = int(indexer["tokenCapacity"]) / 1e18
        allocated = int(indexer["allocatedTokens"]) / 1e18
        remaining = total - allocated
        
        assert remaining < 0  # Over-allocated
        assert remaining == pytest.approx(-1_000_000, rel=1e-6)
    
    def test_whale_indexer_numbers(self):
        """Test handling very large token amounts"""
        indexer = INDEXER_WHALE["data"]["indexer"]
        
        staked = int(indexer["stakedTokens"]) / 1e18
        delegated = int(indexer["delegatedTokens"]) / 1e18
        
        assert staked == pytest.approx(50_000_000, rel=1e-6)
        assert delegated == pytest.approx(800_000_000, rel=1e-6)
        
        # Verify delegation is at max (16x)
        max_delegation = staked * 16
        assert delegated == max_delegation


class TestAllocationDataParsing:
    """Tests for parsing allocation data"""
    
    def test_parse_active_allocations(self):
        """Test parsing active allocations with deployment info"""
        allocations = ALLOCATIONS_ACTIVE["data"]["allocations"]
        
        assert len(allocations) == 2
        
        alloc = allocations[0]
        assert alloc["status"] == "Active"
        assert int(alloc["allocatedTokens"]) / 1e18 == pytest.approx(5_000_000, rel=1e-6)
        assert alloc["subgraphDeployment"]["ipfsHash"].startswith("Qm")
    
    def test_parse_empty_allocations(self):
        """Test handling empty allocations list"""
        allocations = ALLOCATIONS_EMPTY["data"]["allocations"]
        
        assert allocations == []
        assert len(allocations) == 0
    
    def test_parse_legacy_vs_normal_allocations(self):
        """Test distinguishing legacy from normal allocations"""
        allocations = ALLOCATIONS_CLOSED_WITH_LEGACY["data"]["allocations"]
        
        legacy = next(a for a in allocations if a.get("isLegacy"))
        normal = next(a for a in allocations if not a.get("isLegacy"))
        
        # Legacy has 0 rewards in subgraph
        assert int(legacy["indexingRewards"]) == 0
        
        # Normal has rewards tracked
        assert int(normal["indexingRewards"]) > 0
    
    def test_handle_missing_deployment(self):
        """Test handling allocation with null deployment"""
        allocations = ALLOCATIONS_MISSING_DEPLOYMENT["data"]["allocations"]
        
        alloc = allocations[0]
        deployment = alloc.get("subgraphDeployment")
        
        assert deployment is None
        
        # Code should handle this gracefully
        ipfs_hash = deployment.get("ipfsHash", "Unknown") if deployment else "Unknown"
        assert ipfs_hash == "Unknown"


class TestDelegationDataParsing:
    """Tests for parsing delegation data"""
    
    def test_parse_delegations_with_thawing(self):
        """Test parsing delegations including thawing amounts"""
        delegations = DELEGATIONS_RESPONSE["data"]["delegatedStakes"]
        
        # Find delegation with thawing tokens
        thawing = next(d for d in delegations if int(d["lockedTokens"]) > 0)
        
        staked = int(thawing["stakedTokens"]) / 1e18
        locked = int(thawing["lockedTokens"]) / 1e18
        
        assert staked == pytest.approx(50_000, rel=1e-6)
        assert locked == pytest.approx(10_000, rel=1e-6)
    
    def test_parse_empty_delegations(self):
        """Test handling delegator with no delegations"""
        delegations = DELEGATIONS_EMPTY["data"]["delegatedStakes"]
        
        assert delegations == []


class TestNetworkStatsParsing:
    """Tests for parsing network-wide statistics"""
    
    def test_parse_network_stats(self):
        """Test parsing network statistics for APR calculation"""
        network = NETWORK_STATS["data"]["graphNetwork"]
        
        total_allocated = int(network["totalTokensAllocated"]) / 1e18
        total_signalled = int(network["totalTokensSignalled"]) / 1e18
        issuance_per_block = int(network["networkGRTIssuancePerBlock"]) / 1e18
        
        assert total_allocated == pytest.approx(3_500_000_000, rel=1e-6)
        assert total_signalled == pytest.approx(150_000_000, rel=1e-6)
        assert issuance_per_block == pytest.approx(0.114, rel=1e-2)


class TestSyncStatusParsing:
    """Tests for parsing indexer status endpoint responses"""
    
    def test_parse_healthy_synced(self):
        """Test parsing synced deployment status"""
        statuses = STATUS_HEALTHY_SYNCED["data"]["indexingStatuses"]
        
        status = statuses[0]
        assert status["synced"] == True
        assert status["health"] == "healthy"
        
        chain = status["chains"][0]
        assert int(chain["latestBlock"]["number"]) == int(chain["chainHeadBlock"]["number"])
    
    def test_parse_syncing_status(self):
        """Test parsing deployment that is still syncing"""
        statuses = STATUS_SYNCING["data"]["indexingStatuses"]
        
        status = statuses[0]
        chain = status["chains"][0]
        
        latest = int(chain["latestBlock"]["number"])
        head = int(chain["chainHeadBlock"]["number"])
        behind = head - latest
        
        assert behind == 500_000
        assert status["synced"] == False
    
    def test_detect_false_synced_bug(self):
        """Test detecting the 'false synced' bug (synced=True but far behind)"""
        statuses = STATUS_FALSE_SYNCED["data"]["indexingStatuses"]
        
        status = statuses[0]
        chain = status["chains"][0]
        
        # Graph Node says synced...
        assert status["synced"] == True
        
        # ...but calculate actual blocks behind
        latest = int(chain["latestBlock"]["number"])
        head = int(chain["chainHeadBlock"]["number"])
        behind = head - latest
        
        # Actually 300k behind!
        assert behind == 300_000
        
        # Our code should NOT trust synced=True when behind > threshold
        threshold = 100
        actual_synced = status["synced"] and behind <= threshold
        assert actual_synced == False
    
    def test_parse_failed_status(self):
        """Test parsing failed deployment with error"""
        statuses = STATUS_FAILED["data"]["indexingStatuses"]
        
        status = statuses[0]
        assert status["health"] == "failed"
        assert status["fatalError"]["message"] is not None
        assert "deterministic error" in status["fatalError"]["message"]
    
    def test_handle_null_chains(self):
        """Test handling null chains array"""
        statuses = STATUS_NULL_CHAINS["data"]["indexingStatuses"]
        
        status = statuses[0]
        chains = status.get("chains") or []
        
        assert chains == [] or chains is None
        
        # Code should handle gracefully
        chain = chains[0] if chains else {}
        latest = chain.get("latestBlock", {}).get("number", 0) if chain else 0
        assert latest == 0
    
    def test_handle_empty_chains(self):
        """Test handling empty chains array"""
        statuses = STATUS_EMPTY_CHAINS["data"]["indexingStatuses"]
        
        status = statuses[0]
        chains = status.get("chains") or []
        
        assert len(chains) == 0
    
    def test_handle_null_latest_block(self):
        """Test handling null latestBlock"""
        statuses = STATUS_NULL_BLOCKS["data"]["indexingStatuses"]
        
        status = statuses[0]
        chain = status["chains"][0]
        
        # latestBlock is None
        assert chain["latestBlock"] is None
        
        # Code should handle gracefully
        latest_block = chain.get("latestBlock") or {}
        latest = int(latest_block.get("number", 0) or 0)
        assert latest == 0
    
    def test_parse_multiple_deployments(self):
        """Test parsing status with multiple deployments"""
        statuses = STATUS_MULTIPLE_DEPLOYMENTS["data"]["indexingStatuses"]
        
        assert len(statuses) == 3
        
        # Build lookup by hash
        status_map = {s["subgraph"]: s for s in statuses}
        
        # Verify different states
        synced = status_map["QmDeployment1234567890123456789012345678"]
        syncing = status_map["QmDeployment2345678901234567890123456789"]
        failed = status_map["QmDeployment3456789012345678901234567890"]
        
        assert synced["synced"] == True
        assert syncing["synced"] == False and syncing["health"] == "healthy"
        assert failed["health"] == "failed"


class TestGraphQLErrorHandling:
    """Tests for handling GraphQL errors"""
    
    def test_detect_schema_error(self):
        """Test detecting schema/field errors"""
        response = GRAPHQL_ERROR_SCHEMA
        
        assert "errors" in response
        assert "has no field" in response["errors"][0]["message"]
    
    def test_detect_variable_error(self):
        """Test detecting variable validation errors"""
        response = GRAPHQL_ERROR_VARIABLE
        
        assert "errors" in response
        assert "invalid value" in response["errors"][0]["message"]
    
    def test_graceful_error_handling(self):
        """Test that errors don't cause crashes"""
        response = GRAPHQL_ERROR_SCHEMA
        
        # Typical pattern: check for errors, return empty data
        if "errors" in response:
            data = {}
        else:
            data = response.get("data", {})
        
        allocations = data.get("allocations", [])
        assert allocations == []


class TestRewardCalculations:
    """Tests for reward-related calculations"""
    
    def test_legacy_rewards_aggregation(self):
        """Test aggregating multiple reward events for legacy allocations"""
        logs = MULTIPLE_REWARDS_SAME_ALLOCATION
        
        total_rewards = 0
        for log in logs:
            amount = int(log.data.hex(), 16)
            total_rewards += amount
        
        # 10k + 15k + 25k = 50k GRT
        assert total_rewards / 1e18 == pytest.approx(50_000, rel=1e-6)
    
    def test_rewards_per_allocation(self):
        """Test mapping rewards to different allocations"""
        logs = REWARDS_MULTIPLE_ALLOCATIONS
        
        rewards_by_alloc = {}
        for log in logs:
            alloc_id = "0x" + log.topics[2].hex()[-40:]
            amount = int(log.data.hex(), 16)
            rewards_by_alloc[alloc_id] = rewards_by_alloc.get(alloc_id, 0) + amount
        
        assert len(rewards_by_alloc) == 3
        assert rewards_by_alloc["0x0a110c1111111111111111111111111111111111"] / 1e18 == pytest.approx(50_000)
        assert rewards_by_alloc["0x0a110c2222222222222222222222222222222222"] / 1e18 == pytest.approx(30_000)
        assert rewards_by_alloc["0x0a110c3333333333333333333333333333333333"] / 1e18 == pytest.approx(20_000)
    
    def test_empty_rewards(self):
        """Test handling no reward events"""
        logs = EMPTY_LOGS
        
        total_rewards = sum(int(log.data.hex(), 16) for log in logs)
        assert total_rewards == 0


class TestPOISubmissionParsing:
    """Tests for parsing POI submission data"""
    
    def test_parse_poi_submissions(self):
        """Test parsing POI submissions for reward collection tracking"""
        submissions = POI_SUBMISSIONS["data"]["poiSubmissions"]
        
        assert len(submissions) == 1
        
        poi = submissions[0]
        alloc = poi["allocation"]
        
        assert alloc["status"] == "Active"
        rewards = int(alloc["indexingRewards"]) / 1e18
        assert rewards == pytest.approx(25_000, rel=1e-6)
    
    def test_empty_poi_submissions(self):
        """Test handling no POI submissions"""
        submissions = POI_SUBMISSIONS_EMPTY["data"]["poiSubmissions"]
        
        assert submissions == []


class TestTokenConversions:
    """Tests for token amount conversions (wei to GRT)"""
    
    def test_small_amount_conversion(self):
        """Test converting small token amounts"""
        wei = "1000000000000000000"  # 1 GRT
        grt = int(wei) / 1e18
        assert grt == 1.0
    
    def test_large_amount_conversion(self):
        """Test converting large token amounts (billions)"""
        wei = "1000000000000000000000000000"  # 1B GRT
        grt = int(wei) / 1e18
        assert grt == 1_000_000_000
    
    def test_fractional_amount_conversion(self):
        """Test converting fractional token amounts"""
        wei = "1500000000000000000"  # 1.5 GRT
        grt = int(wei) / 1e18
        assert grt == 1.5
    
    def test_zero_amount_conversion(self):
        """Test converting zero tokens"""
        wei = "0"
        grt = int(wei) / 1e18
        assert grt == 0.0
    
    def test_dust_amount_conversion(self):
        """Test converting dust amounts (very small)"""
        wei = "100000000000000"  # 0.0001 GRT
        grt = int(wei) / 1e18
        assert grt == pytest.approx(0.0001, rel=1e-6)


class TestProvisionParsing:
    """Tests for parsing HorizonStaking getProvision responses"""

    def test_parse_provision_no_thawing(self):
        """Test parsing provision with no thawing tokens"""
        client = HorizonStakingClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value=PROVISION_NO_THAWING):
            result = client.get_provision("0x" + "a" * 40)

        assert result is not None
        assert result['tokens'] == 10_000_862_000000000000000000
        assert result['tokensThawing'] == 0

    def test_parse_provision_with_thawing(self):
        """Test parsing provision with large thawing amount"""
        client = HorizonStakingClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value=PROVISION_WITH_THAWING):
            result = client.get_provision("0x" + "a" * 40)

        assert result is not None
        assert result['tokensThawing'] / 1e18 == pytest.approx(10_084_699, rel=1e-6)

    def test_provision_rpc_failure(self):
        """Test get_provision returns None on RPC failure"""
        client = HorizonStakingClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value=None):
            result = client.get_provision("0x" + "a" * 40)

        assert result is None

    def test_provision_empty_response(self):
        """Test get_provision returns None on empty/short response"""
        client = HorizonStakingClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value="0x"):
            result = client.get_provision("0x" + "a" * 40)

        assert result is None

    def test_provision_short_response(self):
        """Test get_provision returns None when response has fewer than 2 slots"""
        short = "0x" + "0" * 64  # Only 1 slot
        client = HorizonStakingClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value=short):
            result = client.get_provision("0x" + "a" * 40)

        assert result is None


def _uint256_hex(value: int) -> str:
    return "0x" + hex(value)[2:].zfill(64)


class TestIssuancePerBlock:
    """Tests for RewardsManagerClient.get_issuance_per_block (GIP-0086/0088 split)"""

    ALLOCATOR = "0xb64f29b2d81140ffc3a135e319561a1bd03b1a7e"
    RAW = 120_730_000_000_000_000_000        # 120.73 GRT/block
    ALLOCATED = 96_584_000_000_000_000_000   # 80% -> indexing rewards

    def _responses(self, allocated, raw, allocator, total):
        from contracts import (
            REWARDS_MANAGER,
            GET_ALLOCATED_ISSUANCE_PER_BLOCK_SELECTOR,
            GET_RAW_ISSUANCE_PER_BLOCK_SELECTOR,
            GET_ISSUANCE_ALLOCATOR_SELECTOR,
            GET_ALLOCATOR_ISSUANCE_PER_BLOCK_SELECTOR,
        )
        table = {
            (REWARDS_MANAGER, GET_ALLOCATED_ISSUANCE_PER_BLOCK_SELECTOR): allocated,
            (REWARDS_MANAGER, GET_RAW_ISSUANCE_PER_BLOCK_SELECTOR): raw,
            (REWARDS_MANAGER, GET_ISSUANCE_ALLOCATOR_SELECTOR): allocator,
            (self.ALLOCATOR, GET_ALLOCATOR_ISSUANCE_PER_BLOCK_SELECTOR): total,
        }
        return lambda to, data, block="latest": table.get((to, data))

    def test_split_with_issuance_allocator(self):
        """RewardsManager only mints the allocator's selfIssuanceRate (80% after GIP-0089)"""
        client = RewardsManagerClient("http://fake-rpc")
        fake = self._responses(
            _uint256_hex(self.ALLOCATED), _uint256_hex(self.RAW),
            "0x" + self.ALLOCATOR[2:].zfill(64), _uint256_hex(self.RAW),
        )
        with patch.object(client, '_eth_call', side_effect=fake):
            result = client.get_issuance_per_block()

        assert result == {
            'allocated': self.ALLOCATED,
            'raw': self.RAW,
            'total': self.RAW,
            'allocator': self.ALLOCATOR,
        }
        assert result['allocated'] / result['total'] == pytest.approx(0.8)

    def test_no_allocator_configured(self):
        """Without an allocator the RewardsManager mints its raw issuancePerBlock"""
        client = RewardsManagerClient("http://fake-rpc")
        fake = self._responses(
            _uint256_hex(self.RAW), _uint256_hex(self.RAW), _uint256_hex(0), None,
        )
        with patch.object(client, '_eth_call', side_effect=fake):
            result = client.get_issuance_per_block()

        assert result['allocated'] == self.RAW
        assert result['total'] == self.RAW
        assert result['allocator'] is None

    def test_rpc_failure_returns_none(self):
        """Callers must fall back to the subgraph value when the RPC call fails"""
        client = RewardsManagerClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value=None):
            assert client.get_issuance_per_block() is None

    def test_allocator_total_failure_falls_back_to_raw(self):
        """If the allocator call fails, 'total' falls back to the raw value"""
        client = RewardsManagerClient("http://fake-rpc")
        fake = self._responses(
            _uint256_hex(self.ALLOCATED), _uint256_hex(self.RAW),
            "0x" + self.ALLOCATOR[2:].zfill(64), None,
        )
        with patch.object(client, '_eth_call', side_effect=fake):
            result = client.get_issuance_per_block()

        assert result['allocated'] == self.ALLOCATED
        assert result['total'] == self.RAW
        assert result['allocator'] == self.ALLOCATOR


class TestRewardsEligibility:
    """Tests for the Rewards Eligibility Oracle client (GIP-0079)"""

    ORACLE = "0x02753bae61c08abd4351bce7f48524935c2cc78e"
    INDEXER = "0xf92f430dd8567b0d466358c79594ab58d919a6d4"
    PERIOD = 1_209_600   # 14 days
    TIMEOUT = 604_800    # 7 days
    NOW = 1_788_400_000

    def _config(self, **overrides):
        config = {
            'oracle': self.ORACLE,
            'validation_enabled': True,
            'eligibility_period': self.PERIOD,
            'oracle_timeout': self.TIMEOUT,
            'last_oracle_update': self.NOW - 3600,
            'revert_on_ineligible': True,
        }
        config.update(overrides)
        return config

    def _responses(self, oracle, renewal, is_eligible, validation=True,
                   period=None, timeout=None, last_update=None, revert=True):
        """Build an _eth_call side_effect table for the whole call sequence."""
        from contracts import (
            REWARDS_MANAGER,
            GET_PROVIDER_ELIGIBILITY_ORACLE_SELECTOR,
            GET_REVERT_ON_INELIGIBLE_SELECTOR,
            GET_ELIGIBILITY_VALIDATION_SELECTOR,
            GET_ELIGIBILITY_PERIOD_SELECTOR,
            GET_ORACLE_UPDATE_TIMEOUT_SELECTOR,
            GET_LAST_ORACLE_UPDATE_TIME_SELECTOR,
            GET_ELIGIBILITY_RENEWAL_TIME_SELECTOR,
            IS_ELIGIBLE_SELECTOR,
        )
        encoded = self.INDEXER.lower().replace("0x", "").zfill(64)
        period = self.PERIOD if period is None else period
        timeout = self.TIMEOUT if timeout is None else timeout
        last_update = self.NOW - 3600 if last_update is None else last_update
        table = {
            (REWARDS_MANAGER, GET_PROVIDER_ELIGIBILITY_ORACLE_SELECTOR):
                "0x" + (oracle or "0x0")[2:].zfill(64),
            (REWARDS_MANAGER, GET_REVERT_ON_INELIGIBLE_SELECTOR): _uint256_hex(1 if revert else 0),
            (self.ORACLE, GET_ELIGIBILITY_VALIDATION_SELECTOR): _uint256_hex(1 if validation else 0),
            (self.ORACLE, GET_ELIGIBILITY_PERIOD_SELECTOR): _uint256_hex(period),
            (self.ORACLE, GET_ORACLE_UPDATE_TIMEOUT_SELECTOR): _uint256_hex(timeout),
            (self.ORACLE, GET_LAST_ORACLE_UPDATE_TIME_SELECTOR): _uint256_hex(last_update),
            (self.ORACLE, GET_ELIGIBILITY_RENEWAL_TIME_SELECTOR + encoded): _uint256_hex(renewal),
            (self.ORACLE, IS_ELIGIBLE_SELECTOR + encoded): _uint256_hex(1 if is_eligible else 0),
        }
        return lambda to, data, block="latest": table.get((to, data))

    # -- pure helper ------------------------------------------------------

    def test_derive_eligible_with_recent_renewal(self):
        """A renewal inside the eligibility period keeps the indexer eligible"""
        renewal = self.NOW - 86400
        state = derive_eligibility(self._config(), renewal, self.NOW)
        assert state['eligible'] is True
        assert state['reason'] == 'renewed'
        assert state['expires_at'] == renewal + self.PERIOD

    def test_derive_expired_renewal_is_ineligible(self):
        """A renewal older than the eligibility period makes the indexer ineligible"""
        renewal = self.NOW - self.PERIOD - 2 * 86400
        state = derive_eligibility(self._config(), renewal, self.NOW)
        assert state['eligible'] is False
        assert state['reason'] == 'expired'
        assert state['expires_at'] == renewal + self.PERIOD

    def test_derive_never_renewed(self):
        """renewalTime == 0 means the oracle never renewed this indexer"""
        state = derive_eligibility(self._config(), 0, self.NOW)
        assert state['eligible'] is False
        assert state['reason'] == 'never_renewed'
        assert state['expires_at'] is None

    def test_derive_validation_disabled(self):
        """Validation disabled globally: everyone is eligible"""
        state = derive_eligibility(self._config(validation_enabled=False), 0, self.NOW)
        assert state['eligible'] is True
        assert state['reason'] == 'validation_disabled'

    def test_derive_oracle_stale_failsafe(self):
        """A stale oracle cannot starve the network: everyone stays eligible"""
        config = self._config(last_oracle_update=self.NOW - self.TIMEOUT - 86400)
        state = derive_eligibility(config, 0, self.NOW)
        assert state['eligible'] is True
        assert state['reason'] == 'oracle_stale'
        assert state['oracle_stale_for'] == self.TIMEOUT + 86400

    def test_derive_no_oracle_configured(self):
        """No oracle on the RewardsManager: eligibility is not enforced"""
        state = derive_eligibility(self._config(oracle=None), 0, self.NOW)
        assert state['eligible'] is True
        assert state['reason'] == 'no_oracle'

    def test_derive_without_config_is_permissive(self):
        """A missing config must never be reported as ineligible"""
        state = derive_eligibility(None, 0, self.NOW)
        assert state['eligible'] is True
        assert state['reason'] == 'no_oracle'

    # -- client -----------------------------------------------------------

    def test_config_reads_oracle_and_parameters(self):
        client = RewardsEligibilityClient("http://fake-rpc")
        fake = self._responses(self.ORACLE, self.NOW - 86400, True)
        with patch.object(client, '_eth_call', side_effect=fake):
            config = client.get_oracle_config()

        assert config == {
            'oracle': self.ORACLE,
            'validation_enabled': True,
            'eligibility_period': self.PERIOD,
            'oracle_timeout': self.TIMEOUT,
            'last_oracle_update': self.NOW - 3600,
            'revert_on_ineligible': True,
        }

    def test_config_is_cached(self):
        """The oracle config is read once per client instance"""
        client = RewardsEligibilityClient("http://fake-rpc")
        fake = self._responses(self.ORACLE, 0, False)
        with patch.object(client, '_eth_call', side_effect=fake) as call:
            client.get_oracle_config()
            calls_after_first = call.call_count
            client.get_oracle_config()
            assert call.call_count == calls_after_first

    def test_indexer_eligible_with_renewal(self):
        client = RewardsEligibilityClient("http://fake-rpc")
        renewal = int(time.time()) - 86400
        fake = self._responses(self.ORACLE, renewal, True)
        with patch.object(client, '_eth_call', side_effect=fake):
            state = client.get_indexer_eligibility(self.INDEXER)

        assert state['eligible'] is True
        assert state['reason'] == 'renewed'
        assert state['renewal_time'] == renewal

    def test_indexer_expired_renewal(self):
        client = RewardsEligibilityClient("http://fake-rpc")
        now = int(time.time())
        renewal = now - self.PERIOD - 2 * 86400
        fake = self._responses(self.ORACLE, renewal, False, last_update=now - 3600)
        with patch.object(client, '_eth_call', side_effect=fake):
            state = client.get_indexer_eligibility(self.INDEXER)

        assert state['eligible'] is False
        assert state['reason'] == 'expired'

    def test_indexer_never_renewed(self):
        client = RewardsEligibilityClient("http://fake-rpc")
        fake = self._responses(self.ORACLE, 0, False, last_update=int(time.time()) - 3600)
        with patch.object(client, '_eth_call', side_effect=fake):
            state = client.get_indexer_eligibility(self.INDEXER)

        assert state['eligible'] is False
        assert state['reason'] == 'never_renewed'

    def test_validation_disabled_skips_indexer_lookup(self):
        """With validation off, everyone is eligible whatever the renewal time"""
        client = RewardsEligibilityClient("http://fake-rpc")
        fake = self._responses(self.ORACLE, 0, True, validation=False,
                               last_update=int(time.time()) - 3600)
        with patch.object(client, '_eth_call', side_effect=fake):
            state = client.get_indexer_eligibility(self.INDEXER)

        assert state['eligible'] is True
        assert state['reason'] == 'validation_disabled'

    def test_oracle_stale_failsafe_via_client(self):
        client = RewardsEligibilityClient("http://fake-rpc")
        now = int(time.time())
        fake = self._responses(self.ORACLE, 0, True,
                               last_update=now - self.TIMEOUT - 86400)
        with patch.object(client, '_eth_call', side_effect=fake):
            state = client.get_indexer_eligibility(self.INDEXER)

        assert state['eligible'] is True
        assert state['reason'] == 'oracle_stale'

    def test_no_oracle_configured_via_client(self):
        """A zero oracle address means eligibility is not enforced at all"""
        client = RewardsEligibilityClient("http://fake-rpc")
        fake = self._responses(None, 0, False)
        with patch.object(client, '_eth_call', side_effect=fake):
            config = client.get_oracle_config()
            state = client.get_indexer_eligibility(self.INDEXER)

        assert config['oracle'] is None
        assert state['eligible'] is True
        assert state['reason'] == 'no_oracle'

    def test_rpc_failure_returns_none(self):
        """Tools must keep working (and show nothing) when the RPC is down"""
        client = RewardsEligibilityClient("http://fake-rpc")
        with patch.object(client, '_eth_call', return_value=None):
            assert client.get_oracle_config() is None
            assert client.get_indexer_eligibility(self.INDEXER) is None
            assert client.get_eligibility_batch([self.INDEXER]) == {}

    def test_batch_uses_single_rpc_request(self):
        """get_eligibility_batch issues one batched eth_call request"""
        client = RewardsEligibilityClient("http://fake-rpc")
        now = int(time.time())
        renewal = now - 86400
        client._config = {
            'oracle': self.ORACLE,
            'validation_enabled': True,
            'eligibility_period': self.PERIOD,
            'oracle_timeout': self.TIMEOUT,
            'last_oracle_update': now - 3600,
            'revert_on_ineligible': True,
        }
        client._config_loaded = True

        other = "0x1111111111111111111111111111111111111111"
        response = Mock()
        response.raise_for_status = Mock()
        # ids: 2i = isEligible, 2i+1 = renewal time, in sorted-address order
        response.json = Mock(return_value=[
            {"id": 0, "result": _uint256_hex(0)},
            {"id": 1, "result": _uint256_hex(0)},
            {"id": 2, "result": _uint256_hex(1)},
            {"id": 3, "result": _uint256_hex(renewal)},
        ])
        with patch.object(client._session, 'post', return_value=response) as post:
            result = client.get_eligibility_batch([self.INDEXER.upper(), other])

        assert post.call_count == 1
        assert len(post.call_args[1]['json']) == 4
        assert result[other]['eligible'] is False
        assert result[other]['reason'] == 'never_renewed'
        assert result[self.INDEXER]['eligible'] is True
        assert result[self.INDEXER]['reason'] == 'renewed'

    def test_batch_falls_back_to_sequential_calls(self):
        """A failing batch request must not lose the eligibility data"""
        client = RewardsEligibilityClient("http://fake-rpc")
        now = int(time.time())
        fake = self._responses(self.ORACLE, now - 86400, True, last_update=now - 3600)
        with patch.object(client, '_eth_call', side_effect=fake):
            with patch.object(client._session, 'post', side_effect=Exception("boom")):
                result = client.get_eligibility_batch([self.INDEXER])

        assert result[self.INDEXER]['reason'] == 'renewed'

    def test_empty_batch_returns_empty_dict(self):
        client = RewardsEligibilityClient("http://fake-rpc")
        assert client.get_eligibility_batch([]) == {}


class TestEligibilityFormatting:
    """Tests for the shared eligibility display helper"""

    NOW = 1_788_400_000

    def _fmt(self, state, style):
        from common import format_eligibility, strip_ansi
        return strip_ansi(format_eligibility(state, style, now=self.NOW))

    def test_full_renewed(self):
        state = {'eligible': True, 'reason': 'renewed',
                 'renewal_time': self.NOW - 27 * 3600,
                 'expires_at': self.NOW + int(12.9 * 86400)}
        assert self._fmt(state, 'full') == "ELIGIBLE (renewed 27h ago, expires in 12.9d)"

    def test_full_never_renewed(self):
        state = {'eligible': False, 'reason': 'never_renewed',
                 'renewal_time': 0, 'expires_at': None}
        assert self._fmt(state, 'full') == "NOT ELIGIBLE (never renewed by the oracle)"

    def test_short_expired(self):
        state = {'eligible': False, 'reason': 'expired',
                 'renewal_time': self.NOW - 16 * 86400,
                 'expires_at': self.NOW - 2 * 86400}
        assert self._fmt(state, 'short') == "NOT ELIGIBLE (expired 2.0d ago)"

    def test_compact_is_empty_when_eligible(self):
        state = {'eligible': True, 'reason': 'renewed',
                 'renewal_time': self.NOW - 3600, 'expires_at': self.NOW + 86400 * 13}
        assert self._fmt(state, 'compact') == ""

    def test_compact_marks_ineligible(self):
        state = {'eligible': False, 'reason': 'never_renewed',
                 'renewal_time': 0, 'expires_at': None}
        assert "ineligible" in self._fmt(state, 'compact')

    def test_none_state_renders_nothing(self):
        assert self._fmt(None, 'full') == ""


class TestTotalSignalledTokens:
    """Tests for summing the network signal across subgraph deployments"""

    def _client(self):
        from indexerinfo import TheGraphClient
        return TheGraphClient("http://fake-subgraph")

    def test_sums_single_page(self):
        """Test summing signalledTokens from a single page"""
        client = self._client()
        page = {'subgraphDeployments': [
            {'id': '0x01', 'signalledTokens': str(int(1000 * 1e18))},
            {'id': '0x02', 'signalledTokens': str(int(2500 * 1e18))},
        ]}
        with patch.object(client, 'query', return_value=page):
            total = client.get_total_signalled_tokens()

        assert total == int(3500 * 1e18)

    def test_paginates_on_id(self):
        """Test pagination continues while full pages are returned"""
        client = self._client()
        full_page = {'subgraphDeployments': [
            {'id': f'0x{i:04x}', 'signalledTokens': str(int(1e18))} for i in range(1000)
        ]}
        last_page = {'subgraphDeployments': [
            {'id': '0xffff', 'signalledTokens': str(int(5e18))}
        ]}
        with patch.object(client, 'query', side_effect=[full_page, last_page]) as q:
            total = client.get_total_signalled_tokens()

        assert total == int(1005 * 1e18)
        assert q.call_count == 2
        # Second query must resume after the last id of the first page
        assert '0x03e7' in q.call_args_list[1][0][0]

    def test_returns_zero_on_failure(self):
        """Test a failed query yields 0 so the caller can fall back"""
        client = self._client()
        with patch.object(client, 'query', return_value={}):
            total = client.get_total_signalled_tokens()

        assert total == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

