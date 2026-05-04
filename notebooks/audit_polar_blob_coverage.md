---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
    format_version: 0.13
    jupytext_version: 1.16.6
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

# Polar workout metadata/API to Azure blob coverage audit

This notebook verifies that workouts known to PostgreSQL and exercises returned by the Polar AccessLink API have corresponding CSV and TCX files in Azure Blob Storage.

It is intentionally read-only:

- Reads `workout_metadata` from PostgreSQL.
- Lists exercises from the Polar AccessLink v3 API.
- Lists Azure Blob Storage names under `polar_csv/` and `polar_tcx/`.
- Does not import, download, convert, delete, or upload any workout files.

## Setup

```{code-cell} ipython3
import sys
from pathlib import Path

# Add repo root to path
repo_root = Path.cwd().parent if 'notebooks' in str(Path.cwd()) else Path.cwd()
sys.path.insert(0, str(repo_root))

# Load environment variables from notebooks/.env when present.
try:
    from dotenv import load_dotenv

    notebook_dir = Path.cwd() if 'notebooks' in str(Path.cwd()) else Path.cwd() / 'notebooks'
    env_file = notebook_dir / '.env'
    if env_file.exists():
        load_dotenv(env_file)
        print(f"✅ Loaded environment variables from: {env_file}")
    else:
        print(f"ℹ️ No notebook .env file found at: {env_file}")
except ImportError:
    print("ℹ️ python-dotenv is not installed; using existing environment variables")

import importlib
from typing import Any

import pandas as pd
from IPython.display import display

from polar.api import exercises as exercise_api
from polar.cloud import azure as azure_storage
from polar.storage import postgres as postgres_storage
from polar.utils.common import get_field
from polar.utils.config import load_configuration

importlib.reload(exercise_api)
importlib.reload(azure_storage)
importlib.reload(postgres_storage)

config = load_configuration()

database_type = config.get('DATABASE_TYPE')
if database_type != 'postgres':
    print(f"⚠️ DATABASE_TYPE is {database_type!r}; this audit reads PostgreSQL because the requested source of truth is PG.")

azure_config = azure_storage.get_azure_storage_config()
print("✅ Configuration loaded")
print(f"  - Database type: {database_type}")
print(f"  - Azure container: {azure_config['container_name']}")
print(f"  - Polar API base: {config['API_BASE']}")
```

## Read PostgreSQL workout metadata

```{code-cell} ipython3
def load_workout_metadata_ids(config: dict[str, Any]) -> pd.DataFrame:
    """Load workout IDs from PostgreSQL workout_metadata using SELECT only."""
    conn = postgres_storage.get_postgres_connection(config)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'workout_metadata'
                )
            """)
            table_exists = cur.fetchone()[0]

            if not table_exists:
                raise RuntimeError("PostgreSQL table workout_metadata does not exist.")

            cur.execute('SELECT "workoutId" FROM workout_metadata ORDER BY "workoutId"')
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()


metadata_ids_df = load_workout_metadata_ids(config)
db_workout_ids = set(metadata_ids_df['workoutId'].dropna().astype(str))

print(f"✅ Loaded {len(metadata_ids_df)} workout_metadata row(s)")
display(metadata_ids_df.head())
```

## Read all exercises from Polar AccessLink

