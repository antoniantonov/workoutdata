"""Exercise the Bash launchers without Docker, Garmin access, or real databases.

The default launcher flow is now the destination-aware delta sync (the
``sync-garmin`` service). The legacy count-based path is exercised via the
explicit legacy flags (``--skip-download``/``--latest``/``--full-download``/
``--activity-limit``).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


JOB_DIR = Path(__file__).resolve().parents[1]
RUN = ["run", "--rm", "--no-deps", "-T"]
UP = ["up", "-d", "--wait", "--no-build"]
IMAGE = "workoutdata/import-garmin:local"


def sync(backends: str, extra=()):
    return RUN + ["-e", f"GARMIN_SYNC_BACKENDS={backends}", *extra, "sync-garmin"]


def legacy_import(backend: str):
    return RUN + ["-e", "GARMIN_IMPORT_MODE=legacy", f"import-garmin-{backend}"]


FAKE_DOCKER = r'''
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state = Path(os.environ["MOCK_DOCKER_STATE"])
with Path(os.environ["MOCK_DOCKER_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")

def fail_if_requested(key):
    if os.environ.get("MOCK_DOCKER_FAIL") == key:
        print("Simulated Docker failure: " + key, file=sys.stderr)
        sys.exit(42)

if args == ["compose", "version"]:
    fail_if_requested("compose:version")
    sys.exit(0)
if args == ["info"]:
    fail_if_requested("info")
    sys.exit(0)
if args[:2] == ["image", "inspect"]:
    sys.exit(0 if (state / "image").exists() else 1)
if args[0] != "compose":
    sys.exit("Unexpected Docker command: " + repr(args))

args = args[1:]
job_dir = None
while args and args[0] in ("--project-directory", "-f", "--profile"):
    if args[0] == "--project-directory":
        job_dir = Path(args[1])
    args = args[2:]
if job_dir is None:
    sys.exit("Missing explicit Compose project directory")

services = {
    "garmin-auth", "refresh-garmin-source", "import-garmin-duckdb",
    "import-garmin-postgres", "sync-garmin", "validate-garmin",
    "postgres", "duckdb-ui",
}
action = args[0]
service = next((arg for arg in args[1:] if arg in services), "")
fail_if_requested(action + ":" + service)

def env_value(key):
    for i, token in enumerate(args):
        if token == "-e" and i + 1 < len(args) and args[i + 1].startswith(key + "="):
            return args[i + 1].split("=", 1)[1]
    return ""

db_file = job_dir / "local_data" / "garmin.duckdb"
ui = state / "duckdb-ui"
postgres = state / "postgres"
if action == "build":
    (state / "image").touch()
elif action == "config" and args[1:3] == ["--images", "import-garmin-duckdb"]:
    print("workoutdata/import-garmin:local")
elif action == "stop" and service == "duckdb-ui":
    ui.unlink(missing_ok=True)
elif action == "up" and service in ("postgres", "duckdb-ui"):
    if service == "duckdb-ui" and not db_file.is_file():
        sys.exit("DuckDB file must exist before starting the UI")
    (state / service).touch()
elif action == "run":
    if service == "sync-garmin":
        backends = env_value("GARMIN_SYNC_BACKENDS")
        wants_duckdb = backends in ("duckdb", "both")
        wants_postgres = backends in ("postgres", "both")
        if wants_duckdb and ui.exists():
            sys.exit("DuckDB UI still holds the database lock")
        if wants_postgres and not postgres.exists():
            sys.exit("PostgreSQL must be started before sync")
        if wants_duckdb:
            db_file.touch()
    elif service == "import-garmin-duckdb":
        if ui.exists():
            sys.exit("DuckDB UI still holds the database lock")
        db_file.touch()
    elif service == "import-garmin-postgres":
        if not postgres.exists():
            sys.exit("PostgreSQL must be started before import")
    elif service == "validate-garmin":
        backend = args[args.index("--backend") + 1]
        if backend != "postgres" and (ui.exists() or not db_file.is_file()):
            sys.exit("Validation needs imported DuckDB with the UI stopped")
        if backend != "duckdb" and not postgres.exists():
            sys.exit("Validation needs running PostgreSQL")
    elif service not in ("garmin-auth", "refresh-garmin-source"):
        sys.exit("Unexpected run service: " + service)
elif action == "port" and service == "postgres":
    print("127.0.0.1:15433")
elif action == "port" and service == "duckdb-ui":
    print("127.0.0.1:14213")
elif action == "exec" and service == "postgres":
    print("test-user\ntest-password\ngarmin-test")
else:
    sys.exit("Unexpected Compose command: " + repr(args))
'''


class RunImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="garmin launcher ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job_dir = self.root / "job with spaces"
        for directory in ("scripts", "local_data"):
            (self.job_dir / directory).mkdir(parents=True, exist_ok=True)
        for name in (
            "scripts/run_import.sh",
            "scripts/run_database.sh",
            "docker-compose.yml",
            ".env.example",
        ):
            shutil.copy2(JOB_DIR / name, self.job_dir / name)

        self.env_file = self.job_dir / ".env"
        self.env_file.write_text("PRESERVE_EXISTING_CONFIGURATION=true\n")
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "image").touch()
        self.log = self.root / "docker.jsonl"
        self.other_dir = self.root / "unrelated working directory"
        self.other_dir.mkdir()

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(f"#!{sys.executable}\n{FAKE_DOCKER}")
        docker.chmod(0o755)
        self.environment = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "MOCK_DOCKER_LOG": str(self.log),
            "MOCK_DOCKER_STATE": str(self.state),
            "MOCK_DOCKER_FAIL": "",
        }

    def invoke(
        self,
        *args: str,
        script: str = "scripts/run_import.sh",
        fail: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.job_dir / script), *args],
            cwd=self.other_dir,
            env={**self.environment, "MOCK_DOCKER_FAIL": fail},
            capture_output=True,
            text=True,
            timeout=20,
        )

    def docker_calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def actions(self) -> list[list[str]]:
        commands = []
        for call in self.docker_calls():
            if call[0] != "compose" or call == ["compose", "version"]:
                continue
            args = call[1:]
            while args[0] in ("--project-directory", "-f", "--profile"):
                args = args[2:]
            if args[0] in ("build", "run", "up", "stop"):
                commands.append(args)
        return commands

    def assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Garmin import complete.", result.stdout)

    # ── delta mode (new default) ────────────────────────────────────────────
    def test_default_delta_builds_and_syncs_into_postgres_only(self) -> None:
        result = self.invoke()
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                UP + ["postgres"],
                sync("postgres"),
            ],
        )
        self.assertTrue((self.state / "postgres").exists())
        self.assertFalse((self.state / "duckdb-ui").exists())
        # No legacy refresh happens in delta mode.
        self.assertNotIn(RUN + ["refresh-garmin-source"], self.actions())

    def test_delta_duckdb_releases_lock_and_restarts_ui(self) -> None:
        (self.state / "duckdb-ui").touch()
        result = self.invoke("--db", "duckdb")
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                ["stop", "duckdb-ui"],
                sync("duckdb"),
                UP + ["duckdb-ui"],
            ],
        )
        self.assertTrue((self.state / "duckdb-ui").exists())
        self.assertFalse((self.state / "postgres").exists())

    def test_delta_both_starts_pg_stops_ui_then_restarts_ui(self) -> None:
        (self.state / "duckdb-ui").touch()
        result = self.invoke("--db", "both", "--look-back-days", "60", "--validate")
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                ["stop", "duckdb-ui"],
                UP + ["postgres"],
                sync("both", ["-e", "GARMIN_LOOK_BACK_DAYS=60", "-e", "GARMIN_VALIDATE=true"]),
                UP + ["duckdb-ui"],
            ],
        )
        self.assertTrue((self.state / "postgres").exists())
        self.assertTrue((self.state / "duckdb-ui").exists())

    def test_delta_auth_runs_before_sync(self) -> None:
        result = self.invoke("--db", "duckdb", "--auth")
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                ["run", "--rm", "--no-deps", "garmin-auth"],
                ["stop", "duckdb-ui"],
                sync("duckdb"),
                UP + ["duckdb-ui"],
            ],
        )

    def test_delta_postgres_does_not_touch_duckdb_ui(self) -> None:
        (self.state / "duckdb-ui").touch()
        result = self.invoke("--db", "postgres")
        self.assert_success(result)
        self.assertTrue((self.state / "duckdb-ui").exists())
        self.assertNotIn(["stop", "duckdb-ui"], self.actions())
        self.assertIn(sync("postgres"), self.actions())

    def test_look_back_days_forwarded_only_when_provided(self) -> None:
        self.assert_success(self.invoke("--db", "postgres"))
        self.assertIn(sync("postgres"), self.actions())
        self.log.write_text("")
        self.assert_success(self.invoke("--db", "postgres", "--look-back-days", "7"))
        self.assertIn(
            sync("postgres", ["-e", "GARMIN_LOOK_BACK_DAYS=7"]), self.actions()
        )

    # ── legacy mode (explicit opt-in) ───────────────────────────────────────
    def test_legacy_offline_skip_download_imports_full_cache(self) -> None:
        for _ in range(2):
            self.log.write_text("")
            result = self.invoke("--db", "duckdb", "--skip-download", "--no-build")
            self.assert_success(result)
            self.assertEqual(
                self.actions(),
                [
                    ["stop", "duckdb-ui"],
                    legacy_import("duckdb"),
                    UP + ["duckdb-ui"],
                ],
            )
            self.assertIn(["image", "inspect", IMAGE], self.docker_calls())
            self.assertNotIn(sync("duckdb"), self.actions())

    def test_legacy_latest_override_refreshes_then_imports(self) -> None:
        result = self.invoke("--latest")
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                RUN + ["-e", "GARMIN_DOWNLOAD_LATEST=true", "refresh-garmin-source"],
                UP + ["postgres"],
                legacy_import("postgres"),
            ],
        )

    def test_legacy_full_download_both_with_auth_and_validate(self) -> None:
        result = self.invoke(
            "--db", "both", "--auth", "--full-download",
            "--activity-limit", "2000", "--validate",
        )
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                ["run", "--rm", "--no-deps", "garmin-auth"],
                RUN + [
                    "-e", "GARMIN_DOWNLOAD_LATEST=false",
                    "-e", "GARMIN_DOWNLOAD_LATEST_ACTIVITIES=2000",
                    "-e", "GARMIN_DOWNLOAD_ALL_ACTIVITIES=2000",
                    "refresh-garmin-source",
                ],
                ["stop", "duckdb-ui"],
                UP + ["postgres"],
                legacy_import("duckdb"),
                legacy_import("postgres"),
                RUN + ["validate-garmin", "--backend", "both"],
                UP + ["duckdb-ui"],
            ],
        )

    def test_legacy_validation_targets_only_selected_backend(self) -> None:
        for backend in ("duckdb", "postgres"):
            with self.subTest(backend=backend):
                self.log.write_text("")
                result = self.invoke("--db", backend, "--skip-download", "--validate")
                self.assert_success(result)
                self.assertIn(
                    RUN + ["validate-garmin", "--backend", backend], self.actions()
                )
                other = "postgres" if backend == "duckdb" else "duckdb"
                self.assertNotIn(legacy_import(other), self.actions())

    # ── argument validation ────────────────────────────────────────────────
    def test_env_file_is_preserved_or_created_from_the_sample(self) -> None:
        original = self.env_file.read_text()
        self.assert_success(self.invoke("--skip-download"))
        self.assertEqual(self.env_file.read_text(), original)
        self.env_file.unlink()
        self.assert_success(self.invoke("--skip-download"))
        self.assertEqual(
            self.env_file.read_text(),
            (self.job_dir / ".env.example").read_text(),
        )

    def test_help_and_invalid_arguments_do_not_call_docker(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: ./scripts/run_import.sh [options]", result.stdout)
        self.assertIn("--look-back-days N", result.stdout)
        invalid_options = (
            ("--db",), ("--db", "sqlite"), ("--activity-limit",),
            ("--activity-limit", "0"), ("--activity-limit", "-1"),
            ("--activity-limit", "1.5"), ("--activity-limit", "many"),
            ("--look-back-days",), ("--look-back-days", "0"),
            ("--look-back-days", "-3"), ("--look-back-days", "1.5"),
            ("--look-back-days", "abc"),
            ("--look-back-days", "30", "--skip-download"),
            ("--look-back-days", "30", "--latest"),
            ("--look-back-days", "30", "--full-download"),
            ("--look-back-days", "30", "--activity-limit", "10"),
            ("--latest", "--full-download"),
            ("--skip-download", "--latest"),
            ("--skip-download", "--full-download"),
            ("--skip-download", "--activity-limit", "10"),
            ("--skip-download", "--auth"),
            ("--unknown",), ("postgres",),
        )
        for args in invalid_options:
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("ERROR:", result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_compose_and_daemon_failures_are_actionable(self) -> None:
        for failure, message in (
            ("compose:version", "Docker Compose is not available"),
            ("info", "Docker is not running"),
        ):
            with self.subTest(failure=failure):
                result = self.invoke(fail=failure)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.actions(), [])

    def test_no_build_rejects_a_missing_local_image(self) -> None:
        (self.state / "image").unlink()
        result = self.invoke("--no-build")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Run without --no-build first", result.stderr)
        self.assertEqual(self.actions(), [])

    # ── failure handling ────────────────────────────────────────────────────
    def test_build_or_auth_failure_aborts_before_touching_databases(self) -> None:
        (self.state / "duckdb-ui").touch()
        for failure in ("build:import-garmin-duckdb", "run:garmin-auth"):
            with self.subTest(failure=failure):
                self.log.write_text("")
                result = self.invoke("--db", "both", "--auth", fail=failure)
                self.assertEqual(result.returncode, 42)
                self.assertIn("ERROR: Garmin import failed while", result.stderr)
                self.assertNotIn("Garmin import complete.", result.stdout)
                self.assertTrue((self.state / "duckdb-ui").exists())
                self.assertFalse((self.state / "postgres").exists())
                self.assertNotIn(["stop", "duckdb-ui"], self.actions())

    def test_sync_failure_does_not_restart_duckdb_ui(self) -> None:
        (self.state / "duckdb-ui").touch()
        result = self.invoke("--db", "duckdb", fail="run:sync-garmin")
        self.assertEqual(result.returncode, 42)
        self.assertNotIn("Garmin import complete.", result.stdout)
        self.assertFalse((self.state / "duckdb-ui").exists())
        self.assertNotIn(UP + ["duckdb-ui"], self.actions())

    def test_postgres_health_failure_prevents_sync(self) -> None:
        result = self.invoke(fail="up:postgres")
        self.assertEqual(result.returncode, 42)
        self.assertIn("starting PostgreSQL", result.stderr)
        self.assertNotIn(sync("postgres"), self.actions())

    def test_ui_health_failure_after_delta_does_not_report_success(self) -> None:
        result = self.invoke("--db", "duckdb", fail="up:duckdb-ui")
        self.assertEqual(result.returncode, 42)
        self.assertIn("starting the DuckDB UI", result.stderr)
        self.assertNotIn("Garmin import complete.", result.stdout)

    def test_legacy_import_failure_does_not_restart_duckdb_ui(self) -> None:
        for failure in ("run:import-garmin-duckdb", "run:validate-garmin"):
            with self.subTest(failure=failure):
                self.log.write_text("")
                (self.state / "duckdb-ui").touch()
                result = self.invoke(
                    "--db", "duckdb", "--skip-download", "--validate", fail=failure,
                )
                self.assertEqual(result.returncode, 42)
                self.assertNotIn("Garmin import complete.", result.stdout)
                self.assertFalse((self.state / "duckdb-ui").exists())
                self.assertNotIn(UP + ["duckdb-ui"], self.actions())

    # ── run_database.sh unchanged behavior ──────────────────────────────────
    def test_existing_database_launcher_still_builds_the_ui_by_default(self) -> None:
        (self.job_dir / "local_data" / "garmin.duckdb").touch()
        result = self.invoke("--duckdb", script="scripts/run_database.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.actions(),
            [["up", "-d", "--wait", "--build", "duckdb-ui"]],
        )

    def test_existing_database_launcher_rejects_conflicting_backends(self) -> None:
        result = self.invoke("--duckdb", "--postgres", script="scripts/run_database.sh")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Choose only one database", result.stderr)
        self.assertEqual(self.docker_calls(), [])


if __name__ == "__main__":
    unittest.main()
