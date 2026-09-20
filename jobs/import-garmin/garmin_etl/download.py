"""Optional download phase: refresh GarminDB SQLite via the GarminDB CLI.

Runs only when ``GARMIN_DOWNLOAD=true``. It writes a job-controlled
``GarminConnectConfig.json`` (with ``base_dir`` pinned to the mounted Garmin
base directory and ``metric=true``), ensures a ``garth_session`` token is
available, and invokes ``garmindb_cli.py -f <config_dir> --all --download
--import`` so the SQLite databases are rebuilt in place.

``garmindb`` is imported lazily / only required here, so the default
transform-only path has no dependency on it. The caller decides whether a
download failure is fatal through ``GARMIN_DOWNLOAD_REQUIRED``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG_DIRNAME = ".GarminDb"


def _build_garmin_config(config: dict) -> dict:
    """Construct a GarminConnectConfig.json dict pinned to the job's paths."""
    base_dir = str(Path(config["GARMIN_BASE_DIR"]).resolve())
    start_date = os.getenv("GARMIN_START_DATE", "01/01/2020")
    all_activities = int(os.getenv("GARMIN_DOWNLOAD_ALL_ACTIVITIES", "1000"))
    latest_activities = int(os.getenv("GARMIN_DOWNLOAD_LATEST_ACTIVITIES", "25"))
    domain = os.getenv("GARMIN_DOMAIN", "garmin.com")

    return {
        "db": {"type": "sqlite"},
        "garmin": {"domain": domain},
        "credentials": {
            "user": os.getenv("GARMIN_USER", ""),
            "secure_password": False,
            "password": os.getenv("GARMIN_PASSWORD", ""),
            "password_file": None,
        },
        "data": {
            "weight_start_date": start_date,
            "sleep_start_date": start_date,
            "rhr_start_date": start_date,
            "hrv_start_date": start_date,
            "monitoring_start_date": start_date,
            "download_latest_activities": latest_activities,
            "download_all_activities": all_activities,
        },
        "directories": {
            "relative_to_home": False,
            "base_dir": base_dir,
            "mount_dir": "/Volumes/GARMIN",
        },
        "enabled_stats": {
            "monitoring": False,
            "steps": False,
            "itime": False,
            "sleep": True,
            "rhr": False,
            "hrv": False,
            "weight": False,
            "activities": True,
        },
        "course_views": {"steps": []},
        "modes": {},
        "activities": {"display": []},
        "settings": {
            "metric": True,
            "default_display_activities": ["walking", "running", "cycling"],
        },
        "checkup": {"look_back_days": 90},
    }


def _resolve_config_dir(config: dict) -> Path:
    config_dir = config.get("GARMIN_CONFIG_DIR")
    if config_dir:
        return Path(config_dir)
    return Path(config["JOB_DIR"]) / "local_data" / DEFAULT_CONFIG_DIRNAME


def _read_session_token(session_file: Path):
    """Parse a garth_session token (base64-encoded JSON). Returns obj or None."""
    import base64

    try:
        return json.loads(base64.b64decode(session_file.read_text()))
    except Exception:
        return None


def _warn_on_expired_oauth2_metadata(session_file: Path) -> None:
    """Warn when OAuth2 refresh metadata is stale, then let garth try renewal.

    In garth 0.6.3, an expired OAuth2 token can be re-minted from the persisted
    OAuth1 token. ``refresh_token_expires_at`` is therefore not a definitive
    session-expiry signal. GarminDB performs the real authenticated API check,
    and its output is scanned below for login failures.
    """
    import time

    obj = _read_session_token(session_file)
    if obj is None:
        return  # Unknown format — let the CLI surface any problem.

    oauth2 = None
    candidates = obj if isinstance(obj, list) else [obj]
    for item in candidates:
        if isinstance(item, dict) and "refresh_token_expires_at" in item:
            oauth2 = item
            break
    if not oauth2:
        return

    now = time.time()
    refresh_exp = oauth2.get("refresh_token_expires_at")
    if refresh_exp and now > refresh_exp:
        from datetime import datetime

        expired_on = datetime.fromtimestamp(refresh_exp).strftime("%Y-%m-%d %H:%M:%S")
        print(
            "  WARNING: OAuth2 refresh metadata expired "
            f"{expired_on}; attempting garth OAuth1 session renewal. "
            "If Garmin rejects the session, renew it with "
            "'docker compose run --rm garmin-auth'."
        )


