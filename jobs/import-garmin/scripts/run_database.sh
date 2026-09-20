#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    cat <<'EOF'
Usage: ./scripts/run_database.sh [--postgres|--duckdb] [--no-build]

Starts a containerized Garmin database for local access.

  --postgres  Start PostgreSQL and print its connection string (default).
  --duckdb    Start the DuckDB Web UI and print its local URL.
  --no-build  Use existing images without building the DuckDB UI image.
  --help      Show this help.
EOF
}

compose() {
    docker compose --project-directory "$JOB_DIR" -f "$JOB_DIR/docker-compose.yml" "$@"
}

urlencode() {
    local LC_ALL=C
    local value="$1"
    local encoded=""
    local char
    local hex
    local i

    for ((i = 0; i < ${#value}; i++)); do
        char="${value:i:1}"
        case "$char" in
            [a-zA-Z0-9.~_-])
                encoded+="$char"
                ;;
            *)
                printf -v hex '%%%02X' "'$char"
                encoded+="$hex"
                ;;
        esac
    done

    printf '%s' "$encoded"
}

mode="postgres"
mode_selected=false
build=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --postgres|--duckdb)
            if [[ "$mode_selected" == true ]]; then
                echo "ERROR: Choose only one database." >&2
                usage >&2
                exit 2
            fi
            mode="${1#--}"
            mode_selected=true
            ;;
        --no-build)
            build=false
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker is not installed or not available on PATH." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: Docker Compose is not available." >&2
    exit 1
fi

if [[ ! -f "$JOB_DIR/.env" ]]; then
    cp "$JOB_DIR/.env.example" "$JOB_DIR/.env"
    echo "Created $JOB_DIR/.env from .env.example"
fi

up_args=(up -d --wait)
if [[ "$build" == false ]]; then
    up_args+=(--no-build)
fi

if [[ "$mode" == "postgres" ]]; then
    compose --profile postgres "${up_args[@]}" postgres

    endpoint="$(compose port postgres 5432 | tail -n 1)"
    host="${endpoint%:*}"
    port="${endpoint##*:}"
    case "$host" in
        0.0.0.0|::|"[::]")
            host="127.0.0.1"
            ;;
    esac
    credentials="$(compose exec -T postgres sh -lc \
        'printf "%s\n%s\n%s\n" "$POSTGRES_USER" "$POSTGRES_PASSWORD" "$POSTGRES_DB"')"
    user="$(printf '%s\n' "$credentials" | sed -n '1p')"
    password="$(printf '%s\n' "$credentials" | sed -n '2p')"
    database="$(printf '%s\n' "$credentials" | sed -n '3p')"

    jdbc_url="jdbc:postgresql://${host}:${port}/$(urlencode "$database")?sslmode=disable"
    connection_string="postgresql://$(urlencode "$user"):$(urlencode "$password")@${host}:${port}/$(urlencode "$database")?sslmode=disable"

    echo
    echo "PostgreSQL is running."
    echo "DBeaver JDBC URL:"
    echo "$jdbc_url"
    echo "Username:"
    echo "$user"
    echo "Password:"
    echo "$password"
    echo
    echo "PostgreSQL URI (for libpq-compatible clients, not DBeaver's JDBC URL field):"
    echo "$connection_string"
    echo
    echo "Stop it with:"
    echo "  docker compose --project-directory \"$JOB_DIR\" --profile postgres stop postgres"
    exit 0
fi

db_path="$JOB_DIR/local_data/garmin.duckdb"
if [[ ! -f "$db_path" ]]; then
    echo "ERROR: DuckDB file not found: $db_path" >&2
    echo "Run: docker compose --project-directory \"$JOB_DIR\" run --rm import-garmin-duckdb" >&2
    exit 1
fi

if [[ "$build" == true ]]; then
    up_args+=(--build)
fi
compose --profile duckdb-ui "${up_args[@]}" duckdb-ui

endpoint="$(compose port duckdb-ui 4214 | tail -n 1)"
port="${endpoint##*:}"

echo
echo "DuckDB Web UI is running."
echo "URL:"
echo "http://127.0.0.1:${port}"
echo
echo "DuckDB file for desktop clients:"
echo "$db_path"
echo
echo "Stop it with:"
echo "  docker compose --project-directory \"$JOB_DIR\" --profile duckdb-ui stop duckdb-ui"
