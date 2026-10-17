"""Unit tests for selected Garmin source acquisition (no network/GarminDB import)."""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl.garmin_client import SleepFetchResult, SleepStatus  # noqa: E402
from garmin_etl.source_import import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    SourceAcquirer,
    activity_archive_filename,
    activity_details_filename,
    activity_fit_filename,
    activity_summary_filename,
    manifest_path,
    read_cache_manifest,
    sleep_filename,
    verify_manifest_artifact,
    write_cache_manifest,
)


class ArchiveError(Exception):
    pass


@dataclass
class FakeAdapter:
    details: dict[str, dict]
    archives: dict[str, bytes | None | BaseException]
    sleeps: dict[date, SleepFetchResult]

    def __post_init__(self) -> None:
        self.summary_calls: list[str] = []
        self.archive_calls: list[str] = []
        self.sleep_calls: list[date] = []

    def fetch_activity_summary(self, activity_id: str) -> dict:
        self.summary_calls.append(activity_id)
        return dict(self.details[activity_id])

    def download_activity_archive(self, activity_id: str):
        self.archive_calls.append(activity_id)
        value = self.archives[activity_id]
        if isinstance(value, BaseException):
            raise value
        return value

    def fetch_sleep(self, day: date) -> SleepFetchResult:
        self.sleep_calls.append(day)
        return self.sleeps[day]


class SourceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1] / "local_data" / "test_source_import_case"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.config = {
            "JOB_DIR": self.root,
            "GARMIN_DB_DIR": self.root / "local_data" / "Garmin" / "DBs",
            "GARMIN_BASE_DIR": self.root / "local_data" / "Garmin",
        }

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _adapter(self, activity_id: str = "1", archive=b"FITDATA") -> FakeAdapter:
        return FakeAdapter(
            details={activity_id: {"activityId": activity_id, "startTimeLocal": "2026-09-20 01:02:03"}},
            archives={activity_id: archive},
            sleeps={},
        )

    def test_manifest_round_trip_checksum_and_version(self) -> None:
        adapter = self._adapter()
        acquirer = SourceAcquirer(self.config, adapter, "manifest")
        result = acquirer.acquire_activities({"1"})
        self.assertEqual(result.acquired, frozenset({"1"}))

        manifest = read_cache_manifest(self.config)
        key = "activity:1:summary"
        self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
        self.assertTrue(verify_manifest_artifact(self.config, manifest, key))

        summary_path = self.root / "local_data" / manifest["artifacts"][key]["path"]
        summary_path.write_text('{"activityId":"1","tampered":true}', encoding="utf-8")
        self.assertFalse(verify_manifest_artifact(self.config, read_cache_manifest(self.config), key))

        write_cache_manifest(self.config, {"schema_version": 999, "artifacts": {"x": {}}})
        self.assertEqual(read_cache_manifest(self.config)["artifacts"], {})

    def test_existing_summary_missing_archive_redownloads_archive(self) -> None:
        first = SourceAcquirer(self.config, self._adapter("1", b"FITDATA"), "first")
        first.acquire_activities({"1"})
        manifest = read_cache_manifest(self.config)
        fit_path = self.root / "local_data" / manifest["artifacts"]["activity:1:fit"]["path"]
        fit_path.unlink()

        adapter = self._adapter("1", b"NEWFIT")
        result = SourceAcquirer(self.config, adapter, "second").acquire_activities({"1"})
        self.assertEqual(adapter.summary_calls, [])
        self.assertEqual(adapter.archive_calls, ["1"])
        self.assertEqual(result.acquired, frozenset({"1"}))
        staged_fit = result.activities_dir / activity_fit_filename("1")
        self.assertEqual(staged_fit.read_bytes(), b"NEWFIT")

    def test_no_fit_path_is_counted_and_not_error(self) -> None:
        adapter = self._adapter("2", None)
        result = SourceAcquirer(self.config, adapter, "nofit").acquire_activities({"2"})
        self.assertEqual(result.acquired, frozenset({"2"}))
        self.assertEqual(result.no_fit, frozenset({"2"}))
        self.assertTrue((result.activities_dir / activity_summary_filename("2")).exists())
        self.assertTrue((result.activities_dir / activity_details_filename("2")).exists())
        self.assertFalse((result.activities_dir / activity_fit_filename("2")).exists())

    def test_failed_archive_download_raises_and_not_complete(self) -> None:
        adapter = self._adapter("3", ArchiveError("500"))
        with self.assertRaises(ArchiveError):
            SourceAcquirer(self.config, adapter, "boom").acquire_activities({"3"})
        manifest = read_cache_manifest(self.config)
        self.assertNotEqual(manifest["activities"].get("3", {}).get("complete"), True)

    def test_sleep_valid_staged_empty_provisional_retry_error_raises(self) -> None:
        valid_day = date(2026, 9, 18)
        empty_day = date(2026, 9, 19)
        prov_day = date(2026, 9, 20)
        err_day = date(2026, 9, 21)
        adapter = FakeAdapter(
            details={},
            archives={},
            sleeps={
                valid_day: SleepFetchResult(valid_day, SleepStatus.VALID, {"dailySleepDTO": {"calendarDate": valid_day.isoformat(), "sleepTimeSeconds": 1}}),
                empty_day: SleepFetchResult(empty_day, SleepStatus.EMPTY),
                prov_day: SleepFetchResult(prov_day, SleepStatus.PROVISIONAL, {"dailySleepDTO": {"calendarDate": prov_day.isoformat()}}),
                err_day: SleepFetchResult(err_day, SleepStatus.ERROR),
            },
        )
        acquirer = SourceAcquirer(self.config, adapter, "sleep")
        result = acquirer.acquire_sleep({valid_day, empty_day, prov_day})
        self.assertEqual(result.sleep_valid, frozenset({valid_day}))
        self.assertEqual(result.sleep_empty, frozenset({empty_day}))
        self.assertEqual(result.sleep_provisional, frozenset({prov_day}))
        self.assertTrue((result.sleep_dir / sleep_filename(valid_day)).exists())
        self.assertFalse((result.sleep_dir / sleep_filename(empty_day)).exists())
        self.assertFalse((result.sleep_dir / sleep_filename(prov_day)).exists())
        manifest = read_cache_manifest(self.config)
        self.assertNotIn(empty_day.isoformat(), manifest["sleep"])
        self.assertNotIn(prov_day.isoformat(), manifest["sleep"])
        with self.assertRaises(RuntimeError):
            acquirer.acquire_sleep({err_day})

    def test_cache_reuse_fully_verified_activity_has_no_download_calls(self) -> None:
        SourceAcquirer(self.config, self._adapter("4", b"FIT4"), "prime").acquire_activities({"4"})
        adapter = self._adapter("4", b"SHOULD_NOT_DOWNLOAD")
        result = SourceAcquirer(self.config, adapter, "reuse").acquire_activities({"4"})
        self.assertEqual(result.reused, frozenset({"4"}))
        self.assertEqual(adapter.summary_calls, [])
        self.assertEqual(adapter.archive_calls, [])
        self.assertTrue((result.activities_dir / activity_summary_filename("4")).exists())
        self.assertTrue((result.activities_dir / activity_details_filename("4")).exists())
        self.assertTrue((result.activities_dir / activity_fit_filename("4")).exists())

    def test_archive_zip_is_cached_and_fit_is_staged(self) -> None:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("folder/original.fit", b"FITZIP")
        adapter = self._adapter("5", buf.getvalue())
        result = SourceAcquirer(self.config, adapter, "zip").acquire_activities({"5"})
        self.assertEqual((result.activities_dir / activity_fit_filename("5")).read_bytes(), b"FITZIP")
        manifest = read_cache_manifest(self.config)
        archive_rel = manifest["artifacts"]["activity:5:archive"]["path"]
        self.assertTrue(archive_rel.endswith(activity_archive_filename("5")))


if __name__ == "__main__":
    unittest.main()
