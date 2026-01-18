"""Validation utilities for Polar AccessLink workflow.

This module provides utilities for validating tokens and environment configuration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from polar.api.tokens import is_token_valid, load_tokens


def run_validation_checks(
    tokens_file: Path = Path("tokens_polar.json"),
    required_env_vars: List[str] = None
) -> bool:
    """Run validation checks on tokens file and environment variables.
    
    Args:
        tokens_file: Path to token storage file
        required_env_vars: List of required environment variable names
    
    Returns:
        True if all checks pass, False otherwise
    """
    if required_env_vars is None:
        required_env_vars = ['POLAR_CLIENT_ID', 'POLAR_CLIENT_SECRET']
    
    print("Running validation checks...\n")

    validation_passed = True

    # Check 1: Tokens file exists
    if tokens_file.exists():
        print("✅ Tokens file exists")
        
        # Check 2: Tokens file is valid JSON
        try:
            tokens = load_tokens(tokens_file)
            print("✅ Tokens file is valid JSON")
            
            # Check 3: Required fields present
            required_fields = ['access_token', 'token_type']
            for field in required_fields:
                if field in tokens and tokens[field]:
                    print(f"✅ Field '{field}' present")
                else:
                    print(f"❌ Field '{field}' missing or empty")
                    validation_passed = False
            
            # Check 4: Optional refresh_token
            if 'refresh_token' in tokens and tokens['refresh_token']:
                print(f"✅ Refresh token available")
            else:
                print(f"⚠️ Refresh token not available (optional)")
            
            # Check 5: Token format (basic validation)
            if len(tokens['access_token']) > 10:
                print(f"✅ Access token format looks valid")
            else:
                print(f"⚠️  Access token seems too short")
                validation_passed = False
                
        except json.JSONDecodeError:
            print("❌ Tokens file is not valid JSON")
            validation_passed = False
    else:
        print("❌ Tokens file does not exist")
        print("   Run authorization flow to obtain tokens")
        validation_passed = False

    # Check 6: Environment variables
    for var in required_env_vars:
        if os.getenv(var):
            print(f"✅ Environment variable {var} is set")
        else:
            print(f"❌ Environment variable {var} is not set")
            validation_passed = False

    # Summary
    print("\n" + "="*80)
    if validation_passed:
        print("✅ ALL VALIDATION CHECKS PASSED")
    else:
        print("⚠️  SOME VALIDATION CHECKS FAILED")
    print("="*80)
    
    return validation_passed


__all__ = [
    'is_token_valid',
    'run_validation_checks',
]
