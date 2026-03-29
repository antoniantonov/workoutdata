"""Python wrappers around garmindb_cli.py commands.

Provides convenient functions to run garmindb CLI operations from Python code
or Jupyter notebooks without needing to use the command line directly.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from garmin.config import get_config_path, get_db_dir


def _run_garmindb_cli(*args: str, config_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Run garmindb_cli.py with the given arguments.

    Parameters
    ----------
    *args:
        CLI arguments to pass to garmindb_cli.py.
    config_path:
        Optional path to GarminConnectConfig.json. Uses default if None.

    Returns
    -------
    subprocess.CompletedProcess
        Result of the CLI invocation.

    Raises
    ------
    RuntimeError
        If the CLI command fails.
    """
    cfg = config_path or get_config_path()
    if not cfg.exists():
        raise FileNotFoundError(
            f"GarminDB config not found at {cfg}.\n"
            "Run garmin.setup_config('email', 'password') first."
        )

    # Look for garmindb_cli.py: next to the Python executable first, then on PATH
    cli_script = Path(sys.executable).parent / "garmindb_cli.py"
    if not cli_script.exists():
        found = shutil.which("garmindb_cli.py")
        if found:
            cli_script = Path(found)
        else:
            raise FileNotFoundError(
                f"garmindb_cli.py not found next to {sys.executable} or on PATH.\n"
                "Make sure garmindb is installed: uv pip install garmindb"
            )
    # garmindb -f expects the config DIRECTORY (not the file), it appends the filename
    cfg_dir = str(cfg.parent)
    cmd = [sys.executable, str(cli_script), "-f", cfg_dir] + list(args)
    print(f"🔄 Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"garmindb_cli failed with exit code {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    return result


def download_all(config_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Download all data from Garmin Connect and import + analyze it.

    Equivalent to: garmindb_cli.py --all --download --import --analyze
    """
    print("\n" + "=" * 60)
    print("GARMIN: Downloading ALL data from Garmin Connect...")
    print("=" * 60)
    return _run_garmindb_cli("--all", "--download", "--import", "--analyze", config_path=config_path)


def download_latest(config_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Download latest data from Garmin Connect and import + analyze it.

    Equivalent to: garmindb_cli.py --all --download --import --analyze --latest
    """
    print("\n" + "=" * 60)
    print("GARMIN: Downloading LATEST data from Garmin Connect...")
    print("=" * 60)
    return _run_garmindb_cli("--all", "--download", "--import", "--analyze", "--latest", config_path=config_path)


def import_data(config_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Import previously downloaded data into SQLite databases.

    Equivalent to: garmindb_cli.py --all --import
    """
    print("\n" + "=" * 60)
    print("GARMIN: Importing data into SQLite databases...")
    print("=" * 60)
    return _run_garmindb_cli("--all", "--import", config_path=config_path)


def analyze_data(config_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Analyze data and create summary tables.

    Equivalent to: garmindb_cli.py --all --analyze
    """
    print("\n" + "=" * 60)
    print("GARMIN: Analyzing data and creating summaries...")
    print("=" * 60)
    return _run_garmindb_cli("--all", "--analyze", config_path=config_path)


def backup_data(config_path: Optional[Path] = None) -> subprocess.CompletedProcess:
    """Backup GarminDB database files.

    Equivalent to: garmindb_cli.py --backup
    """
    print("\n" + "=" * 60)
    print("GARMIN: Backing up databases...")
    print("=" * 60)
    return _run_garmindb_cli("--backup", config_path=config_path)
