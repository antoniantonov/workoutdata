#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: ./scripts/run_import.sh [options]

Build the Garmin image, refresh the source, import data, and leave the selected
database services running for queries. Can be launched from any directory.

  --db postgres|duckdb|both  Import destination(s); default: postgres.
  --auth                    Log in interactively (including MFA) before download.
  --skip-download           Import existing local GarminDB SQLite files only.
  --latest                  Download incrementally, overriding .env.
  --full-download           Request a full download, overriding .env.
  --activity-limit N        Positive activity download limit (latest and full).
  --no-build                Reuse the existing local Garmin image.
  --validate                Compare the selected database(s) with the source.
  --help, -h                Show this help.

Without download overrides, settings come from jobs/import-garmin/.env.
The example configuration downloads incrementally (25 latest activities).
Full downloads also have a limit: 1000 activities in the example configuration.
--skip-download cannot be combined with authentication or download overrides.

PostgreSQL stays running for SQL clients; DuckDB stays open in its Web UI.
The DuckDB UI is stopped before import and restarted only after success.
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

if [[ "$skip_download" == true && ( "$authenticate" == true || -n "$download_latest" || -n "$activity_limit" ) ]]; then
    argument_error "--skip-download cannot be combined with --auth, --latest, --full-download, or --activity-limit."
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
    # Auth, refresh, import, validation, and the UI all use this same image.
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

if [[ "$skip_download" == false ]]; then
    stage="refreshing the Garmin source"
    echo "Refreshing GarminDB SQLite data..."
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

if [[ "$database" != "postgres" ]]; then
    stage="stopping the DuckDB UI to release its database lock"
    echo "Stopping the DuckDB UI before import..."
    compose --profile duckdb-ui stop duckdb-ui
fi

if [[ "$database" != "duckdb" ]]; then
    stage="starting PostgreSQL"
    bash "$SCRIPT_DIR/run_database.sh" --postgres --no-build
fi

backends=("$database")
if [[ "$database" == "both" ]]; then
    backends=(duckdb postgres)
fi
for backend in "${backends[@]}"; do
    stage="importing $backend data"
    echo "Importing into $backend..."
    compose run --rm --no-deps -T "import-garmin-$backend"
done

if [[ "$validate" == true ]]; then
    stage="validating the imported data"
    echo "Validating $database against the SQLite source..."
    compose run --rm --no-deps -T validate-garmin --backend "$database"
fi

if [[ "$database" != "postgres" ]]; then
    stage="starting the DuckDB UI"
    bash "$SCRIPT_DIR/run_database.sh" --duckdb --no-build
fi

echo
echo "Garmin import complete. Selected database services remain running."