```{code-cell} ipython3
def build_api_exercise_audit_rows(exercises: list[dict[str, Any]]) -> pd.DataFrame:
    """Build an exercise-level table with derived workout IDs."""
    rows = []
    columns = [
        'exercise_id',
        'start_time',
        'workoutId',
        'workout_id_parse_error',
    ]

    for exercise in exercises:
        exercise_id = get_field(exercise, 'id', 'exercise_id')
        start_time = get_field(
            exercise,
            'start_time',
            'start-time',
            'local_start_time',
            'local-start-time',
        )

        workout_id = None
        parse_error = None
        if start_time:
            try:
                workout_id = exercise_api.generate_workout_id_from_start_time(str(start_time))
            except Exception as exc:
                parse_error = str(exc)
        else:
            parse_error = "Missing start time"

        rows.append({
            'exercise_id': exercise_id,
            'start_time': start_time,
            'workoutId': workout_id,
            'workout_id_parse_error': parse_error,
        })

    return pd.DataFrame(rows, columns=columns)


exercises = exercise_api.list_exercises(
    access_token=str(config['ACCESS_TOKEN']),
    api_base=str(config['API_BASE']),
)

api_exercises_df = build_api_exercise_audit_rows(exercises)
api_workout_ids = set(api_exercises_df['workoutId'].dropna().astype(str)) if not api_exercises_df.empty else set()

print(f"✅ Loaded {len(api_exercises_df)} exercise(s) from Polar API")
print(f"✅ Derived {len(api_workout_ids)} unique workoutId value(s) from API exercises")

parse_failures_df = api_exercises_df[api_exercises_df['workout_id_parse_error'].notna()]
if not parse_failures_df.empty:
    print(f"⚠️ Could not derive workoutId for {len(parse_failures_df)} API exercise(s)")
    display(parse_failures_df)
```

## List Azure CSV and TCX blobs

```{code-cell} ipython3
CSV_PREFIX = 'polar_csv/'
TCX_PREFIX = 'polar_tcx/'


def extract_workout_ids_from_blobs(
    blob_names: list[str],
    prefix: str,
    suffix: str,
) -> tuple[set[str], list[str]]:
    """Extract workout IDs from exact prefix/name/suffix blob patterns."""
    workout_ids = set()
    ignored = []

    for blob_name in blob_names:
        if not blob_name.startswith(prefix) or not blob_name.endswith(suffix):
            ignored.append(blob_name)
            continue

        remainder = blob_name[len(prefix):]
        if '/' in remainder:
            ignored.append(blob_name)
            continue

        workout_id = remainder[:-len(suffix)]
        if workout_id:
            workout_ids.add(workout_id)
        else:
            ignored.append(blob_name)

    return workout_ids, ignored


if not azure_storage.is_azure_storage_enabled():
    raise RuntimeError(
        "Azure Storage is disabled. Set AZURE_STORAGE_ENABLED=true and AZURE_STORAGE_ACCOUNT_NAME before running this audit."
    )

container_name = azure_config['container_name']

csv_blobs = azure_storage.list_azure_storage_blobs(container_name=container_name, prefix=CSV_PREFIX)
tcx_blobs = azure_storage.list_azure_storage_blobs(container_name=container_name, prefix=TCX_PREFIX)

csv_blob_workout_ids, ignored_csv_blobs = extract_workout_ids_from_blobs(csv_blobs, CSV_PREFIX, '.csv')
tcx_blob_workout_ids, ignored_tcx_blobs = extract_workout_ids_from_blobs(tcx_blobs, TCX_PREFIX, '.tcx')

print(f"✅ Listed {len(csv_blobs)} blob(s) under {CSV_PREFIX}")
print(f"✅ Listed {len(tcx_blobs)} blob(s) under {TCX_PREFIX}")
print(f"✅ Parsed {len(csv_blob_workout_ids)} CSV workoutId value(s)")
print(f"✅ Parsed {len(tcx_blob_workout_ids)} TCX workoutId value(s)")

if ignored_csv_blobs:
    print(f"⚠️ Ignored {len(ignored_csv_blobs)} CSV blob(s) that did not match {CSV_PREFIX}<workoutId>.csv")
if ignored_tcx_blobs:
    print(f"⚠️ Ignored {len(ignored_tcx_blobs)} TCX blob(s) that did not match {TCX_PREFIX}<workoutId>.tcx")
```

## Build coverage tables

