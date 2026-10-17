"""Opt-in live Garmin contract smoke test.

Runs ONLY when ``GARMIN_LIVE_SMOKE=1`` and a valid ``garth_session`` token is
available (see the ``garmin-auth`` command). It verifies the undocumented,
community-observed contracts the adapter relies on — the date-filtered activity
search and the per-date sleep payload — against a safe, read-only window.

It performs NO writes, downloads no FIT archives, and prints only aggregate
counts (never tokens, names, GPS, or raw payloads). Keep it out of the default
suite; it is skipped unless explicitly authorized.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LIVE = os.getenv("GARMIN_LIVE_SMOKE") == "1"


@unittest.skipUnless(LIVE, "set GARMIN_LIVE_SMOKE=1 (with a valid session) to run")
class LiveContractTests(unittest.TestCase):
    def _config(self) -> dict:
        from garmin_etl.config import load_garmin_configuration

        return load_garmin_configuration()

    def test_activity_date_filter_contract(self):
        from garmin_etl.delta import Window
        from garmin_etl.garmin_client import GarminClient

        config = self._config()
        client = GarminClient.from_config(config)
        window = Window.create(
            7, datetime.now(timezone.utc), str(config.get("GARMIN_LOCAL_TIMEZONE", "UTC"))
        )
        summaries, _ = client.discover_activities(window)
        # Every discovered activity must parse and fall inside the exact window.
        for summary in summaries.values():
            self.assertTrue(window.contains_activity(summary.start_utc))
        print(f"[live] discovered {len(summaries)} activities in the 7-day window")

    def test_sleep_payload_contract(self):
        from garmin_etl.delta import Window
        from garmin_etl.garmin_client import GarminClient, SleepStatus

        config = self._config()
        client = GarminClient.from_config(config)
        window = Window.create(
            3, datetime.now(timezone.utc), str(config.get("GARMIN_LOCAL_TIMEZONE", "UTC"))
        )
        statuses = {}
        for day in window.sleep_dates():
            result = client.fetch_sleep(day)
            self.assertIn(result.status, set(SleepStatus))
            statuses[result.status] = statuses.get(result.status, 0) + 1
        print(f"[live] sleep probe status counts: "
              f"{ {str(k): v for k, v in statuses.items()} }")


if __name__ == "__main__":
    unittest.main()
