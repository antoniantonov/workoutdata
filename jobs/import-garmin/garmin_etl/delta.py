"""Typed window/inventory/plan models and pure delta (set-difference) logic.

This module is the vocabulary shared by the Garmin delta coordinator. It has
**no pandas / database / network dependencies** so it can be unit-tested in
isolation and imported cheaply by :mod:`garmin_etl.config`.

Core rules encoded here (see the implementation plan):

- Activities are identified by the Garmin ``activity_id`` string; sleep by the
  provider-calendar ``day``.
- ``missing[b] = R - E[b]`` for remote inventory ``R`` and destination inventory
  ``E[b]``; the union of per-target missing keys is the payload set to acquire
  once; ``network_downloads = needed_payloads - verified_reusable_cache``.
- An empty collection means **none**, never "all". There is no path where a
  missing key or empty set is silently promoted to "import everything".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_LOOK_BACK_DAYS = 30


class Backend(str, Enum):
    """Selectable destination backend."""

    DUCKDB = "duckdb"
    POSTGRES = "postgres"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


def validate_look_back_days(value: object) -> int:
    """Return a positive integer look-back window or raise ``ValueError``.

    Rejects ``None``, booleans, zero, negatives, fractions, and non-numeric
    values. Accepts ``int`` and clean integer-valued ``float``/``str`` inputs so
    the same validator can guard CLI, environment, and programmatic callers.
    """
    if value is None:
        raise ValueError("look-back days is required (no value provided).")
    if isinstance(value, bool):
        raise ValueError("look-back days must be an integer, not a boolean.")

    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"look-back days must be a whole number, got {value!r}.")
        parsed = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdigit():  # rejects '', '-1', '1.5', '2e3', 'many'
            raise ValueError(
                f"look-back days must be a positive integer, got {value!r}."
            )
        parsed = int(text)
    else:
        raise ValueError(f"look-back days has an unsupported type: {type(value)!r}.")

    if parsed <= 0:
        raise ValueError(f"look-back days must be greater than zero, got {parsed}.")
    return parsed


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"Invalid GARMIN_LOCAL_TIMEZONE {name!r}: {exc}") from exc


def selected_backends(db_option: str) -> Tuple[Backend, ...]:
    """Map a ``--db`` option (``postgres``/``duckdb``/``both``) to backends."""
    normalized = str(db_option).strip().lower()
    if normalized == "both":
        return (Backend.DUCKDB, Backend.POSTGRES)
    if normalized in (Backend.DUCKDB.value, Backend.POSTGRES.value):
        return (Backend(normalized),)
    raise ValueError(
        f"Invalid backend selection {db_option!r}; expected 'postgres', "
        "'duckdb', or 'both'."
    )


@dataclass(frozen=True)
class Window:
    """A frozen look-back window anchored on a single ``run_started_at``.

    ``start_utc``/``end_utc`` are timezone-aware UTC instants used for the exact
    activity filter. ``tz`` is the provider/account calendar timezone used for
    day-granular sleep enumeration and for the date parameters sent to the
    activity endpoint (with a one-day guard band on each side).
    """

    look_back_days: int
    start_utc: datetime
    end_utc: datetime
    tz: ZoneInfo

    @classmethod
    def create(
        cls,
        look_back_days: object,
        now_utc: datetime,
        local_timezone: str = "UTC",
    ) -> "Window":
        days = validate_look_back_days(look_back_days)
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware (UTC).")
        end_utc = now_utc.astimezone(timezone.utc)
        try:
            start_utc = end_utc - timedelta(days=days)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                f"look-back days {days} produces an unrepresentable date range."
            ) from exc
        return cls(days, start_utc, end_utc, _load_timezone(local_timezone))

    def contains_activity(self, start_utc: datetime) -> bool:
        """True when an activity's UTC start lies within ``[start, end]``.

        The bounds are inclusive; future starts are excluded because they are
        after ``end_utc``. A naive datetime is rejected rather than silently
        interpreted as UTC.
        """
        if start_utc.tzinfo is None:
            raise ValueError("activity start must be timezone-aware UTC.")
        instant = start_utc.astimezone(timezone.utc)
        return self.start_utc <= instant <= self.end_utc

    @property
    def query_start_date(self) -> date:
        """Local calendar start date for the API request (guard band -1 day)."""
        return (self.start_utc.astimezone(self.tz) - timedelta(days=1)).date()

    @property
    def query_end_date(self) -> date:
        """Local calendar end date for the API request (guard band +1 day)."""
        return (self.end_utc.astimezone(self.tz) + timedelta(days=1)).date()

    def sleep_dates(self) -> Tuple[date, ...]:
        """Provider-calendar dates from the local cutoff through the local run.

        Inclusive on both ends; a rolling N-day window can touch N+1 calendar
        dates because sleep has day granularity.
        """
        start_local = self.start_utc.astimezone(self.tz).date()
        end_local = self.end_utc.astimezone(self.tz).date()
        days = []
        current = start_local
        while current <= end_local:
            days.append(current)
            current += timedelta(days=1)
        return tuple(days)


@dataclass(frozen=True)
class ActivitySummary:
    """A minimal, non-PII activity summary used for planning."""

    activity_id: str
    start_utc: datetime


@dataclass(frozen=True)
class RemoteInventory:
    """The de-duplicated remote inventory for a frozen window."""

    activity_ids: FrozenSet[str]
    sleep_days: FrozenSet[date]
    summaries: Mapping[str, ActivitySummary] = field(default_factory=dict)


@dataclass(frozen=True)
class DestinationInventory:
    """Existing keys already committed in one destination backend."""

    backend: Backend
    activity_ids: FrozenSet[str]
    sleep_days: FrozenSet[date]


@dataclass(frozen=True)
class TargetDelta:
    """The keys a single backend is missing relative to the remote inventory."""

    backend: Backend
    missing_activity_ids: FrozenSet[str]
    missing_sleep_days: FrozenSet[date]

    def is_empty(self) -> bool:
        return not self.missing_activity_ids and not self.missing_sleep_days


@dataclass(frozen=True)
class SyncPlan:
    """The remote inventory plus each selected backend's own delta."""

    window: Window
    remote: RemoteInventory
    targets: Tuple[TargetDelta, ...]

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError("A sync plan requires at least one selected backend.")
        seen = [t.backend for t in self.targets]
        if len(seen) != len(set(seen)):
            raise ValueError("Duplicate backend in sync plan targets.")

    @property
    def needed_activity_payloads(self) -> FrozenSet[str]:
        """Union of every backend's missing activity ids (acquire once)."""
        result: FrozenSet[str] = frozenset()
        for target in self.targets:
            result = result | target.missing_activity_ids
        return result

    @property
    def needed_sleep_days(self) -> FrozenSet[date]:
        """Union of every backend's missing sleep days (probe/fetch once)."""
        result: FrozenSet[date] = frozenset()
        for target in self.targets:
            result = result | target.missing_sleep_days
        return result

    def target(self, backend: Backend) -> TargetDelta:
        for candidate in self.targets:
            if candidate.backend == backend:
                return candidate
        raise KeyError(f"No delta for backend {backend!r} in this plan.")

    def is_empty(self) -> bool:
        """True when no selected backend is missing any activity or sleep key."""
        return all(target.is_empty() for target in self.targets)


