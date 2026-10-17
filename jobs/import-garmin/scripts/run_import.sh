#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: ./scripts/run_import.sh [options]

Build the Garmin image and synchronize workout + sleep data into the selected
database(s), then leave the selected services running for queries. Can be
launched from any directory.

Default mode is a destination-aware DELTA: discover Garmin data in the last
30 days, compare it with the selected destination(s), and download/import only
what is missing. It never re-imports the whole cache.

  --db postgres|duckdb|both  Destination(s); default: postgres.
  --look-back-days N         Delta window size in days (positive int; default 30).
  --auth                     Log in interactively (including MFA) first.
  --no-build                 Reuse the existing local Garmin image.
  --validate                 Add deep row/value + backend-parity checks.
  --help, -h                 Show this help.

Legacy count-based mode (explicit opt-in; NOT the delta mode):
  --skip-download            Import existing local GarminDB SQLite files only.
  --latest                   Legacy incremental download, overriding .env.
  --full-download            Legacy full download, overriding .env.
  --activity-limit N         Positive legacy activity download limit.

--look-back-days cannot be combined with the legacy flags above.
--skip-download cannot be combined with authentication or download overrides.

PostgreSQL stays running for SQL clients; DuckDB stays open in its Web UI.
The DuckDB UI is stopped before the import and restarted only after success.
EOF
}

argument_error() {
    echo "ERROR: $1" >&2
    usage >&2
    exit 2
}

require_value() {
    if [[ -z "${2:-}" || "${2:-}" == -* ]]; then
        argument_error "$1 requires a value."
    fi
}

compose() {
    docker compose --project-directory "$JOB_DIR" -f "$JOB_DIR/docker-compose.yml" "$@"
}

database="postgres"
authenticate=false
skip_download=false
download_latest=""
activity_limit=""
look_back_days=""
build=true
validate=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)
            require_value "$@"
            database="$2"
            case "$database" in
                postgres|duckdb|both) ;;
                *) argument_error "--db must be postgres, duckdb, or both." ;;
            esac
            shift 2
            ;;
        --look-back-days)
            require_value "$@"
            if [[ ! "$2" =~ ^[1-9][0-9]*$ ]]; then
                argument_error "--look-back-days must be a positive integer."
            fi
            look_back_days="$2"
            shift 2
            ;;
        --auth)
            authenticate=true
            shift
            ;;
        --skip-download)
            skip_download=true
            shift
            ;;
        --latest|--full-download)
            if [[ -n "$download_latest" ]]; then
                argument_error "Use only one of --latest and --full-download."
            fi
            if [[ "$1" == "--latest" ]]; then
                download_latest=true
            else
                download_latest=false
            fi
            shift
            ;;
        --activity-limit)
            require_value "$@"
            if [[ ! "$2" =~ ^[1-9][0-9]*$ ]]; then
                argument_error "--activity-limit must be a positive integer."
            fi
            activity_limit="$2"
            shift 2
            ;;
        --no-build)
            build=false
            shift
            ;;
        --validate)
            validate=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            argument_error "Unknown option: $1"
            ;;
    esac
done

legacy_selected=false
if [[ "$skip_download" == true || -n "$download_latest" || -n "$activity_limit" ]]; then
    legacy_selected=true
fi

if [[ -n "$look_back_days" && "$legacy_selected" == true ]]; then
    argument_error "--look-back-days is a delta-mode option and cannot be combined with --skip-download, --latest, --full-download, or --activity-limit."
fi

if [[ "$skip_download" == true && ( "$authenticate" == true || -n "$download_latest" || -n "$activity_limit" ) ]]; then
    argument_error "--skip-download cannot be combined with --auth, --latest, --full-download, or --activity-limit."
fi

mode="delta"
if [[ "$legacy_selected" == true ]]; then
    mode="legacy"
fi

stage="checking prerequisites"
trap 'status=$?; echo "ERROR: Garmin import failed while $stage (exit $status)." >&2; exit "$status"' ERR

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker is not installed or not available on PATH." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: Docker Compose is not available." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running or the Docker daemon is not accessible." >&2
    exit 1
fi