```{code-cell} ipython3
def status_from_missing(missing: list[str]) -> str:
    return 'ok' if not missing else ', '.join(missing)


api_counts_by_workout_id = (
    api_exercises_df.dropna(subset=['workoutId'])
    .groupby('workoutId')
    .size()
    .to_dict()
    if not api_exercises_df.empty
    else {}
)

all_workout_ids = sorted(db_workout_ids | api_workout_ids)
coverage_columns = [
    'workoutId',
    'in_workout_metadata',
    'api_exercise_count',
    'in_polar_api',
    'csv_blob_found',
    'tcx_blob_found',
    'status',
]

coverage_rows = []
for workout_id in all_workout_ids:
    in_workout_metadata = workout_id in db_workout_ids
    in_polar_api = workout_id in api_workout_ids
    csv_blob_found = workout_id in csv_blob_workout_ids
    tcx_blob_found = workout_id in tcx_blob_workout_ids

    missing = []
    if not in_workout_metadata:
        missing.append('missing_metadata')
    if not in_polar_api:
        missing.append('not_returned_by_api')
    if not csv_blob_found:
        missing.append('missing_csv')
    if not tcx_blob_found:
        missing.append('missing_tcx')

    coverage_rows.append({
        'workoutId': workout_id,
        'in_workout_metadata': in_workout_metadata,
        'api_exercise_count': api_counts_by_workout_id.get(workout_id, 0),
        'in_polar_api': in_polar_api,
        'csv_blob_found': csv_blob_found,
        'tcx_blob_found': tcx_blob_found,
        'status': status_from_missing(missing),
    })

coverage_df = pd.DataFrame(coverage_rows, columns=coverage_columns)
metadata_coverage_df = (
    coverage_df[coverage_df['in_workout_metadata']].reset_index(drop=True)
    if not coverage_df.empty
    else pd.DataFrame(columns=coverage_columns)
)

api_audit_df = api_exercises_df.copy()
if not api_audit_df.empty:
    api_audit_df['in_workout_metadata'] = api_audit_df['workoutId'].isin(db_workout_ids)
    api_audit_df['csv_blob_found'] = api_audit_df['workoutId'].isin(csv_blob_workout_ids)
    api_audit_df['tcx_blob_found'] = api_audit_df['workoutId'].isin(tcx_blob_workout_ids)

    api_statuses = []
    for _, row in api_audit_df.iterrows():
        missing = []
        if pd.notna(row['workout_id_parse_error']):
            missing.append('invalid_workout_id')
        if not row['in_workout_metadata']:
            missing.append('missing_metadata')
        if not row['csv_blob_found']:
            missing.append('missing_csv')
        if not row['tcx_blob_found']:
            missing.append('missing_tcx')
        api_statuses.append(status_from_missing(missing))

    api_audit_df['status'] = api_statuses
else:
    api_audit_df = pd.DataFrame(columns=[
        'exercise_id',
        'start_time',
        'workoutId',
        'workout_id_parse_error',
        'in_workout_metadata',
        'csv_blob_found',
        'tcx_blob_found',
        'status',
    ])

print(f"✅ Built coverage table with {len(coverage_df)} unique workoutId value(s)")
print(f"✅ Metadata coverage rows: {len(metadata_coverage_df)}")
print(f"✅ API exercise audit rows: {len(api_audit_df)}")
```

## Summary and issue tables

