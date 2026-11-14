"""UTC datetime helpers used at persistence and API boundaries."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime."""

    return datetime.now(UTC)


def ensure_aware_utc(value: datetime, field_name: str = "datetime") -> datetime:
    """Validate an aware datetime and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Serialize an instant as a sortable ISO-8601 UTC string."""

    normalized = ensure_aware_utc(value)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 UTC string and return an aware UTC datetime."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must include a timezone")
    return parsed.astimezone(UTC)
