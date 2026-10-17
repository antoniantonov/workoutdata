"""Delta sync coordinator: discover, plan, acquire once, load each target.

This is the shared entry point used by the launcher's ``sync-garmin`` service and
by the online ``main.py`` delta path. It ties together the pure planner
(:mod:`garmin_etl.delta`), the garth adapter (:mod:`garmin_etl.garmin_client`),
selective source acquisition (:mod:`garmin_etl.source_import`), the selective
transform, the per-backend delta writes, and scoped validation.

Design notes
------------
- Collaborators (clock, adapter, acquirer, storage resolver, locks) are
  injectable so the coordinator can be tested without network, Docker, or a real
  GarminDB import.
- The union of missing keys is acquired exactly once; each backend loads only
  its own delta. An empty plan performs no download and no destination DML.
- There is no cross-database transaction for ``both``: a backend that commits is
  recorded, and a later backend failure exits non-zero. Because inventories are
  read fresh on each run, a retry naturally skips the already-committed backend.
- Only operational metadata and aggregate counters are logged — never tokens,
  names, GPS, or raw payloads.
"""
from __future__ import annotations

import functools
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from garmin_etl import transform as transform_layer
from garmin_etl import validate as validate_layer
from garmin_etl.delta import (
    Backend,
    DestinationInventory,
    RemoteInventory,
    SyncPlan,
    Window,
    compute_plan,
    selected_backends,
)


def _storage_for(backend: Backend):
    if backend == Backend.DUCKDB:
        from garmin_etl.storage import duckdb as storage
    else:
        from garmin_etl.storage import postgres as storage
    return storage


@dataclass
class TargetReport:
    backend: Backend
    existing: int = 0
    missing_activities: int = 0
    missing_sleep: int = 0
    loaded_activities: int = 0
    loaded_sleep: int = 0
    committed: bool = False


@dataclass
class SyncReport:
    window: Window
    backends: Sequence[Backend]
    remote_activities: int = 0
    remote_sleep_candidates: int = 0
    downloaded_activities: int = 0
    reused_activities: int = 0
    no_fit_activities: int = 0
    sleep_valid: int = 0
    sleep_empty: int = 0
    sleep_provisional: int = 0
    sleep_reused: int = 0
    targets: Dict[Backend, TargetReport] = field(default_factory=dict)
    no_op: bool = False
    failed_backend: Optional[Backend] = None

    def exit_code(self) -> int:
        return 0 if self.failed_backend is None else 1

    def render(self) -> str:
        lines = [
            "Garmin delta sync report",
            f"  window: {self.window.look_back_days}d "
            f"[{self.window.start_utc.isoformat()} .. {self.window.end_utc.isoformat()}]",
            f"  backends: {', '.join(str(b) for b in self.backends)}",
            f"  remote: {self.remote_activities} activities, "
            f"{self.remote_sleep_candidates} sleep candidate day(s)",
        ]
        if self.no_op:
            lines.append("  result: no-op — no destination rows were changed")
            return "\n".join(lines)
        lines.append(
            f"  acquired: {self.downloaded_activities} downloaded, "
            f"{self.reused_activities} reused, {self.no_fit_activities} no-FIT"
        )
        lines.append(
            f"  sleep probes: {self.sleep_valid} valid, {self.sleep_reused} reused, "
            f"{self.sleep_empty} empty, {self.sleep_provisional} provisional"
        )
        for backend in self.backends:
            target = self.targets.get(backend)
            if target is None:
                continue
            lines.append(
                f"  {backend}: existing {target.existing}, "
                f"loaded {target.loaded_activities} activity/{target.loaded_sleep} sleep, "
                f"committed={target.committed}"
            )
        if self.failed_backend is not None:
            lines.append(f"  FAILED backend: {self.failed_backend} (exit non-zero; retry to repair)")
        return "\n".join(lines)


# =============================================================================
# Locking
# =============================================================================

_ADVISORY_LOCK_KEY = 947_216_501  # stable arbitrary key for the sync advisory lock


@contextmanager
def _job_locks(config: dict, backends: Sequence[Backend]):
    """Serialize shared-cache/DuckDB access and guard PostgreSQL sync.

    Acquires a filesystem lock (shared GarminDB cache + DuckDB writer) first,
    then a PostgreSQL advisory lock when PostgreSQL is selected. Locks are
    released on every exit path in the reverse order.
    """
    import fcntl

    lock_dir = Path(config["JOB_DIR"]) / "local_data"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".delta.lock"
    lock_file = open(lock_path, "w")
    pg_conn = None
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if Backend.POSTGRES in backends:
            from garmin_etl.storage.postgres import get_postgres_connection

            pg_conn = get_postgres_connection(config)
            with pg_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
            pg_conn.commit()
        yield
    finally:
        if pg_conn is not None:
            try:
                with pg_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
                pg_conn.commit()
            except Exception:
                pass
            finally:
                pg_conn.close()
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


# =============================================================================
# Coordinator
# =============================================================================

