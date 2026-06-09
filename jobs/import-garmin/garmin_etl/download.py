"""Optional download phase: refresh GarminDB SQLite via the GarminDB CLI.

Runs only when ``GARMIN_DOWNLOAD=true``. It writes a job-controlled
``GarminConnectConfig.json`` (with ``base_dir`` pinned to the mounted Garmin
base directory and ``metric=true``), ensures a ``garth_session`` token is
available, and invokes ``garmindb_cli.py -f <config_dir> --all --download
--import`` so the SQLite databases are rebuilt in place.

``garmindb`` is imported lazily / only required here, so the default
transform-only path has no dependency on it. This phase is best-effort: callers
should fall back to existing SQLite databases on failure.
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


def _validate_session_token(session_file: Path) -> None:
    """Fail fast if the garth session's refresh token is already expired.

    garth can refresh an expired *access* token, but once the *refresh* token
    expires a full interactive re-login (with MFA) is required — which cannot
    happen inside the container. Detect that up front with a clear message.
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
        raise RuntimeError(
            f"Garmin garth session is expired (refresh token expired {expired_on}). "
            "Generate a new token on the host with './scripts/renew_garmin_token.sh' (prompts "
            "for Garmin login + MFA and writes ~/.GarminDb/garth_session), then re-run "
            f"this job. Token file: {session_file}"
        )


def _ensure_session_token(config_dir: Path) -> Path:
    """Ensure a garth_session token exists in the config dir; return its path.

    The host token (``~/.GarminDb/garth_session``) is the source of truth: when it
    exists, the config-dir copy is **refreshed** from it on every run so a freshly
    renewed token is always used (a stale cached copy in the config dir — e.g. left
    by a previous run on the mounted volume — must never win).
    """
    session_file = config_dir / "garth_session"
    host_session = Path(os.path.expanduser("~")) / ".GarminDb" / "garth_session"

    if host_session.exists() and host_session.resolve() != session_file.resolve():
        shutil.copy2(host_session, session_file)
        print(f"  Refreshed garth_session from {host_session}")
        return session_file

    if session_file.exists():
        return session_file

    raise FileNotFoundError(
        "No garth_session token found. Generate one with the helper script on the "
        f"host: './scripts/renew_garmin_token.sh' (writes ~/.GarminDb/garth_session), or place "
        f"a valid token at {session_file}."
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

    Raises on misconfiguration (missing/expired token). Returns False if the CLI
    fails (non-zero exit OR an authentication failure marker in its output), so
    the caller can decide whether to abort or fall back to existing databases.
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
    _validate_session_token(session_file)  # fail fast on an expired refresh token

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
            "expired — regenerate it (Garmin login + MFA) and re-run."
        )
        return False

    print("  ✅ GarminDB download + import complete")
    return True


__all__ = ["run_download"]