stage="preparing the environment file"
if [[ ! -f "$JOB_DIR/.env" ]]; then
    cp "$JOB_DIR/.env.example" "$JOB_DIR/.env"
    echo "Created $JOB_DIR/.env from .env.example"
fi

if [[ "$build" == true ]]; then
    stage="building the Garmin image"
    echo "Building the shared Garmin image..."
    # Auth, sync/import, validation, and the UI all use this same image.
    compose build import-garmin-duckdb
else
    stage="checking the existing Garmin image"
    image="$(compose config --images import-garmin-duckdb)"
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "ERROR: The Garmin image is not available locally. Run without --no-build first." >&2
        exit 1
    fi
fi

if [[ "$authenticate" == true ]]; then
    stage="authenticating with Garmin"
    echo "Starting interactive Garmin authentication..."
    compose run --rm --no-deps garmin-auth
fi

release_duckdb_lock() {
    if [[ "$database" != "postgres" ]]; then
        stage="stopping the DuckDB UI to release its database lock"
        echo "Stopping the DuckDB UI before import..."
        compose --profile duckdb-ui stop duckdb-ui
    fi
}

start_postgres_if_selected() {
    if [[ "$database" != "duckdb" ]]; then
        stage="starting PostgreSQL"
        bash "$SCRIPT_DIR/run_database.sh" --postgres --no-build
    fi
}

start_duckdb_ui_if_selected() {
    if [[ "$database" != "postgres" ]]; then
        stage="starting the DuckDB UI"
        bash "$SCRIPT_DIR/run_database.sh" --duckdb --no-build
    fi
}

if [[ "$mode" == "delta" ]]; then
    echo "Running destination-aware delta sync (mode: delta, db: $database)..."
    # Database readiness and the DuckDB lock release happen BEFORE planning so a
    # read-only inventory connection cannot conflict with the UI's writer.
    release_duckdb_lock
    start_postgres_if_selected

    stage="running the destination-aware delta sync"
    sync_args=(run --rm --no-deps -T -e "GARMIN_SYNC_BACKENDS=$database")
    if [[ -n "$look_back_days" ]]; then
        sync_args+=(-e "GARMIN_LOOK_BACK_DAYS=$look_back_days")
    fi
    if [[ "$validate" == true ]]; then
        sync_args+=(-e "GARMIN_VALIDATE=true")
    fi
    sync_args+=(sync-garmin)
    echo "Synchronizing missing data into $database..."
    compose "${sync_args[@]}"

    start_duckdb_ui_if_selected
else
    echo "Running legacy count-based import (mode: legacy, db: $database)..."
    if [[ "$skip_download" == false ]]; then
        stage="refreshing the Garmin source"
        echo "Refreshing GarminDB SQLite data (legacy download)..."
        download_args=(run --rm --no-deps -T)
        if [[ -n "$download_latest" ]]; then
            download_args+=(-e "GARMIN_DOWNLOAD_LATEST=$download_latest")
        fi
        if [[ -n "$activity_limit" ]]; then
            download_args+=(
                -e "GARMIN_DOWNLOAD_LATEST_ACTIVITIES=$activity_limit"
                -e "GARMIN_DOWNLOAD_ALL_ACTIVITIES=$activity_limit"
            )
        fi
        compose "${download_args[@]}" refresh-garmin-source
    else
        echo "Skipping download; using existing GarminDB SQLite data."
    fi

    release_duckdb_lock
    start_postgres_if_selected

    backends=("$database")
    if [[ "$database" == "both" ]]; then
        backends=(duckdb postgres)
    fi
    for backend in "${backends[@]}"; do
        stage="importing $backend data"
        echo "Importing into $backend (legacy full cache)..."
        compose run --rm --no-deps -T -e "GARMIN_IMPORT_MODE=legacy" "import-garmin-$backend"
    done

    if [[ "$validate" == true ]]; then
        stage="validating the imported data"
        echo "Validating $database against the SQLite source..."
        compose run --rm --no-deps -T validate-garmin --backend "$database"
    fi

    start_duckdb_ui_if_selected
fi

echo
echo "Garmin import complete. Selected database services remain running."
