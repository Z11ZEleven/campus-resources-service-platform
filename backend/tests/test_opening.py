from datetime import datetime, time
from types import SimpleNamespace

from app.core.time import BUSINESS_TZ, to_utc
from app.modules.resources import opening


def moment(day, hour=0, minute=0):
    return to_utc(datetime(2026, 10, day, hour, minute, tzinfo=BUSINESS_TZ))


def test_whole_interval_rejects_gap_and_merges_adjacency():
    intervals = [(moment(26, 8), moment(26, 10)), (moment(26, 11), moment(26, 12))]
    assert not opening.covers(intervals, moment(26, 9), moment(26, 12))
    assert opening.covers(
        [*intervals, (moment(26, 10), moment(26, 11))], moment(26, 8), moment(26, 12)
    )
    assert not opening.covers(intervals, moment(26, 10), moment(26, 11))
    assert not opening.covers(intervals, moment(26, 9), moment(26, 9))


def test_overnight_weekly_midnight_and_closure_precedence():
    room = SimpleNamespace(open_time=time(8), close_time=time(22), opening_rules_enabled=True)
    weekly = [SimpleNamespace(weekday=0, start_time=time(22), end_time=time(2))]
    exceptions = [
        SimpleNamespace(kind="OPEN", start_at=moment(27, 1), end_at=moment(27, 3)),
        SimpleNamespace(kind="CLOSE", start_at=moment(27, 0, 30), end_at=moment(27, 1)),
    ]
    result = opening.calculate(room, weekly, exceptions, moment(26), moment(28))
    assert opening.covers(result, moment(26, 22), moment(27))
    assert opening.covers(result, moment(27, 1), moment(27, 3))
    assert not opening.covers(result, moment(26, 23), moment(27, 2))
    assert not opening.covers(result, moment(27, 3), moment(27, 4))


def test_legacy_daily_fallback_and_explicit_empty_schedule():
    room = SimpleNamespace(open_time=time(8), close_time=time(22), opening_rules_enabled=False)
    result = opening.calculate(room, [], [], moment(26), moment(27))
    assert opening.covers(result, moment(26, 8), moment(26, 22))
    room.opening_rules_enabled = True
    assert opening.calculate(room, [], [], moment(26), moment(27)) == []
