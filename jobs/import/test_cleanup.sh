#!/usr/bin/env bash
# test_cleanup.sh — Remove the last N workout entries from the database.
#
# Usage:
#   ./test_cleanup.sh --db duckdb          # Remove from local DuckDB
#   ./test_cleanup.sh --db postgres        # Remove from online PostgreSQL
#   ./test_cleanup.sh --db duckdb -n 3     # Remove last 3 entries from DuckDB
#
# This script is for TESTING PURPOSES ONLY.
# It is excluded from the Docker build image.

set -euo pipefail

# Defaults
DB_TYPE=""
NUM_ENTRIES=2
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
        -h|--help)
            echo "Usage: $0 --db <duckdb|postgres> [-n <num_entries>]"
            echo ""
            echo "Options:"
            echo "  --db <type>    Database type: 'duckdb' or 'postgres' (required)"
            echo "  -n <num>       Number of last entries to remove (default: 2)"
            echo "  -h, --help     Show this help message"
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
    echo "Usage: $0 --db <duckdb|postgres> [-n <num_entries>]"
    exit 1
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
echo "🗑️  Removing last ${NUM_ENTRIES} workout entries from ${DB_TYPE_UPPER} database..."
echo "================================================================"

if [[ "$DB_TYPE" == "duckdb" ]]; then
    DUCKDB_FILE="${SCRIPT_DIR}/local_data/database_v2.duckdb"

    if [[ ! -f "$DUCKDB_FILE" ]]; then
        echo "❌ Error: DuckDB file not found at: $DUCKDB_FILE"
        exit 1
    fi

    echo "📂 DuckDB file: $DUCKDB_FILE"

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

elif [[ "$DB_TYPE" == "postgres" ]]; then
    # Build connection string from .env variables
    PG_HOST="${POSTGRES_HOST:?POSTGRES_HOST not set}"
    PG_PORT="${POSTGRES_PORT:-5432}"
    PG_DB="${POSTGRES_DATABASE:?POSTGRES_DATABASE not set}"
    PG_USER="${POSTGRES_USER:?POSTGRES_USER not set}"
    PG_PASS="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set}"

    CONN_STR="host=${PG_HOST} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER} password=${PG_PASS} sslmode=require"

    echo "📂 PostgreSQL: ${PG_HOST}:${PG_PORT}/${PG_DB}"

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

else
    echo "❌ Error: Invalid database type '${DB_TYPE}'. Use 'duckdb' or 'postgres'."
    exit 1
fi
