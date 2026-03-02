"""GarminDB data import module.

This subpackage provides functions to read health data from GarminDB SQLite
databases and import it into DuckDB or PostgreSQL.

GarminDB (https://github.com/tcgoetz/GarminDB) stores Garmin Connect data in
three SQLite databases:
- garmin.db          : sleep, resting heart rate, daily summaries
- garmin_monitoring.db : continuous (all-day) heart rate monitoring data
- garmin_activities.db : recorded activities with GPS coordinates

Usage::

    from polar.garmin import import_all_garmin_data
    from polar.utils.config import load_configuration

    config = load_configuration()
    stats = import_all_garmin_data('/path/to/garmin/dbs', config)
"""

from polar.garmin.reader import (
    read_heart_rate,
    read_sleep,
    read_activities,
    read_daily_summary,
)
from polar.garmin.importer import (
    import_garmin_heart_rate,
    import_garmin_sleep,
    import_garmin_activities,
    import_garmin_daily_summary,
    import_all_garmin_data,
)

__all__ = [
    # Reader functions
    'read_heart_rate',
    'read_sleep',
    'read_activities',
    'read_daily_summary',
    # Importer functions
    'import_garmin_heart_rate',
    'import_garmin_sleep',
    'import_garmin_activities',
    'import_garmin_daily_summary',
    'import_all_garmin_data',
]
