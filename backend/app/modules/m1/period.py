"""M1 booking period proposal; independent from room opening facts."""

from datetime import UTC, datetime, time, timedelta

from app.core.errors import DomainError
from app.core.time import BUSINESS_TZ, as_utc, to_utc


def precise_utcnow():
    return datetime.now(UTC)


def validate_booking_period(start_at, end_at, *, now=None, max_hours=4):
    try:
        local_values = [
            value.astimezone(BUSINESS_TZ)
            if value.tzinfo is not None and value.utcoffset() is not None
            else None
            for value in (start_at, end_at)
        ]
        if any(value is None for value in local_values):
            raise ValueError("timezone required")
        if any(value.minute or value.second or value.microsecond for value in local_values):
            raise DomainError(422, "WHOLE_HOUR_REQUIRED", "预约时间必须为整点")
        start, end = to_utc(start_at), to_utc(end_at)
    except ValueError as exc:
        raise DomainError(422, "TIMEZONE_REQUIRED", "时间必须包含时区") from exc
    current = as_utc(precise_utcnow() if now is None else now).replace(tzinfo=None)
    local_start = as_utc(start).astimezone(BUSINESS_TZ)
    local_end = as_utc(end).astimezone(BUSINESS_TZ)
    today = as_utc(current).astimezone(BUSINESS_TZ).date()
    if start < current or not timedelta(hours=1) <= end - start <= timedelta(hours=max_hours):
        raise DomainError(422, "INVALID_PERIOD", "预约须从当前时刻起且不超过最大预约时长")
    if (
        local_start.date() != today
        or local_end.date() != today
        or local_start.time() < time(8)
        or local_end.time() > time(22)
    ):
        raise DomainError(422, "OUTSIDE_WINDOW", "预约须在当天 08:00 至 22:00 内")
    return start, end
