"""Exercise the Bash launchers without Docker, Garmin access, or real databases."""
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
    "import-garmin-postgres", "validate-garmin", "postgres", "duckdb-ui",
}
action = args[0]
service = next((arg for arg in args[1:] if arg in services), "")
fail_if_requested(action + ":" + service)

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
    if service == "import-garmin-duckdb":
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

    def test_default_builds_refreshes_and_leaves_only_postgres_running(self) -> None:
        result = self.invoke()
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                RUN + ["refresh-garmin-source"],
                UP + ["postgres"],
                RUN + ["import-garmin-postgres"],
            ],
        )
        self.assertTrue((self.state / "postgres").exists())
        self.assertFalse((self.state / "duckdb-ui").exists())
        self.assertIn("jdbc:postgresql://127.0.0.1:15433/garmin-test", result.stdout)
        for call in self.docker_calls():
            if call[:2] == ["compose", "--project-directory"]:
                self.assertEqual(call[2], str(self.job_dir))
                self.assertEqual(call[3:5], ["-f", str(self.job_dir / "docker-compose.yml")])

    def test_duckdb_releases_the_lock_and_restarts_ui_after_import(self) -> None:
        (self.state / "duckdb-ui").touch()
        result = self.invoke("--db", "duckdb")
        self.assert_success(result)
        self.assertEqual(
            self.actions(),
            [
                ["build", "import-garmin-duckdb"],
                RUN + ["refresh-garmin-source"],
                ["stop", "duckdb-ui"],
                RUN + ["import-garmin-duckdb"],
                UP + ["duckdb-ui"],
            ],
        )
        self.assertTrue((self.state / "duckdb-ui").exists())
        self.assertFalse((self.state / "postgres").exists())
        self.assertIn("http://127.0.0.1:14213", result.stdout)

    def test_both_share_one_build_and_refresh_then_validate_before_ui(self) -> None:
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
                RUN + ["import-garmin-duckdb"],
                RUN + ["import-garmin-postgres"],
                RUN + ["validate-garmin", "--backend", "both"],
                UP + ["duckdb-ui"],
            ],
        )
        self.assertTrue((self.state / "postgres").exists())
        self.assertTrue((self.state / "duckdb-ui").exists())

    def test_latest_override_is_passed_only_to_the_refresh_container(self) -> None:
        result = self.invoke("--latest")
        self.assert_success(result)
        self.assertIn(
            RUN + ["-e", "GARMIN_DOWNLOAD_LATEST=true", "refresh-garmin-source"],
            self.actions(),
        )
        self.assertIn(RUN + ["import-garmin-postgres"], self.actions())

    def test_offline_no_build_reuses_image_and_can_rerun_with_ui_running(self) -> None:
        for _ in range(2):
            self.log.write_text("")
            result = self.invoke("--db", "duckdb", "--skip-download", "--no-build")
            self.assert_success(result)
            self.assertEqual(
                self.actions(),
                [
                    ["stop", "duckdb-ui"],
                    RUN + ["import-garmin-duckdb"],
                    UP + ["duckdb-ui"],
                ],
            )
            self.assertIn(["image", "inspect", IMAGE], self.docker_calls())
            self.assertTrue((self.state / "duckdb-ui").exists())

    def test_no_build_rejects_a_missing_local_image(self) -> None:
        (self.state / "image").unlink()
        result = self.invoke("--no-build")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Run without --no-build first", result.stderr)
        self.assertEqual(self.actions(), [])

    def test_validation_targets_only_the_selected_backend(self) -> None:
        for backend in ("duckdb", "postgres"):
            with self.subTest(backend=backend):
                self.log.write_text("")
                result = self.invoke("--db", backend, "--skip-download", "--validate")
                self.assert_success(result)
                self.assertIn(
                    RUN + ["validate-garmin", "--backend", backend],
                    self.actions(),
                )
                other = "postgres" if backend == "duckdb" else "duckdb"
                self.assertNotIn(RUN + [f"import-garmin-{other}"], self.actions())

    def test_postgres_import_does_not_stop_an_existing_duckdb_ui(self) -> None:
        (self.state / "duckdb-ui").touch()
        result = self.invoke("--db", "postgres", "--validate")
        self.assert_success(result)
        self.assertTrue((self.state / "duckdb-ui").exists())
        self.assertNotIn(["stop", "duckdb-ui"], self.actions())

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
        self.assertIn("--db postgres|duckdb|both", result.stdout)
        invalid_options = (
            ("--db",), ("--db", "sqlite"), ("--activity-limit",),
            ("--activity-limit", "0"), ("--activity-limit", "-1"),
            ("--activity-limit", "1.5"), ("--activity-limit", "many"),
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

    def test_build_auth_and_refresh_failures_abort_before_touching_databases(self) -> None:
        (self.state / "duckdb-ui").touch()
        for failure in (
            "build:import-garmin-duckdb",
            "run:garmin-auth",
            "run:refresh-garmin-source",
        ):
            with self.subTest(failure=failure):
                self.log.write_text("")
                result = self.invoke("--db", "both", "--auth", fail=failure)
                self.assertEqual(result.returncode, 42)
                self.assertIn("ERROR: Garmin import failed while", result.stderr)
                self.assertNotIn("Garmin import complete.", result.stdout)
                self.assertTrue((self.state / "duckdb-ui").exists())
                self.assertFalse((self.state / "postgres").exists())
                self.assertNotIn(["stop", "duckdb-ui"], self.actions())
                self.assertNotIn(RUN + ["import-garmin-duckdb"], self.actions())
                if failure != "run:refresh-garmin-source":
                    self.assertNotIn(RUN + ["refresh-garmin-source"], self.actions())

    def test_postgres_health_failure_prevents_import(self) -> None:
        result = self.invoke("--skip-download", fail="up:postgres")
        self.assertEqual(result.returncode, 42)
        self.assertIn("starting PostgreSQL", result.stderr)
        self.assertNotIn(RUN + ["import-garmin-postgres"], self.actions())

    def test_import_or_validation_failure_does_not_restart_duckdb_ui(self) -> None:
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

    def test_ui_health_failure_does_not_report_import_workflow_success(self) -> None:
        result = self.invoke("--db", "duckdb", "--skip-download", fail="up:duckdb-ui")
        self.assertEqual(result.returncode, 42)
        self.assertIn("starting the DuckDB UI", result.stderr)
        self.assertNotIn("Garmin import complete.", result.stdout)

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
