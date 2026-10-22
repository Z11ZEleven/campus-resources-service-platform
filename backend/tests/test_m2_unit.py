from datetime import datetime, timedelta

import pytest

from app.core.errors import DomainError
from app.core.time import BUSINESS_TZ, to_utc
from app.modules.m2.service import deadline
from app.modules.resources import service
from app.modules.resources.models import Room


@pytest.mark.parametrize("today", [(2026, 1, 30), (2026, 12, 30), (2028, 2, 28)])
def test_two_natural_days_across_month_year_and_leap_day(monkeypatch, today):
    current = datetime(*today, 20, 0, tzinfo=BUSINESS_TZ)
    monkeypatch.setattr(service, "business_date", lambda: current.date())
    monkeypatch.setattr(service, "utcnow", lambda: to_utc(current))
    room = Room(
        open_time=datetime.min.replace(hour=8).time(),
        close_time=datetime.min.replace(hour=22).time(),
    )
    permitted = (current + timedelta(days=2)).replace(hour=8)
    assert service.validate_whole_room_period(
        room, permitted, permitted + timedelta(hours=4), minimum_days=2
    )
    early = permitted - timedelta(days=1)
    with pytest.raises(DomainError) as error:
        service.validate_whole_room_period(room, early, early + timedelta(hours=1), minimum_days=2)
    assert error.value.code == "ADVANCE_REQUIRED"
    expected = (current + timedelta(days=1)).replace(hour=23, minute=59, second=59)
    assert deadline(to_utc(current)) == to_utc(expected)


def test_more_than_seven_days_is_permitted_for_m2(monkeypatch):
    current = datetime(2026, 10, 22, 10, tzinfo=BUSINESS_TZ)
    monkeypatch.setattr(service, "business_date", lambda: current.date())
    monkeypatch.setattr(service, "utcnow", lambda: to_utc(current))
    room = Room(
        open_time=datetime.min.replace(hour=8).time(),
        close_time=datetime.min.replace(hour=22).time(),
    )
    start = (current + timedelta(days=40)).replace(hour=18)
    assert service.validate_whole_room_period(
        room, start, start + timedelta(hours=4), minimum_days=2
    )
