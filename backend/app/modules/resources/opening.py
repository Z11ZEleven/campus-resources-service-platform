"""Half-open opening intervals. All stored datetime values are naive UTC."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.time import BUSINESS_TZ, as_utc, to_utc


def merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def covers(intervals, start, end):
    return end > start and any(a <= start and end <= b for a, b in merge(intervals))


def calculate(room, weekly, exceptions, start, end):
    opens = []
    day = as_utc(start).astimezone(BUSINESS_TZ).date() - timedelta(days=1)
    last = as_utc(end).astimezone(BUSINESS_TZ).date()
    while day <= last:
        rules = [r for r in weekly if r.weekday == day.weekday()]
        pairs = [(r.start_time, r.end_time) for r in rules]
        if not room.opening_rules_enabled:
            pairs = [(room.open_time, room.close_time)]
        for begin, finish in pairs:
            next_day = day + timedelta(days=1) if finish <= begin else day
            opens.append(
                (
                    to_utc(datetime.combine(day, begin, BUSINESS_TZ)),
                    to_utc(datetime.combine(next_day, finish, BUSINESS_TZ)),
                )
            )
        day += timedelta(days=1)
    opens.extend((r.start_at, r.end_at) for r in exceptions if r.kind == "OPEN")
    result = merge(opens)
    for closure in (r for r in exceptions if r.kind == "CLOSE"):
        pieces = []
        for a, b in result:
            if closure.end_at <= a or closure.start_at >= b:
                pieces.append((a, b))
            else:
                if a < closure.start_at:
                    pieces.append((a, closure.start_at))
                if closure.end_at < b:
                    pieces.append((closure.end_at, b))
        result = pieces
    return [(max(a, start), min(b, end)) for a, b in result if a < end and b > start]


def intervals(session, room, start, end):
    from app.modules.resources.models import OpeningException, OpeningRule

    weekly = list(session.scalars(select(OpeningRule).where(OpeningRule.room_id == room.id)))
    special = list(
        session.scalars(
            select(OpeningException).where(
                OpeningException.room_id == room.id,
                OpeningException.start_at < end,
                OpeningException.end_at > start,
            )
        )
    )
    return calculate(room, weekly, special, start, end)


def is_open(session, room, start, end):
    return covers(intervals(session, room, start, end), start, end)