class GarminSync:
    """Coordinate a destination-aware delta sync for the selected backends."""

    def __init__(
        self,
        config: dict,
        backends: Sequence[Backend],
        *,
        adapter=None,
        acquirer=None,
        clock: Optional[Callable[[], datetime]] = None,
        run_id: Optional[str] = None,
        use_locks: bool = True,
        storage_resolver: Optional[Callable[[Backend], object]] = None,
    ) -> None:
        if not backends:
            raise ValueError("At least one backend must be selected.")
        self.config = config
        self.backends = tuple(backends)
        self._adapter = adapter
        self._acquirer = acquirer
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        self._use_locks = use_locks
        self._resolve = storage_resolver or _storage_for
        self.last_report: Optional[SyncReport] = None

    # ── collaborator construction (real defaults) ──────────────────────────
    def _get_adapter(self):
        if self._adapter is None:
            from garmin_etl.garmin_client import GarminClient

            self._adapter = GarminClient.from_config(self.config)
        return self._adapter

    def _get_acquirer(self):
        if self._acquirer is None:
            from garmin_etl.source_import import SourceAcquirer

            self._acquirer = SourceAcquirer(self.config, self._get_adapter(), self._run_id)
        return self._acquirer

    # ── main entry ─────────────────────────────────────────────────────────
    def run(self, *, look_back_days: int, deep_validate: bool = False) -> SyncReport:
        window = Window.create(
            look_back_days,
            self._clock(),
            str(self.config.get("GARMIN_LOCAL_TIMEZONE", "UTC")),
        )
        if self._use_locks:
            with _job_locks(self.config, self.backends):
                return self._run_locked(window, deep_validate)
        return self._run_locked(window, deep_validate)

    def _discover(self, window: Window) -> RemoteInventory:
        return self._get_adapter().build_remote_inventory(window)

    def _target_inventories(
        self, window: Window, remote: RemoteInventory
    ) -> Dict[Backend, DestinationInventory]:
        sleep_days = sorted(remote.sleep_days)
        low = sleep_days[0] if sleep_days else window.start_utc.date()
        high = sleep_days[-1] if sleep_days else window.end_utc.date()
        destinations: Dict[Backend, DestinationInventory] = {}
        for backend in self.backends:
            storage = self._resolve(backend)
            existing_ids = storage.get_existing_activity_ids(self.config, remote.activity_ids)
            existing_days = storage.get_existing_sleep_days(self.config, low, high)
            destinations[backend] = DestinationInventory(
                backend=backend,
                activity_ids=frozenset(str(a) for a in existing_ids),
                sleep_days=frozenset(existing_days),
            )
        return destinations

    def _run_locked(self, window: Window, deep_validate: bool) -> SyncReport:
        report = SyncReport(window=window, backends=self.backends)
        self.last_report = report
        remote = self._discover(window)
        report.remote_activities = len(remote.activity_ids)
        report.remote_sleep_candidates = len(remote.sleep_days)

        destinations = self._target_inventories(window, remote)
        plan = compute_plan(window, remote, destinations)
        for backend in self.backends:
            target = plan.target(backend)
            report.targets[backend] = TargetReport(
                backend=backend,
                existing=len(destinations[backend].activity_ids),
                missing_activities=len(target.missing_activity_ids),
                missing_sleep=len(target.missing_sleep_days),
            )

        if plan.is_empty():
            report.no_op = True
            # A no-op run still verifies cross-backend parity under --validate, so
            # a previously-committed mismatch is not hidden once both targets hold
            # the key.
            if deep_validate and len(self.backends) > 1:
                self._parity_check(remote)
            print(report.render())
            return report

        valid_sleep = self._acquire(plan, report)
        try:
            self._load_targets(plan, valid_sleep, deep_validate, report)

            if deep_validate and len(self.backends) > 1 and report.failed_backend is None:
                self._parity_check(remote)
        except Exception:
            # Surface which backend(s) committed vs. failed before propagating,
            # so a partial `both` failure reports that distinction. The committed
            # backend is skipped and its cache reused on a retry.
            print(report.render())
            raise

        print(report.render())
        return report

    def _parity_check(self, remote: RemoteInventory) -> None:
        # Compare all in-window candidate activities/days present in both backends
        # (not just this run's writes), so pre-existing divergence is surfaced.
        validate_layer.validate_window_parity(
            self.config,
            [str(b) for b in self.backends],
            sorted(remote.activity_ids),
            sorted(remote.sleep_days),
        )

    def _acquire(self, plan: SyncPlan, report: SyncReport) -> frozenset:
        acquirer = self._get_acquirer()
        needed_ids = plan.needed_activity_payloads
        needed_days = plan.needed_sleep_days

        activity_result = acquirer.acquire_activities(needed_ids)
        sleep_result = acquirer.acquire_sleep(needed_days)
        acquirer.import_into_cache()

        report.downloaded_activities = len(getattr(activity_result, "acquired", ()))
        report.reused_activities = len(getattr(activity_result, "reused", ()))
        report.no_fit_activities = len(getattr(activity_result, "no_fit", ()))
        report.sleep_valid = len(getattr(sleep_result, "sleep_valid", ()))
        report.sleep_reused = len(getattr(sleep_result, "sleep_reused", ()))
        report.sleep_empty = len(getattr(sleep_result, "sleep_empty", ()))
        report.sleep_provisional = len(getattr(sleep_result, "sleep_provisional", ()))
        return frozenset(getattr(sleep_result, "sleep_valid", ()))

    # ── stable workoutId assignment (persistent, cross-run/backend) ─────────
    def _assignment_map_path(self):
        return (
            Path(self.config["JOB_DIR"]) / "local_data" / ".delta_cache" / "workout_ids.json"
        )

    def _load_assignment_map(self) -> Dict[str, str]:
        try:
            data = json.loads(self._assignment_map_path().read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _save_assignment_map(self, mapping: Dict[str, str]) -> None:
        path = self._assignment_map_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(mapping, sort_keys=True))
        os.replace(tmp, path)

    def _canonical_assignments(self, plan: SyncPlan) -> Dict[str, str]:
        """Compute one canonical ``{activity_id: workoutId}`` map for the run.

        The assignment for new activities is computed **once over the union** of
        needed ids (not per-backend subset), seeded from the persistent map plus
        every selected backend's committed assignments for the in-window
        candidates. This guarantees the same workoutId across backends and across
        separate single-backend runs, and reserves ids of stale/other activities
        so a new activity never reuses one.
        """
        persistent = self._load_assignment_map()
        seed_maps = [persistent]
        for backend in self.backends:
            seed_maps.append(
                self._resolve(backend).get_workout_id_assignments(
                    self.config, plan.remote.activity_ids
                )
            )
        seed = transform_layer.merge_existing_assignments(*seed_maps)

        needed_ids = sorted(plan.needed_activity_payloads)
        if not needed_ids:
            return seed
        union = transform_layer.transform_selected(self.config, needed_ids, [])
        canonical = transform_layer.compute_workout_id_map(union["workouts"], seed)
        merged = transform_layer.merge_existing_assignments(persistent, canonical)
        self._save_assignment_map(merged)
        # Return seed ∪ canonical so every backend subset id resolves via the
        # "existing" branch and every backend agrees.
        return {**seed, **canonical}

    def _load_targets(
        self,
        plan: SyncPlan,
        valid_sleep: frozenset,
        deep_validate: bool,
        report: SyncReport,
    ) -> None:
        canonical = self._canonical_assignments(plan)
        assign = functools.partial(
            transform_layer.assign_workout_ids, existing_assignments=canonical
        )
        for backend in self.backends:
            target = plan.target(backend)
            activity_ids = sorted(target.missing_activity_ids)
            sleep_days = sorted(target.missing_sleep_days & valid_sleep)
            target_report = report.targets[backend]
            if not activity_ids and not sleep_days:
                # Fully satisfied backend: no writable connection, no DML. This
                # also lets a partial-`both` retry leave the committed backend
                # completely untouched.
                target_report.committed = True
                continue
            frames = transform_layer.transform_selected(
                self.config, activity_ids, sleep_days, assign_workout_ids=assign
            )
            storage = self._resolve(backend)
            try:
                storage.import_workouts(frames["workouts"], frames["timeseries"], self.config)
                storage.import_sleep(frames["sleep"], self.config)
            except Exception:
                report.failed_backend = backend
                raise
            validate_layer.assert_scope_present(
                self.config, str(backend), activity_ids, sleep_days,
                storage=self._resolve(backend),
            )
            if deep_validate:
                validate_layer.validate_delta(
                    self.config, str(backend), frames, activity_ids, sleep_days, deep=True
                )
            target_report.loaded_activities = len(activity_ids)
            target_report.loaded_sleep = len(sleep_days)
            target_report.committed = True