def _ensure_session_token(config_dir: Path) -> Path:
    """Ensure a garth_session token exists in the config dir; return its path.

    The configured token is the source of truth. A token under
    ``~/.GarminDb/garth_session`` is accepted only as a backwards-compatible
    fallback when the configured path is empty.
    """
    session_file = config_dir / "garth_session"
    host_session = Path(os.path.expanduser("~")) / ".GarminDb" / "garth_session"

    if session_file.exists():
        return session_file

    if host_session.exists() and host_session.resolve() != session_file.resolve():
        shutil.copy2(host_session, session_file)
        print(f"  Copied legacy garth_session from {host_session}")
        return session_file

    raise FileNotFoundError(
        "No garth_session token found. Generate one inside Docker with "
        "'docker compose run --rm garmin-auth', or provide a valid token at "
        f"{session_file}."
    )


def _find_cli() -> list:
    """Return the command prefix used to invoke garmindb_cli."""
    cli = shutil.which("garmindb_cli.py")
    if cli:
        return [sys.executable, cli]
    # Fall back to the module form if the package exposes it.
    return [sys.executable, "-m", "garmindb.garmindb_cli"]


# garmindb_cli.py exits 0 even when authentication fails, so the textual output
# must be scanned for these failure markers.
_FAILURE_MARKERS = (
    "Failed to login",
    "Login failed",
    "Authentication failed",
    "Failed to authenticate",
    "session expired",
    "401 Client Error",
    "403 Client Error",
)


def run_download(config: dict) -> bool:
    """Run the GarminDB download+import phase. Returns True on success.

    Raises on misconfiguration (for example, a missing token). Returns False if
    the CLI fails (non-zero exit OR an authentication failure marker in its
    output), so the caller can decide whether to abort or fall back to existing
    databases.
    """
    try:
        import garmindb  # noqa: F401  (presence check; lazy)
    except ImportError as exc:
        raise ImportError(
            "GARMIN_DOWNLOAD is enabled but the 'garmindb' package is not installed. "
            "Install it with: pip install garmindb"
        ) from exc

    config_dir = _resolve_config_dir(config)
    config_dir.mkdir(parents=True, exist_ok=True)
    Path(config["GARMIN_BASE_DIR"]).mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "GarminConnectConfig.json"
    config_file.write_text(json.dumps(_build_garmin_config(config), indent=4), encoding="utf-8")
    print(f"  Wrote GarminDB config: {config_file}")

    session_file = _ensure_session_token(config_dir)
    _warn_on_expired_oauth2_metadata(session_file)

    cmd = _find_cli() + ["-f", str(config_dir), "--all", "--download", "--import"]
    if config.get("GARMIN_DOWNLOAD_LATEST", True):
        cmd.append("--latest")

    print(f"  Running: {' '.join(cmd)}")
    # Stream output live while capturing it so we can detect auth failures that
    # garmindb_cli does not surface via the exit code.
    captured = []
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        captured.append(line)
    returncode = proc.wait()
    output = "".join(captured)

    failure_marker = next((m for m in _FAILURE_MARKERS if m.lower() in output.lower()), None)
    if returncode != 0:
        print(f"  ❌ garmindb_cli exited with code {returncode}")
        return False
    if failure_marker:
        print(
            f"  ❌ GarminDB download failed: detected '{failure_marker}' in CLI output "
            "(garmindb_cli returns 0 even on auth failure). The garth session is likely "
            "unusable — renew it with 'docker compose run --rm garmin-auth' and re-run."
        )
        return False

    print("  ✅ GarminDB download + import complete")
    return True


def main() -> int:
    """Run the live Garmin source refresh as a standalone container command."""
    from garmin_etl.config import load_garmin_configuration

    try:
        config = load_garmin_configuration()
        return 0 if run_download(config) else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports the actionable error
        print(f"ERROR: Garmin source refresh failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_download", "main"]
