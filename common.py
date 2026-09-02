#!/usr/bin/env python3
"""
Shared utilities for grtinfo CLI tools

This module contains common formatting functions, colors, and utilities
used across subinfo, indexerinfo, and delegatorinfo.
"""

import os
import re
from datetime import datetime
from typing import Optional


class Colors:
    """ANSI color codes for terminal output"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Standard colors
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Bright colors
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'


def terminal_link(url: str, text: str) -> str:
    """Create a clickable terminal hyperlink (OSC 8)
    
    Can be disabled by setting NO_HYPERLINKS=1 environment variable
    """
    if os.environ.get('NO_HYPERLINKS') == '1':
        return text
    return f'\033]8;;{url}\033\\{text}\033]8;;\033\\'


def format_deployment_link(ipfs_hash: str, subgraph_id: Optional[str] = None) -> str:
    """Format IPFS hash as a clickable link to The Graph Explorer"""
    if subgraph_id:
        url = f"https://thegraph.com/explorer/subgraphs/{subgraph_id}?view=Query&chain=arbitrum-one"
        return terminal_link(url, ipfs_hash)
    return ipfs_hash


def format_tokens(tokens: str) -> str:
    """Format token amount with thousands separator
    
    Args:
        tokens: Token amount as string (in wei, 18 decimals)
    
    Returns:
        Formatted string like "1,234,567 GRT"
    """
    try:
        amount = float(tokens) / 1e18
        if amount >= 1:
            return f"{amount:,.0f} GRT"
        elif amount > 0:
            return f"{amount:.2f} GRT"
        elif amount < 0:
            return f"{amount:,.0f} GRT"
        else:
            return "0 GRT"
    except:
        return "0 GRT"


def format_tokens_short(tokens: str) -> str:
    """Format token amount in short form (k, M)
    
    Args:
        tokens: Token amount as string (in wei, 18 decimals)
    
    Returns:
        Formatted string like "1.2M" or "456k"
    """
    try:
        amount = float(tokens) / 1e18
        if amount >= 1_000_000:
            return f"{amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            return f"{amount/1_000:.0f}k"
        elif amount >= 1:
            return f"{amount:,.0f}"
        else:
            return f"{amount:.2f}"
    except:
        return "0"


def format_percentage(value: float) -> str:
    """Format a PPM (parts per million) value as percentage"""
    return f"{value / 10000:.2f}%"


def format_timestamp(ts: str) -> str:
    """Format Unix timestamp to readable date string"""
    try:
        dt = datetime.fromtimestamp(int(ts))
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return 'Unknown'


def format_duration(seconds: float) -> str:
    """Format duration in human readable format
    
    Args:
        seconds: Duration in seconds (can be int or float)
    
    Returns:
        Formatted string like "3d 5h", "2h", "45m", "30s"
    """
    seconds = int(seconds)
    if seconds < 0:
        return "expired"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        if minutes > 0 and hours < 12:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{seconds}s"


def allocation_anchor(alloc: dict) -> tuple:
    """Return the reference point from which an allocation's staleness runs.

    Rewards accrue (and staleness is measured) from the most recent of the
    allocation creation and its last POI presentation. Displaying the raw
    creation date for an allocation that has presented POIs since is
    misleading: what matters is how long ago the last POI was presented.

    Args:
        alloc: allocation dict with ``createdAt`` and, when available,
            ``poiCount`` and ``latestPoiPresentedAt``.

    Returns:
        Tuple ``(timestamp, kind)`` where ``kind`` is ``'poi'`` when the anchor
        is the last POI presentation, ``'created'`` when no POI was presented.
    """
    created_at = int(alloc.get('createdAt') or 0)
    poi_count = int(alloc.get('poiCount') or 0)
    latest_poi = int(alloc.get('latestPoiPresentedAt') or 0)

    if poi_count > 0 and latest_poi > created_at:
        return latest_poi, 'poi'
    return created_at, 'created'


def allocation_deadline(alloc: dict, max_poi_staleness_seconds: int,
                        max_allocation_seconds: int) -> tuple:
    """Compute when an allocation stops earning indexing rewards.

    Since the Horizon upgrade the allocation creation date no longer bounds its
    lifetime. A Horizon allocation keeps earning as long as its indexer presents
    a fresh POI within ``maxPOIStaleness``; each POI presentation resets the
    clock. Legacy (pre-Horizon) allocations still expire ``maxAllocationEpochs``
    after creation.

    Args:
        alloc: allocation dict with at least ``createdAt`` and, when available,
            ``isLegacy``, ``poiCount`` and ``latestPoiPresentedAt``.
        max_poi_staleness_seconds: SubgraphService.maxPOIStaleness() in seconds.
        max_allocation_seconds: legacy max allocation lifetime in seconds.

    Returns:
        Tuple ``(deadline_ts, anchor_kind)`` where ``deadline_ts`` is the Unix
        timestamp at which rewards stop, and ``anchor_kind`` is one of
        ``'poi'`` (anchored on the last POI), ``'created-horizon'`` (Horizon
        allocation that never presented a POI) or ``'created-legacy'``.
    """
    created_at = int(alloc.get('createdAt') or 0)

    # Legacy allocations keep the old creation-based expiry.
    if alloc.get('isLegacy'):
        return created_at + max_allocation_seconds, 'created-legacy'

    # Horizon: staleness runs from the last POI, or from creation when no POI
    # has been presented yet, mirroring the contract behaviour.
    anchor_ts, anchor_kind = allocation_anchor(alloc)
    if anchor_kind == 'poi':
        return anchor_ts + max_poi_staleness_seconds, 'poi'
    return created_at + max_poi_staleness_seconds, 'created-horizon'


def format_time_left(seconds_left: float, colors: type = Colors) -> str:
    """Format the time remaining before an allocation goes stale, with color.

    Green when comfortably in the future, yellow within a week, red within two
    days, and a bright red ``stale`` once the deadline has passed.
    """
    if seconds_left <= 0:
        return f"{colors.BRIGHT_RED}stale{colors.RESET}"

    days = seconds_left / 86400
    if days <= 2:
        color = colors.BRIGHT_RED
    elif days <= 7:
        color = colors.BRIGHT_YELLOW
    else:
        color = colors.BRIGHT_GREEN

    return f"{color}{format_duration(seconds_left)} left{colors.RESET}"


# =============================================================================
# Rewards Eligibility Oracle (GIP-0079) display helpers
# =============================================================================
# An ineligible indexer mints no indexing rewards at all, for itself and for its
# delegators, so the reason behind the boolean matters: the oracle answers "yes"
# both when the indexer earned it and when a global fail-safe is active.

# Remaining eligibility below which we warn (the oracle renews roughly daily,
# so under 4 days means a couple of missed runs away from losing rewards).
ELIGIBILITY_WARN_SECONDS = 4 * 86400


def format_span(seconds: float) -> str:
    """Compact span used by the eligibility lines: "27h" under 2 days, else "12.9d"."""
    seconds = max(0, int(seconds))
    if seconds < 2 * 86400:
        return f"{seconds // 3600}h"
    return f"{seconds / 86400:.1f}d"


def _eligibility_detail(state: dict, now: float) -> str:
    """Uncolored parenthetical explaining why an indexer is (in)eligible."""
    reason = state.get('reason')
    renewal = state.get('renewal_time') or 0
    expires_at = state.get('expires_at')

    if reason == 'renewed':
        return (f"renewed {format_span(now - renewal)} ago, "
                f"expires in {format_span(expires_at - now)}")
    if reason == 'expired':
        return (f"renewed {format_span(now - renewal)} ago "
                f"(expired {format_span(now - expires_at)} ago)")
    if reason == 'never_renewed':
        return "never renewed by the oracle"
    if reason == 'validation_disabled':
        return "eligibility validation disabled"
    if reason == 'oracle_stale':
        stale_for = state.get('oracle_stale_for')
        stale_str = f" since {format_span(stale_for)}" if stale_for else ""
        return f"oracle stale{stale_str}: everyone eligible"
    if reason == 'no_oracle':
        return "no eligibility oracle configured"
    return ""


def format_eligibility(state: Optional[dict], style: str = 'full',
                       now: Optional[float] = None, colors: type = Colors) -> str:
    """Render an eligibility state from RewardsEligibilityClient.

    Args:
        state: eligibility dict, or None when it could not be determined
        style: 'full' (indexerinfo overview line), 'short' (delegator table tag)
               or 'compact' (subinfo allocation row marker, empty when eligible)
        now: current unix timestamp (defaults to time.time())
    Returns:
        Colored string, possibly empty.
    """
    if not state:
        return ""
    if now is None:
        now = datetime.now().timestamp()

    eligible = state.get('eligible', False)
    reason = state.get('reason')
    failsafe = reason in ('validation_disabled', 'oracle_stale', 'no_oracle')
    detail = _eligibility_detail(state, now)

    # Warn while still eligible but close to expiry: the indexer needs a renewal.
    warning = False
    if eligible and reason == 'renewed' and state.get('expires_at'):
        warning = (state['expires_at'] - now) <= ELIGIBILITY_WARN_SECONDS

    if style == 'compact':
        if eligible:
            return ""
        return f"  {colors.BRIGHT_RED}✗ ineligible{colors.RESET}"

    if style == 'short':
        if not eligible:
            # Condensed detail: the delegator table has no room for the renewal date.
            if reason == 'expired' and state.get('expires_at'):
                short_detail = f"expired {format_span(now - state['expires_at'])} ago"
            elif reason == 'never_renewed':
                short_detail = "never renewed"
            else:
                short_detail = detail
            suffix = f" ({short_detail})" if short_detail else ""
            return f"{colors.BRIGHT_RED}NOT ELIGIBLE{suffix}{colors.RESET}"
        if failsafe:
            return f"{colors.DIM}eligible{colors.RESET}"
        if warning:
            return f"{colors.BRIGHT_YELLOW}eligible ({detail}){colors.RESET}"
        return f"{colors.BRIGHT_GREEN}eligible{colors.RESET}"

    # style == 'full'
    if eligible:
        color = colors.BRIGHT_YELLOW if warning else colors.BRIGHT_GREEN
        label = f"{color}ELIGIBLE{colors.RESET}"
    else:
        label = f"{colors.BRIGHT_RED}NOT ELIGIBLE{colors.RESET}"
    if detail:
        detail_color = colors.BRIGHT_YELLOW if warning else colors.DIM
        return f"{label} {detail_color}({detail}){colors.RESET}"
    return label


def print_section(title: str, colors: type = Colors):
    """Display a compact section title with color
    
    Args:
        title: Section title text
        colors: Colors class to use (allows overriding)
    """
    print(f"\n{colors.CYAN}▸ {title}{colors.RESET}")


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def get_display_width(text: str) -> int:
    """Get display width of text without ANSI codes"""
    return len(strip_ansi(text))

