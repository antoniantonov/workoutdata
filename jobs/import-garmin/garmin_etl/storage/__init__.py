"""Storage backends for Garmin data.

Two interchangeable data layers expose the same surface:
- ``garmin_etl.storage.duckdb``
- ``garmin_etl.storage.postgres``

The active backend is chosen at runtime via ``config['DATABASE_TYPE']``.
"""
