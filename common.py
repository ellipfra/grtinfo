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

