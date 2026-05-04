#!/usr/bin/env bash
# test_cleanup.sh — Remove workout entries from the database.
#
# Usage:
#   ./test_cleanup.sh --db duckdb                          # Remove last 2 entries from DuckDB
#   ./test_cleanup.sh --db postgres -n 3                   # Remove last 3 entries from PostgreSQL
#   ./test_cleanup.sh --db duckdb --from-date 15-04-2026   # Remove all entries from 15-04-2026 onwards
#   ./test_cleanup.sh --db postgres --from-date 01-03-2026 # Remove all entries from 01-03-2026 onwards
#
# This script is for TESTING PURPOSES ONLY.
# It is excluded from the Docker build image.

set -euo pipefail

# Defaults
DB_TYPE=""
NUM_ENTRIES=2
FROM_DATE=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --db)
            DB_TYPE="$2"
            shift 2
            ;;
        -n)
            NUM_ENTRIES="$2"
            shift 2
            ;;
        --from-date)
            FROM_DATE="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 --db <duckdb|postgres> [-n <num_entries>] [--from-date <DD-MM-YYYY>]"
            echo ""
            echo "Options:"
            echo "  --db <type>         Database type: 'duckdb' or 'postgres' (required)"
            echo "  -n <num>            Number of last entries to remove (default: 2)"
            echo "  --from-date <date>  Delete all entries from this date onwards (format: DD-MM-YYYY)"
            echo "  -h, --help          Show this help message"
            echo ""
            echo "If --from-date is provided, -n is ignored."
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

if [[ -z "$DB_TYPE" ]]; then
    echo "❌ Error: --db parameter is required (duckdb or postgres)"
    echo "Usage: $0 --db <duckdb|postgres> [-n <num_entries>] [--from-date <DD-MM-YYYY>]"
    exit 1
fi

# Validate --from-date format if provided
if [[ -n "$FROM_DATE" ]]; then
    if ! [[ "$FROM_DATE" =~ ^[0-9]{2}-[0-9]{2}-[0-9]{4}$ ]]; then
        echo "❌ Error: --from-date must be in DD-MM-YYYY format (got: $FROM_DATE)"
        exit 1
    fi
fi

# Load .env file if it exists
if [[ -f "$ENV_FILE" ]]; then
    echo "📄 Loading environment from: $ENV_FILE"
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "⚠️  No .env file found at $ENV_FILE"
fi

DB_TYPE_UPPER=$(echo "$DB_TYPE" | tr '[:lower:]' '[:upper:]')

if [[ -n "$FROM_DATE" ]]; then
    echo "🗑️  Removing all workout entries from ${FROM_DATE} onwards from ${DB_TYPE_UPPER} database..."
else
    echo "🗑️  Removing last ${NUM_ENTRIES} workout entries from ${DB_TYPE_UPPER} database..."
fi
echo "================================================================"

if [[ "$DB_TYPE" == "duckdb" ]]; then
    DUCKDB_FILE="${SCRIPT_DIR}/local_data/database_v2.duckdb"

    if [[ ! -f "$DUCKDB_FILE" ]]; then
        echo "❌ Error: DuckDB file not found at: $DUCKDB_FILE"
        exit 1
    fi

    echo "📂 DuckDB file: $DUCKDB_FILE"

    if [[ -n "$FROM_DATE" ]]; then
        # Delete all entries from the given date onwards
        duckdb "$DUCKDB_FILE" <<EOF
-- Show the entries we are about to delete
SELECT workoutId, Date, Sport
FROM workout_metadata
WHERE strptime(Date, '%d-%m-%Y') >= strptime('${FROM_DATE}', '%d-%m-%Y')
ORDER BY strptime(Date, '%d-%m-%Y') ASC;

-- Delete from timeseries first
DELETE FROM timeseries
WHERE workoutId IN (
    SELECT workoutId FROM workout_metadata
    WHERE strptime(Date, '%d-%m-%Y') >= strptime('${FROM_DATE}', '%d-%m-%Y')
);

-- Delete from workout_metadata
DELETE FROM workout_metadata
WHERE strptime(Date, '%d-%m-%Y') >= strptime('${FROM_DATE}', '%d-%m-%Y');

