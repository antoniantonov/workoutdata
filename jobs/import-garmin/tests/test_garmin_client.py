"""Adapter tests using a stub garth client and synthetic payloads (no network).

These prove the date-window filtering, pagination + stabilization, selective
fetch, no-FIT handling, and sleep classification against fixtures. An opt-in
live contract check (guarded by GARMIN_LIVE_SMOKE=1) is intentionally not run in
the default suite.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garmin_etl.delta import Window  # noqa: E402
from garmin_etl.garmin_client import (  # noqa: E402
    ACTIVITY_SEARCH_PATH,
    DiscoveryError,
    GarminClient,
    InventoryUnstableError,
    SleepStatus,
    classify_sleep,
    extract_inventory,
    parse_activity_id,
    parse_start_utc,
)

UTC = timezone.utc


def _summary(activity_id, start_gmt):
    return {"activityId": activity_id, "startTimeGMT": start_gmt}


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"{status} Client Error")
        self.response = type("R", (), {"status_code": status})()


class StubClient:
    """A garth.Client stand-in that slices a flat per-scan list by offset.

    ``scans`` is a list of scans; each scan is a flat, ordered list of summary
    dicts (as the real ``.../search/activities`` endpoint returns for
    ``start``/``limit``). A fresh scan begins when the adapter requests
    ``start=0`` again, letting us model an inventory that changes between scans.
    """

    def __init__(self, scans=None, details=None, archives=None, sleep=None):
        self._scans = scans if scans is not None else [[]]
        self._scan_index = 0
        self._any_served = False
        self._details = details or {}
        self._archives = archives or {}
        self._sleep = sleep or {}
        self.calls = []
        self.download_calls = []
        self._token = "token-v1"

    def connectapi(self, path, method="GET", **kwargs):
        self.calls.append((path, kwargs.get("params")))
        if path == ACTIVITY_SEARCH_PATH:
            params = kwargs["params"]
            start, limit = params["start"], params["limit"]
            if start == 0 and self._any_served:
                self._scan_index = min(self._scan_index + 1, len(self._scans) - 1)
            self._any_served = True
            scan = self._scans[min(self._scan_index, len(self._scans) - 1)]
            return list(scan[start:start + limit])
        if path.startswith("/activity-service/activity/"):
            return self._details.get(path.rsplit("/", 1)[-1])
        if path.startswith("/wellness-service/wellness/dailySleepData/"):
            return self._sleep.get(kwargs["params"]["date"])
        raise AssertionError(f"unexpected path {path}")

    def download(self, path, **kwargs):
        activity_id = path.rsplit("/", 1)[-1]
        self.download_calls.append(activity_id)
        value = self._archives.get(activity_id, KeyError)
        if value is KeyError:
            raise _HttpError(404)
        return value

    def dumps(self):
        return self._token


def _stable(flat):
    """Two identical scans of the same flat list (a stable inventory)."""
    return [list(flat), list(flat)]


class ParsingTests(unittest.TestCase):
    def test_parse_activity_id_precision(self):
        self.assertEqual(parse_activity_id({"activityId": 19876543210}), "19876543210")
        self.assertEqual(parse_activity_id({"activityId": "19876543210"}), "19876543210")

    def test_parse_activity_id_rejects_bad(self):
        for bad in ({}, {"activityId": None}, {"activityId": "x"}, {"activityId": 1.5}):
            with self.assertRaises(DiscoveryError):
                parse_activity_id(bad)

    def test_parse_start_utc(self):
        got = parse_start_utc({"startTimeGMT": "2026-09-10 08:30:00"})
        self.assertEqual(got, datetime(2026, 9, 10, 8, 30, tzinfo=UTC))

    def test_parse_start_utc_rejects_missing_or_bad(self):
        for bad in ({}, {"startTimeGMT": ""}, {"startTimeGMT": "nonsense"}):
            with self.assertRaises(DiscoveryError):
                parse_start_utc(bad)

    def test_extract_inventory_filters_window(self):
        window = Window.create(30, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))
        summaries = [
            _summary(1, "2026-09-10 08:00:00"),  # in window
            _summary(2, "2026-07-01 08:00:00"),  # before window (guard-band row)
            _summary(3, "2026-09-20 11:59:59"),  # in window
        ]
        parsed, raw = extract_inventory(summaries, window)
        self.assertEqual(set(parsed), {"1", "3"})
        self.assertEqual(set(raw), {"1", "3"})


class DiscoveryTests(unittest.TestCase):
    def _window(self):
        return Window.create(30, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))

    def test_multi_page_over_25_and_stabilizes(self):
        # 30 in-window activities, page size 25 → proves >25 / multi-page.
        flat = (
            [_summary(i, "2026-09-10 08:00:00") for i in range(1, 26)]
            + [_summary(i, "2026-09-11 08:00:00") for i in range(26, 31)]
        )
        client = StubClient(scans=_stable(flat))
        adapter = GarminClient(client, page_size=25)
        summaries, raw = adapter.discover_activities(self._window())
        self.assertEqual(set(summaries), {str(i) for i in range(1, 31)})
        self.assertEqual(set(raw), {str(i) for i in range(1, 31)})

    def test_known_ids_before_a_later_missing_id_are_all_found(self):
        flat = [
            _summary(100, "2026-09-10 08:00:00"),
            _summary(101, "2026-09-10 09:00:00"),
            _summary(102, "2026-09-12 08:00:00"),
        ]
        client = StubClient(scans=_stable(flat))
        adapter = GarminClient(client, page_size=25)
        summaries, _ = adapter.discover_activities(self._window())
        self.assertEqual(set(summaries), {"100", "101", "102"})

    def test_unstable_inventory_fails_loudly(self):
        scans = [
            [_summary(1, "2026-09-10 08:00:00")],
            [_summary(2, "2026-09-11 08:00:00")],
            [_summary(3, "2026-09-12 08:00:00")],
        ]
        client = StubClient(scans=scans)
        adapter = GarminClient(client, page_size=25)
        with self.assertRaises(InventoryUnstableError):
            adapter.discover_activities(self._window())

    def test_non_list_response_is_error_not_empty(self):
        class Bad(StubClient):
            def connectapi(self, path, method="GET", **kwargs):
                if path == ACTIVITY_SEARCH_PATH:
                    return {"error": "boom"}
                return super().connectapi(path, method, **kwargs)

        adapter = GarminClient(Bad(), page_size=25)
        with self.assertRaises(DiscoveryError):
            adapter.discover_activities(self._window())

    def test_build_remote_inventory_sets_sleep_candidates(self):
        client = StubClient(scans=_stable([_summary(1, "2026-09-10 08:00:00")]))
        adapter = GarminClient(client, page_size=25)
        window = self._window()
        remote = adapter.build_remote_inventory(window)
        self.assertEqual(remote.activity_ids, frozenset({"1"}))
        self.assertEqual(remote.sleep_days, frozenset(window.sleep_dates()))


class FetchTests(unittest.TestCase):
    def test_download_archive_returns_bytes(self):
        client = StubClient(archives={"1": b"PK\x03\x04zip"})
        adapter = GarminClient(client)
        self.assertEqual(adapter.download_activity_archive("1"), b"PK\x03\x04zip")

    def test_download_archive_404_is_none_for_no_fit(self):
        client = StubClient(archives={})
        adapter = GarminClient(client)
        self.assertIsNone(adapter.download_activity_archive("999"))

    def test_download_archive_non_404_propagates(self):
        class Boom(StubClient):
            def download(self, path, **kwargs):
                raise _HttpError(500)

        adapter = GarminClient(Boom())
        with self.assertRaises(_HttpError):
            adapter.download_activity_archive("1")

    def test_download_archive_500_with_404_in_message_propagates(self):
        # A 500 whose message/URL contains "404" must NOT be misread as no-FIT.
        class Boom(StubClient):
            def download(self, path, **kwargs):
                raise _HttpError(500)  # status_code=500 is authoritative

        adapter = GarminClient(Boom())
        with self.assertRaises(_HttpError):
            adapter.download_activity_archive("123404567")

    def test_download_archive_empty_body_is_failure_not_no_fit(self):
        client = StubClient(archives={"1": b""})
        adapter = GarminClient(client)
        with self.assertRaises(DiscoveryError):
            adapter.download_activity_archive("1")

    def test_parse_start_utc_preserves_fractional_seconds(self):
        got = parse_start_utc({"startTimeGMT": "2026-09-10 08:30:00.900"})
        self.assertEqual(got, datetime(2026, 9, 10, 8, 30, 0, 900000, tzinfo=UTC))

    def test_fetch_summary_requires_dict(self):
        class Bad(StubClient):
            def connectapi(self, path, method="GET", **kwargs):
                return ["not", "a", "dict"]

        adapter = GarminClient(Bad())
        with self.assertRaises(DiscoveryError):
            adapter.fetch_activity_summary("1")


class SleepClassificationTests(unittest.TestCase):
    def test_valid(self):
        payload = {"dailySleepDTO": {"sleepTimeSeconds": 25200,
                                     "sleepStartTimestampGMT": 1_700_000_000}}
        self.assertEqual(classify_sleep(date(2026, 9, 19), payload).status, SleepStatus.VALID)

    def test_empty(self):
        for payload in (None, {}, {"dailySleepDTO": {}},
                        {"dailySleepDTO": {"sleepTimeSeconds": 0}}):
            self.assertEqual(
                classify_sleep(date(2026, 9, 19), payload).status, SleepStatus.EMPTY
            )

    def test_provisional(self):
        payload = {"dailySleepDTO": {"sleepWindowConfirmationType": "UNCONFIRMED",
                                     "sleepTimeSeconds": 100}}
        self.assertEqual(
            classify_sleep(date(2026, 9, 19), payload).status, SleepStatus.PROVISIONAL
        )

    def test_wrong_calendar_day_is_not_valid(self):
        # A valid-looking payload for a different day must not enter the delta.
        payload = {"dailySleepDTO": {"calendarDate": "2026-09-01",
                                     "sleepTimeSeconds": 25200,
                                     "sleepStartTimestampGMT": 1}}
        result = classify_sleep(date(2026, 9, 19), payload)
        self.assertEqual(result.status, SleepStatus.EMPTY)

    def test_matching_calendar_day_datetime_format_is_valid(self):
        payload = {"dailySleepDTO": {"calendarDate": "2026-09-19T00:00:00.0",
                                     "sleepTimeSeconds": 25200,
                                     "sleepStartTimestampGMT": 1}}
        self.assertEqual(
            classify_sleep(date(2026, 9, 19), payload).status, SleepStatus.VALID
        )

    def test_fetch_sleep_http_error_is_error_not_empty(self):
        class Boom(StubClient):
            def connectapi(self, path, method="GET", **kwargs):
                if path.startswith("/wellness-service/"):
                    raise _HttpError(500)
                return super().connectapi(path, method, **kwargs)

        adapter = GarminClient(Boom(), profile="user-1")
        with self.assertRaises(DiscoveryError):
            adapter.fetch_sleep(date(2026, 9, 19))

    def test_fetch_sleep_valid_via_adapter(self):
        payload = {"dailySleepDTO": {"sleepTimeSeconds": 25200,
                                     "sleepStartTimestampGMT": 1}}
        client = StubClient(sleep={"2026-09-19": payload})
        adapter = GarminClient(client, profile="user-1")
        result = adapter.fetch_sleep(date(2026, 9, 19))
        self.assertEqual(result.status, SleepStatus.VALID)
        self.assertIs(result.payload, payload)


if __name__ == "__main__":
    unittest.main()