```{code-cell} ipython3
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 160)


def show_issue_table(title: str, df: pd.DataFrame, empty_message: str) -> None:
    print()
    print(title)
    print("=" * len(title))
    if df.empty:
        print(empty_message)
    else:
        display(df)


def sql_literal(value: object) -> str:
    """Return a PostgreSQL-safe single-quoted literal for copyable SQL output."""
    return "'" + str(value).replace("'", "''") + "'"


def print_metadata_delete_sql(missing_metadata_blobs_df: pd.DataFrame) -> None:
    """Print a copyable PostgreSQL DELETE statement without executing it."""
    print()
    print("Copyable PostgreSQL cleanup query for these missing storage files")
    print("=" * 65)

    missing_workout_ids = sorted(
        missing_metadata_blobs_df['workoutId']
        .dropna()
        .astype(str)
        .unique()
    )

    if not missing_workout_ids:
        print("-- No workout_metadata rows need deletion.")
        return

    workout_id_literals = ",\n    ".join(sql_literal(workout_id) for workout_id in missing_workout_ids)
    delete_sql = f"""-- Review this list before executing.
-- This deletes only PostgreSQL metadata rows whose CSV or TCX blob is missing.
BEGIN;

DELETE FROM workout_metadata
WHERE "workoutId" IN (
    {workout_id_literals}
);

COMMIT;"""

    print(delete_sql)


metadata_missing_csv_df = metadata_coverage_df[metadata_coverage_df['csv_blob_found'].eq(False)]
metadata_missing_tcx_df = metadata_coverage_df[metadata_coverage_df['tcx_blob_found'].eq(False)]
metadata_missing_any_blob_df = metadata_coverage_df[
    metadata_coverage_df['csv_blob_found'].eq(False) | metadata_coverage_df['tcx_blob_found'].eq(False)
]
api_missing_metadata_df = api_audit_df[api_audit_df['in_workout_metadata'].eq(False)] if not api_audit_df.empty else api_audit_df
api_missing_any_blob_df = api_audit_df[
    api_audit_df['csv_blob_found'].eq(False) | api_audit_df['tcx_blob_found'].eq(False)
] if not api_audit_df.empty else api_audit_df
duplicate_api_workout_ids_df = (
    api_exercises_df.dropna(subset=['workoutId'])
    .groupby('workoutId')
    .size()
    .reset_index(name='api_exercise_count')
    .query('api_exercise_count > 1')
    if not api_exercises_df.empty
    else pd.DataFrame(columns=['workoutId', 'api_exercise_count'])
)

summary_df = pd.DataFrame([{
    'metadata_rows': len(metadata_ids_df),
    'api_exercises': len(api_exercises_df),
    'unique_metadata_workoutIds': len(db_workout_ids),
    'unique_api_workoutIds': len(api_workout_ids),
    'csv_blob_workoutIds': len(csv_blob_workout_ids),
    'tcx_blob_workoutIds': len(tcx_blob_workout_ids),
    'metadata_missing_csv': len(metadata_missing_csv_df),
    'metadata_missing_tcx': len(metadata_missing_tcx_df),
    'api_missing_metadata': len(api_missing_metadata_df),
    'api_missing_csv_or_tcx': len(api_missing_any_blob_df),
    'duplicate_api_workoutIds': len(duplicate_api_workout_ids_df),
}])

print("📊 Audit summary")
display(summary_df)

show_issue_table(
    "PostgreSQL metadata rows missing CSV or TCX blobs",
    metadata_missing_any_blob_df[['workoutId', 'csv_blob_found', 'tcx_blob_found', 'in_polar_api', 'status']],
    "✅ Every PostgreSQL metadata workoutId has both CSV and TCX blobs.",
)
print_metadata_delete_sql(metadata_missing_any_blob_df)

show_issue_table(
    "Polar API exercises missing PostgreSQL metadata",
    api_missing_metadata_df[['exercise_id', 'start_time', 'workoutId', 'status']],
    "✅ Every API exercise with a derived workoutId exists in PostgreSQL metadata.",
)

show_issue_table(
    "Polar API exercises missing CSV or TCX blobs",
    api_missing_any_blob_df[['exercise_id', 'start_time', 'workoutId', 'csv_blob_found', 'tcx_blob_found', 'status']],
    "✅ Every API exercise has both CSV and TCX blobs.",
)

show_issue_table(
    "Duplicate API-derived workoutId values",
    duplicate_api_workout_ids_df,
    "✅ No duplicate API-derived workoutId values found.",
)
```

## Full audit tables

```{code-cell} ipython3
print("Full PostgreSQL metadata coverage table")
display(metadata_coverage_df[['workoutId', 'in_polar_api', 'csv_blob_found', 'tcx_blob_found', 'status']])

print("Full Polar API exercise audit table")
api_display_columns = [
    'exercise_id',
    'start_time',
    'workoutId',
    'in_workout_metadata',
    'csv_blob_found',
    'tcx_blob_found',
    'status',
]
display(api_audit_df[api_display_columns] if not api_audit_df.empty else api_audit_df)
```
