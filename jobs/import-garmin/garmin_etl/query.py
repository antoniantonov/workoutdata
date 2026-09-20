"""Run a read-only DuckDB or PostgreSQL query inside the import container."""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from garmin_etl.config import load_garmin_configuration


def _read_sql(argument: str | None) -> str:
    if argument:
        return argument
    if sys.stdin.isatty():
        raise ValueError("Provide SQL with --sql or pipe SQL on standard input.")
    sql = sys.stdin.read().strip()
    if not sql:
        raise ValueError("No SQL was provided.")
    return sql


def _print_frame(frame: pd.DataFrame) -> None:
    if frame.empty:
        print("(no rows)")
        return
    print(frame.to_string(index=False))


def _query_duckdb(sql: str, config: dict) -> None:
    import duckdb

    con = duckdb.connect(str(config["GARMIN_DUCKDB_PATH"]), read_only=True)
    try:
        result = con.execute(sql)
        if result.description:
            _print_frame(result.fetchdf())
    finally:
        con.close()


def _query_postgres(sql: str, config: dict) -> None:
    from garmin_etl.storage.postgres import get_postgres_connection

    conn = get_postgres_connection(config)
    try:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                columns = [column.name for column in cur.description]
                _print_frame(pd.DataFrame(cur.fetchall(), columns=columns))
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only SQL against a Garmin destination database."
    )
    parser.add_argument("--backend", choices=("duckdb", "postgres"))
    parser.add_argument("--sql", help="SQL statement. If omitted, SQL is read from stdin.")
    args = parser.parse_args()

    try:
        sql = _read_sql(args.sql)
        config = load_garmin_configuration()
        backend = args.backend or str(config["DATABASE_TYPE"])
        if backend == "duckdb":
            _query_duckdb(sql, config)
        else:
            _query_postgres(sql, config)
    except Exception as exc:  # noqa: BLE001 - CLI boundary surfaces a concise failure
        print(f"ERROR: Query failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
