"""Plan campaign batches from schedule settings. Pure functions for tests and API preview."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "00:00").strip().split(":")
    hour = int(parts[0]) if parts else 0
    minute = int(parts[1]) if len(parts) > 1 else 0
    return max(0, min(23, hour)), max(0, min(59, minute))


def _in_time_window(local_dt: datetime, windows: list[dict[str, Any]]) -> bool:
    if not windows:
        return True
    minutes = local_dt.hour * 60 + local_dt.minute
    for window in windows:
        start_h, start_m = _parse_hhmm(str(window.get("start") or "00:00"))
        end_h, end_m = _parse_hhmm(str(window.get("end") or "23:59"))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        if start_min <= minutes <= end_min:
            return True
    return False


def _advance_to_allowed(
    utc_dt: datetime,
    *,
    tz_name: str,
    weekdays: list[int],
    time_windows: list[dict[str, Any]],
    max_steps: int = 10_000,
) -> datetime:
    tz = ZoneInfo(tz_name or "UTC")
    current = utc_dt.astimezone(timezone.utc)
    allowed_days = set(int(d) for d in weekdays) if weekdays else set(range(7))
    for _ in range(max_steps):
        local = current.astimezone(tz)
        # Python weekday: Mon=0 .. Sun=6
        if local.weekday() in allowed_days and _in_time_window(local, time_windows):
            return current
        current = current + timedelta(minutes=15)
    return utc_dt.astimezone(timezone.utc)


def plan_batches(
    *,
    recipient_count: int,
    batch_size: int,
    interval_seconds: int,
    start_at: datetime | None,
    send_immediately: bool = True,
    timezone_name: str = "Europe/Moscow",
    weekdays: list[int] | None = None,
    time_windows: list[dict[str, Any]] | None = None,
    max_per_hour: int = 0,
    max_per_day: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return batch plan preview: batches, ETA, per-day distribution."""
    count = max(0, int(recipient_count))
    size = max(1, int(batch_size or 25))
    interval = max(0, int(interval_seconds or 0))
    weekdays = list(weekdays or [])
    time_windows = list(time_windows or [])
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    if send_immediately or start_at is None:
        cursor = clock
    else:
        cursor = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
        cursor = cursor.astimezone(timezone.utc)

    if count == 0:
        return {
            "batch_count": 0,
            "total_recipients": 0,
            "first_batch_at": None,
            "estimated_completion_at": None,
            "batches": [],
            "per_day": {},
            "next_send_at": None,
        }

    effective_size = size
    if max_per_hour and max_per_hour > 0:
        effective_size = min(effective_size, max_per_hour)
    if max_per_day and max_per_day > 0:
        effective_size = min(effective_size, max_per_day)
    effective_size = max(1, effective_size)

    batches: list[dict[str, Any]] = []
    remaining = count
    batch_index = 0
    day_counts: dict[str, int] = {}
    sent_today = 0
    sent_this_hour = 0
    current_day = None
    current_hour = None

    while remaining > 0:
        cursor = _advance_to_allowed(
            cursor,
            tz_name=timezone_name,
            weekdays=weekdays,
            time_windows=time_windows,
        )
        local = cursor.astimezone(ZoneInfo(timezone_name or "UTC"))
        day_key = local.date().isoformat()
        hour_key = local.replace(minute=0, second=0, microsecond=0)

        if current_day != day_key:
            current_day = day_key
            sent_today = 0
        if current_hour != hour_key:
            current_hour = hour_key
            sent_this_hour = 0

        take = min(remaining, effective_size)
        if max_per_day and max_per_day > 0:
            take = min(take, max_per_day - sent_today)
        if max_per_hour and max_per_hour > 0:
            take = min(take, max_per_hour - sent_this_hour)
        if take <= 0:
            cursor = cursor + timedelta(hours=1)
            continue

        batches.append(
            {
                "batch_index": batch_index,
                "scheduled_at": cursor.isoformat(),
                "size": take,
            }
        )
        day_counts[day_key] = day_counts.get(day_key, 0) + take
        sent_today += take
        sent_this_hour += take
        remaining -= take
        batch_index += 1
        cursor = cursor + timedelta(seconds=interval if interval > 0 else 1)

    first_at = batches[0]["scheduled_at"] if batches else None
    last_at = batches[-1]["scheduled_at"] if batches else None
    return {
        "batch_count": len(batches),
        "total_recipients": count,
        "first_batch_at": first_at,
        "estimated_completion_at": last_at,
        "batches": batches,
        "per_day": day_counts,
        "next_send_at": first_at,
        "batch_size": effective_size,
        "interval_seconds": interval,
    }
