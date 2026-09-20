"""Serve the Garmin DuckDB Web UI from a Docker container."""
from __future__ import annotations

import os
import select
import signal
import socket
import socketserver
import sys
from pathlib import Path

import duckdb

from garmin_etl.config import load_garmin_configuration


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _ProxyHandler(socketserver.BaseRequestHandler):
    upstream_port = 4213

    def handle(self) -> None:
        with socket.create_connection(("localhost", self.upstream_port)) as upstream:
            sockets = [self.request, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [])
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is self.request else self.request
                    target.sendall(data)


def _positive_port(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


def main() -> int:
    try:
        config = load_garmin_configuration()
        db_path = Path(config["GARMIN_DUCKDB_PATH"])
        if not db_path.is_file():
            raise FileNotFoundError(
                f"DuckDB file not found: {db_path}. Run the DuckDB import first."
            )

        ui_port = _positive_port("DUCKDB_UI_LOCAL_PORT", 4213)
        proxy_port = _positive_port("DUCKDB_UI_PROXY_PORT", 4214)
        if ui_port == proxy_port:
            raise ValueError(
                "DUCKDB_UI_LOCAL_PORT and DUCKDB_UI_PROXY_PORT must be different"
            )

        con = duckdb.connect(str(db_path))
        server = None
        try:
            con.execute("LOAD ui")
            con.execute(f"SET ui_local_port = {ui_port}")
            started = con.execute("CALL start_ui_server()").fetchone()[0]

            _ProxyHandler.upstream_port = ui_port
            server = _ThreadingTCPServer(("0.0.0.0", proxy_port), _ProxyHandler)

            def _stop(_signum, _frame) -> None:
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)

            print(started)
            print(
                f"DuckDB UI proxy listening on 0.0.0.0:{proxy_port}",
                flush=True,
            )
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            if server is not None:
                server.server_close()
            try:
                con.execute("CALL stop_ui_server()")
            except duckdb.Error:
                pass
            con.close()
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports startup failure
        print(f"ERROR: DuckDB UI failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
