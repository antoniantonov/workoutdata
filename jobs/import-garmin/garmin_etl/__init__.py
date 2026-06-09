"""Garmin ETL package: transform GarminDB SQLite data into DuckDB / PostgreSQL.

Exposes a configuration loader, a transform layer that reads the GarminDB SQLite
databases, an optional download layer (GarminDB CLI), and two storage backends
(DuckDB and PostgreSQL) selectable via the ``DATABASE_TYPE`` environment variable.
"""
