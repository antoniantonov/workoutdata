"""Test suite for users.py refactoring.

Verifies that database operations are properly delegated to
database-specific modules based on configuration.
"""
import sys
from pathlib import Path
import tempfile

# Add repository root to path (parent of 'polar' package)
sys.path.insert(0, str(Path(__file__).parent.parent))

from unittest.mock import Mock, patch, MagicMock


def test_duckdb_delegation():
    """Test that DuckDB operations are delegated to polar.storage.duckdb module."""
    from polar.api.users import _get_db_module, _get_db_context
    
    config = {
        'DATABASE_TYPE': 'duckdb',
        'DUCKDB_PATH': '/tmp/test.duckdb'
    }
    
    # Get module
    db_module = _get_db_module(config)
    assert db_module.__name__ == 'polar.storage.duckdb'
    
    # Get context (should be Path for DuckDB)
    db_context = _get_db_context(config)
    assert db_context == '/tmp/test.duckdb'


def test_postgres_delegation():
    """Test that PostgreSQL operations are delegated to polar.storage.postgres module."""
    from polar.api.users import _get_db_module
    
    config = {
        'DATABASE_TYPE': 'postgres',
        'POSTGRES_CONNECTION_STRING': 'postgresql://localhost/test'
    }
    
    # Get module
    db_module = _get_db_module(config)
    assert db_module.__name__ == 'polar.storage.postgres'


def test_get_default_physical_info():
    """Test that default physical info is accessible from DB modules."""
    from polar.storage import duckdb
    from polar.storage import postgres
    
    # Both modules should have the same defaults
    duckdb_defaults = duckdb.get_default_physical_info()
    postgres_defaults = postgres.get_default_physical_info()
    
    assert duckdb_defaults == postgres_defaults
    assert 'weight' in duckdb_defaults
    assert 'height' in duckdb_defaults
    assert 'maximum_heart_rate' in duckdb_defaults


def test_duckdb_userinfo_functions_exist():
    """Test that userinfo functions exist in polar.storage.duckdb module."""
    from polar.storage import duckdb
    
    assert hasattr(duckdb, 'ensure_userinfo_table')
    assert hasattr(duckdb, 'get_userinfo_from_db')
    assert hasattr(duckdb, 'save_userinfo_to_db')
    assert hasattr(duckdb, 'get_default_physical_info')


def test_postgres_userinfo_functions_exist():
    """Test that userinfo functions exist in polar.storage.postgres module."""
    from polar.storage import postgres
    
    assert hasattr(postgres, 'ensure_userinfo_table')
    assert hasattr(postgres, 'get_userinfo_from_db')
    assert hasattr(postgres, 'save_userinfo_to_db')
    assert hasattr(postgres, 'get_default_physical_info')


def test_users_module_exports():
    """Test that users.py only exports API-related functions."""
    from polar.api import users
    
    # Should export API functions
    assert hasattr(users, 'get_user_info')
    assert hasattr(users, 'register_user')
    assert hasattr(users, 'get_physical_info')
    
    # Should NOT export database functions
    assert not hasattr(users, 'ensure_userinfo_table')
    assert not hasattr(users, 'get_userinfo_from_db')
    assert not hasattr(users, 'save_userinfo_to_db')


def test_duckdb_module_exports():
    """Test that polar.storage.duckdb exports userinfo functions."""
    from polar.storage import duckdb
    
    # Check __all__ includes userinfo functions
    assert 'ensure_userinfo_table' in duckdb.__all__
    assert 'get_userinfo_from_db' in duckdb.__all__
    assert 'save_userinfo_to_db' in duckdb.__all__
    assert 'get_default_physical_info' in duckdb.__all__


def test_postgres_module_exports():
    """Test that polar.storage.postgres exports userinfo functions."""
    from polar.storage import postgres
    
    # Check __all__ includes userinfo functions
    assert 'ensure_userinfo_table' in postgres.__all__
    assert 'get_userinfo_from_db' in postgres.__all__
    assert 'save_userinfo_to_db' in postgres.__all__
    assert 'get_default_physical_info' in postgres.__all__


if __name__ == '__main__':
    # Run tests
    print("Running users.py refactoring tests...")
    
    test_duckdb_delegation()
    print("✅ DuckDB delegation test passed")
    
    test_postgres_delegation()
    print("✅ PostgreSQL delegation test passed")
    
    test_get_default_physical_info()
    print("✅ Default physical info test passed")
    
    test_duckdb_userinfo_functions_exist()
    print("✅ DuckDB userinfo functions exist")
    
    test_postgres_userinfo_functions_exist()
    print("✅ PostgreSQL userinfo functions exist")
    
    test_users_module_exports()
    print("✅ Users module exports test passed")
    
    test_duckdb_module_exports()
    print("✅ DuckDB module exports test passed")
    
    test_postgres_module_exports()
    print("✅ PostgreSQL module exports test passed")
    
    print("\n✅ All tests passed!")