-- Confirm remaining count
SELECT 'Remaining workouts:' AS status, COUNT(*) AS count FROM workout_metadata;
EOF
        echo ""
        echo "✅ Deleted all entries from ${FROM_DATE} onwards from DuckDB."
    else
        # Find last N workout IDs and delete them
        duckdb "$DUCKDB_FILE" <<EOF
-- Show the entries we are about to delete
SELECT workoutId, Date, Sport
FROM workout_metadata
ORDER BY strptime(Date, '%d-%m-%Y') DESC
LIMIT ${NUM_ENTRIES};

-- Delete from timeseries first
DELETE FROM timeseries
WHERE workoutId IN (
    SELECT workoutId FROM workout_metadata
    ORDER BY strptime(Date, '%d-%m-%Y') DESC
    LIMIT ${NUM_ENTRIES}
);

-- Delete from workout_metadata
DELETE FROM workout_metadata
WHERE workoutId IN (
    SELECT workoutId FROM workout_metadata
    ORDER BY strptime(Date, '%d-%m-%Y') DESC
    LIMIT ${NUM_ENTRIES}
);

-- Confirm remaining count
SELECT 'Remaining workouts:' AS status, COUNT(*) AS count FROM workout_metadata;
EOF
        echo ""
        echo "✅ Deleted last ${NUM_ENTRIES} entries from DuckDB."
    fi

elif [[ "$DB_TYPE" == "postgres" ]]; then
    # Build connection string from .env variables
    PG_HOST="${POSTGRES_HOST:?POSTGRES_HOST not set}"
    PG_PORT="${POSTGRES_PORT:-5432}"
    PG_DB="${POSTGRES_DATABASE:?POSTGRES_DATABASE not set}"
    PG_USER="${POSTGRES_USER:?POSTGRES_USER not set}"
    PG_PASS="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set}"

    CONN_STR="host=${PG_HOST} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER} password=${PG_PASS} sslmode=require"

    echo "📂 PostgreSQL: ${PG_HOST}:${PG_PORT}/${PG_DB}"

    if [[ -n "$FROM_DATE" ]]; then
        # Delete all entries from the given date onwards
        psql "$CONN_STR" <<EOF
-- Show the entries we are about to delete
SELECT "workoutId", "Date", "Sport"
FROM workout_metadata
WHERE to_date("Date", 'DD-MM-YYYY') >= to_date('${FROM_DATE}', 'DD-MM-YYYY')
ORDER BY to_date("Date", 'DD-MM-YYYY') ASC;

-- Delete from timeseries first
DELETE FROM timeseries
WHERE "workoutId" IN (
    SELECT "workoutId" FROM workout_metadata
    WHERE to_date("Date", 'DD-MM-YYYY') >= to_date('${FROM_DATE}', 'DD-MM-YYYY')
);

-- Delete from workout_metadata
DELETE FROM workout_metadata
WHERE to_date("Date", 'DD-MM-YYYY') >= to_date('${FROM_DATE}', 'DD-MM-YYYY');

-- Confirm remaining count
SELECT 'Remaining workouts:' AS status, COUNT(*) AS count FROM workout_metadata;
EOF
        echo ""
        echo "✅ Deleted all entries from ${FROM_DATE} onwards from PostgreSQL."
    else
        psql "$CONN_STR" <<EOF
-- Show the entries we are about to delete
SELECT "workoutId", "Date", "Sport"
FROM workout_metadata
ORDER BY to_date("Date", 'DD-MM-YYYY') DESC
LIMIT ${NUM_ENTRIES};

-- Delete from timeseries first
DELETE FROM timeseries
WHERE "workoutId" IN (
    SELECT "workoutId" FROM workout_metadata
    ORDER BY to_date("Date", 'DD-MM-YYYY') DESC
    LIMIT ${NUM_ENTRIES}
);

-- Delete from workout_metadata
DELETE FROM workout_metadata
WHERE "workoutId" IN (
    SELECT "workoutId" FROM workout_metadata
    ORDER BY to_date("Date", 'DD-MM-YYYY') DESC
    LIMIT ${NUM_ENTRIES}
);

-- Confirm remaining count
SELECT 'Remaining workouts:' AS status, COUNT(*) AS count FROM workout_metadata;
EOF
        echo ""
        echo "✅ Deleted last ${NUM_ENTRIES} entries from PostgreSQL."
    fi

else
    echo "❌ Error: Invalid database type '${DB_TYPE}'. Use 'duckdb' or 'postgres'."
    exit 1
fi
