"""Thin garth adapter: session reuse, date-filtered discovery, selective fetch.

This wraps the already-installed :class:`garth.Client` (garth 0.6.3, pulled in by
``garmindb==3.7.0``). It does **not** add a second authentication stack or a new
dependency. Only date-filtered discovery and *selected* payload acquisition live
here; the destination comparison happens elsewhere so no payload is downloaded
before the delta is known.

Endpoints (community-observed conventions, not an official Garmin contract; the
live smoke test in the test-suite is opt-in):

- ``GET /activitylist-service/activities/search/activities`` with
  ``startDate``/``endDate``/``start``/``limit`` — lightweight summaries.
- ``GET /activity-service/activity/<id>`` — activity detail JSON.
- ``GET /download-service/files/activity/<id>`` — original activity archive.
- ``GET /wellness-service/wellness/dailySleepData/<profile>?date=YYYY-MM-DD`` —
  a full per-date sleep payload (there is no lightweight sleep-date inventory).

Security: tokens are persisted atomically with ``0600`` permissions and are
never logged; activity names, GPS, and raw payloads are never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone, date
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from garmin_etl.delta import ActivitySummary, RemoteInventory, Window

# Verified endpoint templates (kept as constants so tests and the live check
# assert the same strings).
ACTIVITY_SEARCH_PATH = "/activitylist-service/activities/search/activities"
ACTIVITY_DETAIL_PATH = "/activity-service/activity/{activity_id}"
ACTIVITY_ARCHIVE_PATH = "/download-service/files/activity/{activity_id}"
SLEEP_PATH = "/wellness-service/wellness/dailySleepData/{profile}"

DEFAULT_PAGE_SIZE = 100
# A scan re-reads the whole window; two consecutive identical scans confirm a
# stable inventory. Offset pagination is not an atomic snapshot.
MAX_STABILIZATION_SCANS = 3
# Hard safety cap: a runaway/looping pager must fail loudly, never return an
# apparently complete prefix.
MAX_PAGES_PER_SCAN = 1000


class GarthClientProtocol(Protocol):
    """The small slice of :class:`garth.Client` this adapter uses (for stubs)."""

    def connectapi(self, path: str, method: str = "GET", **kwargs: Any) -> Any: ...

    def download(self, path: str, **kwargs: Any) -> bytes: ...

    def dumps(self) -> str: ...


class DiscoveryError(RuntimeError):
    """Raised when remote discovery cannot be completed or verified."""


class InventoryUnstableError(DiscoveryError):
    """Raised when the paginated inventory never stabilizes within the cap."""


class SleepStatus(str, Enum):
    VALID = "valid"
    EMPTY = "empty"
    PROVISIONAL = "provisional"
    ERROR = "error"


@dataclass(frozen=True)
class SleepFetchResult:
    """Outcome of a single sleep-date probe.

    Only :attr:`SleepStatus.VALID` results carry a payload eligible for the
    destination delta. ``EMPTY``/``PROVISIONAL`` stay retryable on later runs;
    ``ERROR`` is a failure, never "no sleep".
    """

    day: date
    status: SleepStatus
    payload: Optional[dict] = None


@dataclass
class ActivityArtifacts:
    """Downloaded artifacts for one selected activity (kept in memory)."""

    activity_id: str
    summary: dict
    details: Optional[dict] = None
    archive_bytes: Optional[bytes] = None
    has_fit: bool = True


# =============================================================================
# Pure parsing helpers (no I/O — unit-testable directly)
# =============================================================================

def parse_activity_id(summary: dict) -> str:
    """Normalize ``activityId`` to a string without losing integer precision."""
    raw = summary.get("activityId")
    if raw is None:
        raise DiscoveryError("Activity summary is missing 'activityId'.")
    if isinstance(raw, bool):
        raise DiscoveryError(f"Invalid 'activityId' value: {raw!r}.")
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text.isdigit():
            raise DiscoveryError(f"Invalid 'activityId' value: {raw!r}.")
        return text
    if isinstance(raw, float):
        if not raw.is_integer():
            raise DiscoveryError(f"Non-integer 'activityId' value: {raw!r}.")
        return str(int(raw))
    raise DiscoveryError(f"Unsupported 'activityId' type: {type(raw)!r}.")


def parse_start_utc(summary: dict) -> datetime:
    """Parse the verified UTC start (``startTimeGMT``) as a tz-aware instant.

    ``startTimeGMT`` is ``'YYYY-MM-DD HH:MM:SS'`` in GMT. A missing or malformed
    value is an error (a malformed page is not an empty inventory), never a
    naive-local guess.
    """
    raw = summary.get("startTimeGMT")
    if not raw or not isinstance(raw, str):
        raise DiscoveryError("Activity summary is missing a valid 'startTimeGMT'.")
    text = raw.strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1].strip()
    try:
        if "." in text:
            naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")
        else:
            naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise DiscoveryError(f"Malformed 'startTimeGMT' value: {raw!r}.") from exc
    return naive.replace(tzinfo=timezone.utc)


def extract_inventory(
    summaries: List[dict], window: Window
) -> Tuple[Dict[str, ActivitySummary], Dict[str, dict]]:
    """De-duplicate summaries and keep only starts inside the exact window.

    Returns ``(activity_summaries_by_id, raw_summary_by_id)``. The date-filtered
    request uses a guard band, so out-of-window rows are dropped here; malformed
    rows raise.
    """
    parsed: Dict[str, ActivitySummary] = {}
    raw_by_id: Dict[str, dict] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            raise DiscoveryError(f"Activity summary is not an object: {summary!r}.")
        activity_id = parse_activity_id(summary)
        start_utc = parse_start_utc(summary)
        if not window.contains_activity(start_utc):
            continue
        parsed[activity_id] = ActivitySummary(activity_id, start_utc)
        raw_by_id[activity_id] = summary
    return parsed, raw_by_id


# =============================================================================
# Session token helpers (atomic, 0600, never logged)
# =============================================================================

def _session_token_path(config: dict) -> Path:
    config_dir = config.get("GARMIN_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / "garth_session"
    job_dir = Path(config["JOB_DIR"])
    return job_dir / "local_data" / ".GarminDb" / "garth_session"


def _read_session_text(config: dict) -> str:
    session_file = _session_token_path(config)
    if session_file.exists():
        return session_file.read_text()
    host_session = Path(os.path.expanduser("~")) / ".GarminDb" / "garth_session"
    if host_session.exists():
        return host_session.read_text()
    raise FileNotFoundError(
        "No garth_session token found. Generate one inside Docker with "
        "'docker compose run --rm garmin-auth', or provide a valid token at "
        f"{session_file}."
    )


def persist_session_token(config: dict, token_text: str) -> Path:
    """Atomically write the session token with restricted permissions."""
    session_file = _session_token_path(config)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = session_file.with_name(session_file.name + ".tmp")
    tmp.write_text(token_text)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, session_file)
    try:
        session_file.chmod(0o600)
    except OSError:
        pass
    return session_file


# =============================================================================
# Adapter
# =============================================================================

class GarminClient:
    """Date-filtered discovery + selective payload acquisition over garth."""

    def __init__(
        self,
        client: GarthClientProtocol,
        profile: Optional[str] = None,
        *,
        config: Optional[dict] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._client = client
        self._profile = profile
        self._config = config
        self._page_size = page_size
        self._initial_token: Optional[str] = None
        try:
            self._initial_token = client.dumps()
        except Exception:  # noqa: BLE001 - stubs may not implement dumps
            self._initial_token = None

    @classmethod
    def from_config(cls, config: dict, *, page_size: int = DEFAULT_PAGE_SIZE) -> "GarminClient":
        """Resume the configured garth session (OAuth1 renewal handled by garth)."""
        import garth

        token_text = _read_session_text(config)
        client = garth.Client()
        domain = os.getenv("GARMIN_DOMAIN", "garmin.com")
        client.configure(domain=domain)
        client.loads(token_text)
        return cls(client, config=config, page_size=page_size)

    # ── session persistence ────────────────────────────────────────────────
    def persist_refreshed_token(self) -> None:
        """Persist the session iff garth renewed it during this run."""
        if self._config is None:
            return
        try:
            current = self._client.dumps()
        except Exception:  # noqa: BLE001
            return
        if current and current != self._initial_token:
            persist_session_token(self._config, current)
            self._initial_token = current

    # ── discovery ──────────────────────────────────────────────────────────
    def _fetch_summary_page(self, window: Window, start: int, limit: int) -> List[dict]:
        params = {
            "startDate": window.query_start_date.isoformat(),
            "endDate": window.query_end_date.isoformat(),
            "start": start,
            "limit": limit,
        }
        result = self._client.connectapi(ACTIVITY_SEARCH_PATH, params=params)
        if result is None:
            return []
        if not isinstance(result, list):
            raise DiscoveryError(
                "Activity search returned an unexpected (non-list) response."
            )
        return result

    def _scan_once(
        self, window: Window
    ) -> Tuple[Dict[str, ActivitySummary], Dict[str, dict]]:
        """One complete paginated scan of the window; fails loudly on anomalies."""
        collected: Dict[str, ActivitySummary] = {}
        raw_by_id: Dict[str, dict] = {}
        offset = 0
        pages = 0
        no_progress = 0
        while True:
            if pages >= MAX_PAGES_PER_SCAN:
                raise InventoryUnstableError(
                    f"Activity pagination exceeded {MAX_PAGES_PER_SCAN} pages; "
                    "refusing to return a partial inventory."
                )
            page = self._fetch_summary_page(window, offset, self._page_size)
            pages += 1
            if not page:
                break  # verified terminal (empty) page
            parsed, raw = extract_inventory(page, window)
            before = len(collected)
            collected.update(parsed)
            raw_by_id.update(raw)
            offset += len(page)
            no_progress = no_progress + 1 if len(collected) == before else 0
            if no_progress >= MAX_PAGES_PER_SCAN:
                raise InventoryUnstableError("Activity pagination made no progress.")
        return collected, raw_by_id

    def discover_activities(
        self, window: Window
    ) -> Tuple[Dict[str, ActivitySummary], Dict[str, dict]]:
        """Return ``(summaries_by_id, raw_by_id)`` for a stable window inventory.

        Repeats complete scans until two consecutive scans match (bounded by
        :data:`MAX_STABILIZATION_SCANS`); otherwise raises rather than claiming
        completeness.
        """
        previous_keys: Optional[frozenset] = None
        for _ in range(MAX_STABILIZATION_SCANS):
            current, raw = self._scan_once(window)
            current_keys = frozenset(current)
            if previous_keys is not None and current_keys == previous_keys:
                self.persist_refreshed_token()
                return current, raw
            previous_keys = current_keys
        raise InventoryUnstableError(
            "Remote activity inventory did not stabilize within "
            f"{MAX_STABILIZATION_SCANS} scans; late uploads may be in progress. "
            "Re-run to let the overlapping window recover."
        )

    def build_remote_inventory(self, window: Window) -> RemoteInventory:
        """Assemble the :class:`RemoteInventory` for the frozen window.

        Sleep has no lightweight inventory API, so the candidate sleep days are
        the window's provider-calendar dates; the coordinator diffs them against
        each destination and probes only what is missing.
        """
        summaries, _ = self.discover_activities(window)
        return RemoteInventory(
            activity_ids=frozenset(summaries),
            sleep_days=frozenset(window.sleep_dates()),
            summaries=dict(summaries),
        )

    # ── selective payload acquisition ──────────────────────────────────────
    def fetch_activity_summary(self, activity_id: str) -> dict:
        result = self._client.connectapi(
            ACTIVITY_DETAIL_PATH.format(activity_id=activity_id)
        )
        if not isinstance(result, dict):
            raise DiscoveryError(
                f"Activity {activity_id} detail returned an unexpected response."
            )
        return result

    def download_activity_archive(self, activity_id: str) -> Optional[bytes]:
        """Return the original activity archive bytes, or ``None`` if absent.

        Only an explicit 404 (a manual/no-sensor activity with no archive) returns
        ``None`` so the caller can take the fixture-tested no-FIT path. Other
        errors propagate, and an unexpectedly empty body is treated as a failed
        download (raised), never a silent zero-sample activity.
        """
        try:
            data = self._client.download(
                ACTIVITY_ARCHIVE_PATH.format(activity_id=activity_id)
            )
        except Exception as exc:  # noqa: BLE001 - inspect for a 404 signal only
            if _is_not_found(exc):
                return None
            raise
        if not data:
            raise DiscoveryError(
                f"Activity {activity_id} archive download returned an empty body."
            )
        return data

    # ── sleep ──────────────────────────────────────────────────────────────
    @property
    def profile(self) -> str:
        if self._profile is None:
            profile = getattr(self._client, "profile", None)
            if isinstance(profile, dict):
                self._profile = profile.get("displayName") or profile.get("userName")
            elif isinstance(profile, str):
                self._profile = profile
            if not self._profile:
                raise DiscoveryError("Could not resolve the Garmin profile id.")
        return self._profile

    def fetch_sleep(self, day: date) -> SleepFetchResult:
        """Probe one sleep date, classifying the response.

        Distinguishes valid data, a successful-but-empty response, an explicitly
        provisional response, and an API failure. Only VALID enters the delta.
        """
        params = {"date": day.isoformat(), "nonSleepBufferMinutes": 60}
        try:
            payload = self._client.connectapi(
                SLEEP_PATH.format(profile=self.profile), params=params
            )
        except Exception as exc:  # noqa: BLE001 - HTTP failure is not "no sleep"
            raise DiscoveryError(f"Sleep fetch failed for {day.isoformat()}.") from exc
        return classify_sleep(day, payload)


def classify_sleep(day: date, payload: Any) -> SleepFetchResult:
    """Classify a raw sleep payload into a :class:`SleepFetchResult`."""
    if payload is None or not isinstance(payload, dict):
        return SleepFetchResult(day, SleepStatus.EMPTY)
    daily = payload.get("dailySleepDTO")
    if not isinstance(daily, dict):
        return SleepFetchResult(day, SleepStatus.EMPTY)
    # A payload for a different calendar day must never enter this day's delta.
    # Compare only the date prefix so a datetime-formatted value still matches.
    calendar = daily.get("calendarDate")
    if calendar is not None and str(calendar)[:10] != day.isoformat():
        return SleepFetchResult(day, SleepStatus.EMPTY)
    # A provisional/in-progress night should defer, not be marked imported.
    if daily.get("sleepWindowConfirmationType") == "UNCONFIRMED":
        return SleepFetchResult(day, SleepStatus.PROVISIONAL, payload)
    total = daily.get("sleepTimeSeconds")
    start = daily.get("sleepStartTimestampGMT")
    if (total in (None, 0)) and not start:
        return SleepFetchResult(day, SleepStatus.EMPTY)
    return SleepFetchResult(day, SleepStatus.VALID, payload)


def _is_not_found(exc: Exception) -> bool:
    """Best-effort detection of an HTTP 404 across garth/requests exceptions.

    When a definite status code is present, trust it exactly — never fall back
    to substring-matching (a 500 whose message/URL contains ``404`` must not be
    misread as a not-found).
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        return status == 404
    return "404" in str(exc)


__all__ = [
    "ACTIVITY_SEARCH_PATH",
    "ACTIVITY_DETAIL_PATH",
    "ACTIVITY_ARCHIVE_PATH",
    "SLEEP_PATH",
    "DEFAULT_PAGE_SIZE",
    "MAX_STABILIZATION_SCANS",
    "GarminClient",
    "GarthClientProtocol",
    "ActivityArtifacts",
    "SleepFetchResult",
    "SleepStatus",
    "DiscoveryError",
    "InventoryUnstableError",
    "parse_activity_id",
    "parse_start_utc",
    "extract_inventory",
    "classify_sleep",
    "persist_session_token",
]
