"""Selective GarminDB source acquisition and cache import helpers.

This module stages only the payloads selected by the destination delta planner.
It deliberately reuses GarminDB's importers for FIT/JSON parsing; the code here
only downloads, verifies, caches, stages, and invokes those importers.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Protocol

from garmin_etl.garmin_client import SleepStatus

MANIFEST_SCHEMA_VERSION = 1
CACHE_DIRNAME = ".delta_cache"
ACTIVITY_DIRNAME = "Activities"
SLEEP_DIRNAME = "Sleep"
NO_FIT_MARKER = "NO_FIT.json"


class SourceAdapter(Protocol):
    """Adapter methods required for selected source acquisition."""

    def fetch_activity_summary(self, activity_id: str) -> dict: ...

    def download_activity_archive(self, activity_id: str) -> Optional[bytes]: ...

    def fetch_sleep(self, day: date) -> Any: ...


@dataclass(frozen=True)
class AcquisitionResult:
    """Aggregate outcome for one staging run.

    Sets contain only stable identifiers (activity id or calendar day). Raw
    payloads, GPS, names, and tokens are intentionally excluded.
    """

    staging_dir: Path
    acquired: frozenset[str] = frozenset()
    reused: frozenset[str] = frozenset()
    no_fit: frozenset[str] = frozenset()
    failed: frozenset[str] = frozenset()
    sleep_valid: frozenset[date] = frozenset()
    sleep_reused: frozenset[date] = frozenset()
    sleep_empty: frozenset[date] = frozenset()
    sleep_provisional: frozenset[date] = frozenset()

    @property
    def activities_dir(self) -> Path:
        return self.staging_dir / ACTIVITY_DIRNAME

    @property
    def sleep_dir(self) -> Path:
        return self.staging_dir / SLEEP_DIRNAME


@dataclass
class _AcquisitionBuilder:
    staging_dir: Path
    acquired: set[str] = field(default_factory=set)
    reused: set[str] = field(default_factory=set)
    no_fit: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    sleep_valid: set[date] = field(default_factory=set)
    sleep_reused: set[date] = field(default_factory=set)
    sleep_empty: set[date] = field(default_factory=set)
    sleep_provisional: set[date] = field(default_factory=set)

    def freeze(self) -> AcquisitionResult:
        return AcquisitionResult(
            staging_dir=self.staging_dir,
            acquired=frozenset(self.acquired),
            reused=frozenset(self.reused),
            no_fit=frozenset(self.no_fit),
            failed=frozenset(self.failed),
            sleep_valid=frozenset(self.sleep_valid),
            sleep_reused=frozenset(self.sleep_reused),
            sleep_empty=frozenset(self.sleep_empty),
            sleep_provisional=frozenset(self.sleep_provisional),
        )


def _local_data_dir(config: Mapping[str, Any]) -> Path:
    return Path(config["JOB_DIR"]) / "local_data"


def stage_dir_for_run(config: Mapping[str, Any], run_id: str) -> Path:
    """Return/create the run-specific staging directory under ``local_data``."""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(run_id))
    if not safe:
        raise ValueError("run_id must contain at least one safe character")
    root = (_local_data_dir(config) / f"delta_stage_{safe}").resolve()
    local = _local_data_dir(config).resolve()
    if local not in (root, *root.parents):
        raise ValueError("resolved staging directory escaped local_data")
    (root / ACTIVITY_DIRNAME).mkdir(parents=True, exist_ok=True)
    (root / SLEEP_DIRNAME).mkdir(parents=True, exist_ok=True)
    return root


def cache_root(config: Mapping[str, Any]) -> Path:
    """Return/create the delta cache root under ``local_data``."""
    root = _local_data_dir(config) / CACHE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def manifest_path(config: Mapping[str, Any]) -> Path:
    return cache_root(config) / "manifest.json"


def new_manifest() -> dict[str, Any]:
    return {"schema_version": MANIFEST_SCHEMA_VERSION, "artifacts": {}, "activities": {}, "sleep": {}}


def read_cache_manifest(config: Mapping[str, Any]) -> dict[str, Any]:
    """Read the cache manifest; incompatible/missing/corrupt manifests are empty."""
    path = manifest_path(config)
    if not path.exists():
        return new_manifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_manifest()
    if not isinstance(data, dict) or data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return new_manifest()
    data.setdefault("artifacts", {})
    data.setdefault("activities", {})
    data.setdefault("sleep", {})
    if not isinstance(data["artifacts"], dict) or not isinstance(data["activities"], dict) or not isinstance(data["sleep"], dict):
        return new_manifest()
    return data


def write_cache_manifest(config: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    """Atomically persist the cache manifest."""
    path = manifest_path(config)
    payload = json.dumps(manifest, indent=2, sort_keys=True, default=str).encode("utf-8")
    _atomic_write_bytes(path, payload)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest_artifact(config: Mapping[str, Any], manifest: Mapping[str, Any], artifact_key: str) -> bool:
    """Return True iff a manifest artifact exists and its checksum matches."""
    entry = manifest.get("artifacts", {}).get(artifact_key)
    if not isinstance(entry, dict):
        return False
    rel = entry.get("path")
    expected = entry.get("sha256")
    if not isinstance(rel, str) or not isinstance(expected, str):
        return False
    path = (_local_data_dir(config) / rel).resolve()
    local = _local_data_dir(config).resolve()
    if local not in (path, *path.parents) or not path.is_file():
        return False
    try:
        return sha256_file(path) == expected
    except OSError:
        return False


class SourceAcquirer:
    """Acquire selected Garmin payloads into a staging directory and cache."""

    def __init__(self, config: Mapping[str, Any], adapter: SourceAdapter, run_id: str) -> None:
        self.config = config
        self.adapter = adapter
        self.staging_dir = stage_dir_for_run(config, run_id)
        self.cache_dir = cache_root(config)
        self.manifest = read_cache_manifest(config)

    def acquire_activities(self, needed_ids: set[str] | frozenset[str]) -> AcquisitionResult:
        """Stage selected activities, downloading only missing/corrupt artifacts."""
        result = _AcquisitionBuilder(self.staging_dir)
        for raw_id in sorted(needed_ids, key=str):
            activity_id = str(raw_id)
            try:
                state = self._activity_state(activity_id)
                if state["complete"]:
                    self._stage_cached_activity(activity_id)
                    result.reused.add(activity_id)
                    if state["no_fit"]:
                        result.no_fit.add(activity_id)
                    continue

                activity_payload: Optional[dict] = None
                if not state["summary"] or not state["details"]:
                    activity_payload = self.adapter.fetch_activity_summary(activity_id)
                    if not isinstance(activity_payload, dict):
                        raise ValueError(f"activity {activity_id} payload is not a JSON object")
                    summary_payload, details_payload = _split_activity_payload(activity_id, activity_payload)
                    if not state["summary"]:
                        self._cache_json(_activity_artifact_key(activity_id, "summary"), _activity_cache_path(self.config, activity_id, activity_summary_filename(activity_id)), summary_payload)
                    if not state["details"]:
                        self._cache_json(_activity_artifact_key(activity_id, "details"), _activity_cache_path(self.config, activity_id, activity_details_filename(activity_id)), details_payload)

                state = self._activity_state(activity_id)
                if not state["archive"] and not state["no_fit"]:
                    archive = self.adapter.download_activity_archive(activity_id)
                    if archive is None:
                        self._mark_no_fit(activity_id)
                        result.no_fit.add(activity_id)
                    else:
                        self._cache_archive(activity_id, archive)

                state = self._activity_state(activity_id)
                if not state["complete"]:
                    raise RuntimeError(f"activity {activity_id} cache did not verify after acquisition")
                self._mark_activity_complete(activity_id, no_fit=state["no_fit"])
                write_cache_manifest(self.config, self.manifest)
                self._stage_cached_activity(activity_id)
                result.acquired.add(activity_id)
                if state["no_fit"]:
                    result.no_fit.add(activity_id)
            except Exception:
                result.failed.add(activity_id)
                write_cache_manifest(self.config, self.manifest)
                raise
        return result.freeze()

    def acquire_sleep(self, needed_days: set[date] | frozenset[date]) -> AcquisitionResult:
        """Stage valid sleep days; leave empty/provisional days retryable."""
        result = _AcquisitionBuilder(self.staging_dir)
        for day in sorted(needed_days):
            key = _sleep_artifact_key(day)
            if verify_manifest_artifact(self.config, self.manifest, key) and self._sleep_identity_ok(day):
                self._stage_cached_sleep(day)
                result.sleep_reused.add(day)
                result.sleep_valid.add(day)
                continue

            fetched = self.adapter.fetch_sleep(day)
            status = getattr(fetched, "status", None)
            payload = getattr(fetched, "payload", None)
            if status == SleepStatus.VALID:
                if not isinstance(payload, dict):
                    raise RuntimeError(f"valid sleep fetch for {day.isoformat()} had no JSON payload")
                self._cache_json(key, _sleep_cache_path(self.config, day), payload)
                self.manifest["sleep"][day.isoformat()] = {"artifact": key, "status": "valid"}
                write_cache_manifest(self.config, self.manifest)
                self._stage_cached_sleep(day)
                result.sleep_valid.add(day)
            elif status == SleepStatus.EMPTY:
                self.manifest["sleep"].pop(day.isoformat(), None)
                write_cache_manifest(self.config, self.manifest)
                result.sleep_empty.add(day)
            elif status == SleepStatus.PROVISIONAL:
                self.manifest["sleep"].pop(day.isoformat(), None)
                write_cache_manifest(self.config, self.manifest)
                result.sleep_provisional.add(day)
            elif status == SleepStatus.ERROR:
                raise RuntimeError(f"sleep fetch failed for {day.isoformat()}")
            else:
                raise RuntimeError(f"unexpected sleep status {status!r} for {day.isoformat()}")
        return result.freeze()

    def import_into_cache(self, staged: AcquisitionResult | Path | None = None) -> None:
        """Run GarminDB importers over this run's selected staging directories.

        GarminDB importers parse and load files into the persistent SQLite cache
        at ``config['GARMIN_DB_DIR']``. ``latest=False`` is intentional because
        the staging directory is already delta-filtered.
        """
        stage = _stage_path(staged, self.staging_dir)
        import_into_cache(self.config, stage)

    def _activity_state(self, activity_id: str) -> dict[str, bool]:
        summary_ok = verify_manifest_artifact(self.config, self.manifest, _activity_artifact_key(activity_id, "summary")) and self._activity_json_identity_ok(activity_id, "summary")
        details_ok = verify_manifest_artifact(self.config, self.manifest, _activity_artifact_key(activity_id, "details")) and self._activity_json_identity_ok(activity_id, "details")
        fit_ok = verify_manifest_artifact(self.config, self.manifest, _activity_artifact_key(activity_id, "fit"))
        no_fit = verify_manifest_artifact(self.config, self.manifest, _activity_artifact_key(activity_id, "no_fit"))
        return {"summary": summary_ok, "details": details_ok, "archive": fit_ok, "no_fit": no_fit, "complete": summary_ok and details_ok and (fit_ok or no_fit)}

    def _cache_json(self, key: str, path: Path, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        _atomic_write_bytes(path, data)
        self._record_artifact(key, path, "json")

    def _cache_archive(self, activity_id: str, data: bytes) -> None:
        if not data:
            raise RuntimeError(f"activity {activity_id} archive download was empty")
        archive_path = _activity_cache_path(self.config, activity_id, activity_archive_filename(activity_id))
        _atomic_write_bytes(archive_path, data)
        self._record_artifact(_activity_artifact_key(activity_id, "archive"), archive_path, "archive")
        fit_bytes = _extract_fit_bytes(activity_id, data)
        fit_path = _activity_cache_path(self.config, activity_id, activity_fit_filename(activity_id))
        _atomic_write_bytes(fit_path, fit_bytes)
        self._record_artifact(_activity_artifact_key(activity_id, "fit"), fit_path, "fit")
        self.manifest["activities"].setdefault(activity_id, {})["no_fit"] = False

    def _mark_no_fit(self, activity_id: str) -> None:
        marker = {"activity_id": activity_id, "status": "no_fit"}
        path = _activity_cache_path(self.config, activity_id, NO_FIT_MARKER)
        self._cache_json(_activity_artifact_key(activity_id, "no_fit"), path, marker)
        activity = self.manifest["activities"].setdefault(activity_id, {})
        activity["no_fit"] = True
        activity.pop("complete", None)

    def _mark_activity_complete(self, activity_id: str, *, no_fit: bool) -> None:
        self.manifest["activities"][activity_id] = {
            "summary": _activity_artifact_key(activity_id, "summary"),
            "details": _activity_artifact_key(activity_id, "details"),
            "fit": None if no_fit else _activity_artifact_key(activity_id, "fit"),
            "no_fit": no_fit,
            "complete": True,
        }

    def _record_artifact(self, key: str, path: Path, kind: str) -> None:
        self.manifest["artifacts"][key] = {
            "kind": kind,
            "path": str(path.resolve().relative_to(_local_data_dir(self.config).resolve())),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }

    def _stage_cached_activity(self, activity_id: str) -> None:
        activities_dir = self.staging_dir / ACTIVITY_DIRNAME
        activities_dir.mkdir(parents=True, exist_ok=True)
        for kind, filename in (
            ("summary", activity_summary_filename(activity_id)),
            ("details", activity_details_filename(activity_id)),
            ("fit", activity_fit_filename(activity_id)),
        ):
            key = _activity_artifact_key(activity_id, kind)
            if not verify_manifest_artifact(self.config, self.manifest, key):
                if kind == "fit" and verify_manifest_artifact(self.config, self.manifest, _activity_artifact_key(activity_id, "no_fit")):
                    continue
                raise RuntimeError(f"cached {kind} artifact for activity {activity_id} did not verify")
            shutil.copy2(_artifact_path(self.config, self.manifest, key), activities_dir / filename)

    def _stage_cached_sleep(self, day: date) -> None:
        key = _sleep_artifact_key(day)
        if not verify_manifest_artifact(self.config, self.manifest, key):
            raise RuntimeError(f"cached sleep artifact for {day.isoformat()} did not verify")
        sleep_dir = self.staging_dir / SLEEP_DIRNAME
        sleep_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_artifact_path(self.config, self.manifest, key), sleep_dir / sleep_filename(day))

    def _activity_json_identity_ok(self, activity_id: str, kind: str) -> bool:
        try:
            payload = json.loads(_artifact_path(self.config, self.manifest, _activity_artifact_key(activity_id, kind)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return _activity_payload_identity(payload) == activity_id

    def _sleep_identity_ok(self, day: date) -> bool:
        try:
            payload = json.loads(_artifact_path(self.config, self.manifest, _sleep_artifact_key(day)).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        daily = payload.get("dailySleepDTO") if isinstance(payload, dict) else None
        calendar = daily.get("calendarDate") if isinstance(daily, dict) else None
        return calendar in (None, day.isoformat())


def import_into_cache(config: Mapping[str, Any], staged: AcquisitionResult | Path) -> None:
    """Invoke GarminDB importers for staged activity and sleep payloads."""
    stage = _stage_path(staged, None)
    activities_dir = stage / ACTIVITY_DIRNAME
    sleep_dir = stage / SLEEP_DIRNAME

    from idbutils import DbParams
    import fitfile
    from garmindb import ActivityFitFileProcessor, GarminActivitiesFitData, GarminJsonDetailsData, GarminJsonSummaryData, GarminSleepData
    from garmindb.garmindb import Attributes, GarminDb

    db_dir = Path(config["GARMIN_DB_DIR"])
    db_dir.mkdir(parents=True, exist_ok=True)
    db_params = DbParams(db_type="sqlite", db_path=str(db_dir))
    gdb = GarminDb(db_params)
    Attributes.set_if_unset(gdb, "measurement_system", "metric")
    measurement_system = fitfile.field_enums.DisplayMeasure.metric
    debug = 0
    latest = False

    if sleep_dir.exists():
        sleep = GarminSleepData(db_params, str(sleep_dir), latest, debug)
        if sleep.file_count() > 0:
            sleep.process()

    if activities_dir.exists():
        summary = GarminJsonSummaryData(db_params, str(activities_dir), latest, measurement_system, debug)
        if summary.file_count() > 0:
            summary.process()
        details = GarminJsonDetailsData(db_params, str(activities_dir), latest, measurement_system, debug)
        if details.file_count() > 0:
            details.process()
        fit_data = GarminActivitiesFitData(str(activities_dir), latest, measurement_system, debug)
        if fit_data.file_count() > 0:
            fit_data.process_files(ActivityFitFileProcessor(db_params, _NullPluginManager(), debug))


class _NullPluginManager:
    def get_file_processors(self, file_type: str, fit_file: Any) -> dict[str, Any]:
        return {}


def activity_summary_filename(activity_id: str) -> str:
    return f"activity_{activity_id}.json"


def activity_details_filename(activity_id: str) -> str:
    return f"activity_details_{activity_id}.json"


def activity_archive_filename(activity_id: str) -> str:
    return f"activity_{activity_id}.zip"


def activity_fit_filename(activity_id: str) -> str:
    return f"{activity_id}.fit"


def sleep_filename(day: date) -> str:
    return f"sleep_{day.isoformat()}.json"


def _activity_cache_path(config: Mapping[str, Any], activity_id: str, filename: str) -> Path:
    path = cache_root(config) / "activities" / activity_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sleep_cache_path(config: Mapping[str, Any], day: date) -> Path:
    path = cache_root(config) / "sleep" / sleep_filename(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _activity_artifact_key(activity_id: str, kind: str) -> str:
    return f"activity:{activity_id}:{kind}"


def _sleep_artifact_key(day: date) -> str:
    return f"sleep:{day.isoformat()}:json"


def _artifact_path(config: Mapping[str, Any], manifest: Mapping[str, Any], key: str) -> Path:
    rel = manifest["artifacts"][key]["path"]
    return _local_data_dir(config) / rel


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _activity_payload_identity(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    candidates = [payload.get("activityId")]
    summary = payload.get("summaryDTO")
    if isinstance(summary, dict):
        candidates.append(summary.get("activityId"))
    metadata = payload.get("metadataDTO")
    if isinstance(metadata, dict):
        candidates.append(metadata.get("activityId"))
    for value in candidates:
        if value is not None:
            return str(value)
    return None


def _split_activity_payload(activity_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = _activity_payload_identity(payload)
    if identity is not None and identity != activity_id:
        raise ValueError(f"activity payload identity {identity!r} did not match requested id")
    details = dict(payload)
    details.setdefault("activityId", activity_id)
    summary_dto = payload.get("summaryDTO")
    if isinstance(summary_dto, dict):
        summary = dict(summary_dto)
        for key in ("activityName", "description", "activityType", "eventType"):
            if key in payload and key not in summary:
                summary[key] = payload[key]
        summary.setdefault("activityId", activity_id)
    else:
        summary = dict(payload)
        summary.setdefault("activityId", activity_id)
    return summary, details


def _extract_fit_bytes(activity_id: str, data: bytes) -> bytes:
    with BytesIO(data) as bio:
        if zipfile.is_zipfile(bio):
            bio.seek(0)
            with zipfile.ZipFile(bio) as zf:
                fit_names = [name for name in zf.namelist() if name.lower().endswith(".fit") and not name.endswith("/")]
                if not fit_names:
                    raise RuntimeError(f"activity {activity_id} archive contained no FIT file")
                return zf.read(fit_names[0])
    return data


def _stage_path(staged: AcquisitionResult | Path | None, default: Optional[Path]) -> Path:
    if isinstance(staged, AcquisitionResult):
        return staged.staging_dir
    if isinstance(staged, Path):
        return staged
    if staged is None and default is not None:
        return default
    raise ValueError("staged path is required")


__all__ = [
    "ACTIVITY_DIRNAME",
    "CACHE_DIRNAME",
    "MANIFEST_SCHEMA_VERSION",
    "SLEEP_DIRNAME",
    "AcquisitionResult",
    "SourceAcquirer",
    "activity_archive_filename",
    "activity_details_filename",
    "activity_fit_filename",
    "activity_summary_filename",
    "cache_root",
    "import_into_cache",
    "manifest_path",
    "new_manifest",
    "read_cache_manifest",
    "sha256_file",
    "sleep_filename",
    "stage_dir_for_run",
    "verify_manifest_artifact",
    "write_cache_manifest",
]