# =============================================================================
# CLI entry point (used by the sync-garmin Compose service and main.py)
# =============================================================================

def _selected_backends_from_env(config: dict) -> Sequence[Backend]:
    raw = os.getenv("GARMIN_SYNC_BACKENDS")
    if raw:
        names = [part.strip() for part in raw.split(",") if part.strip()]
        result: List[Backend] = []
        for name in names:
            for backend in selected_backends(name):
                if backend not in result:
                    result.append(backend)
        if result:
            return tuple(result)
    # Fall back to the single configured DATABASE_TYPE.
    return selected_backends(str(config["DATABASE_TYPE"]))


def run_from_config(config: dict, *, deep_validate: Optional[bool] = None) -> SyncReport:
    """Build real collaborators from ``config`` and run the delta sync."""
    backends = _selected_backends_from_env(config)
    look_back_days = int(config["GARMIN_LOOK_BACK_DAYS"])
    if deep_validate is None:
        deep_validate = os.getenv("GARMIN_VALIDATE", "false").lower() == "true"
    sync = GarminSync(config, backends)
    return sync.run(look_back_days=look_back_days, deep_validate=deep_validate)


def main() -> int:
    from garmin_etl.config import load_garmin_configuration

    try:
        config = load_garmin_configuration()
        report = run_from_config(config)
        return report.exit_code()
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports the actionable error
        print(f"ERROR: Garmin delta sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["GarminSync", "SyncReport", "TargetReport", "run_from_config", "main"]