def compute_target_delta(
    backend: Backend,
    remote: RemoteInventory,
    destination: DestinationInventory,
) -> TargetDelta:
    """Pure per-target set difference: ``missing = remote - existing``."""
    if destination.backend != backend:
        raise ValueError(
            f"Destination inventory backend {destination.backend!r} does not "
            f"match target backend {backend!r}."
        )
    return TargetDelta(
        backend=backend,
        missing_activity_ids=frozenset(remote.activity_ids) - destination.activity_ids,
        missing_sleep_days=frozenset(remote.sleep_days) - destination.sleep_days,
    )


def compute_plan(
    window: Window,
    remote: RemoteInventory,
    destinations: Mapping[Backend, DestinationInventory],
) -> SyncPlan:
    """Build a :class:`SyncPlan` from the remote inventory and destinations.

    ``destinations`` must be non-empty; an empty mapping is an error rather than
    an implicit "import everything into nothing" no-op.
    """
    if not destinations:
        raise ValueError("compute_plan requires at least one selected destination.")
    targets = tuple(
        compute_target_delta(backend, remote, destinations[backend])
        for backend in destinations
    )
    return SyncPlan(window=window, remote=remote, targets=targets)


def compute_network_downloads(
    needed_payloads: Iterable[str],
    verified_reusable_cache: Iterable[str],
) -> FrozenSet[str]:
    """``network_downloads = needed_payloads - verified_reusable_cache``."""
    return frozenset(needed_payloads) - frozenset(verified_reusable_cache)


__all__ = [
    "DEFAULT_LOOK_BACK_DAYS",
    "Backend",
    "validate_look_back_days",
    "selected_backends",
    "Window",
    "ActivitySummary",
    "RemoteInventory",
    "DestinationInventory",
    "TargetDelta",
    "SyncPlan",
    "compute_target_delta",
    "compute_plan",
    "compute_network_downloads",
]
