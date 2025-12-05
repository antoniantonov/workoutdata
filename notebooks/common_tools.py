"""Common utility functions for Polar AccessLink workflow.

This module provides shared helper functions used across multiple modules.
"""
from __future__ import annotations

from typing import Dict, Optional


def get_field(data: Dict[str, object], *keys: str) -> Optional[object]:
    """Extract field from dictionary trying multiple possible key names.
    
    Args:
        data: Dictionary to extract field from (e.g., exercise dict)
        *keys: Key names to try in order
    
    Returns:
        Value if found, None otherwise
    """
    for key in keys:
        if key in data:
            return data[key]
    return None


__all__ = [
    'get_field',
]
