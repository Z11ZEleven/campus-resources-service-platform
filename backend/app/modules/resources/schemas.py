from datetime import date, datetime, time
from typing import Literal

from pydantic import Field, model_validator

from app.core.schema import Id, Input, Output, UtcDateTime


class RoomView(Output):
    id: Id
    code: str
    name: str
    building_id: Id | None
    resource_version: int
    opening_rules_enabled: bool
    building_name: str
    capacity: int
    department_code: str | None
    open_time: time
    close_time: time
    is_active: bool


class SeatView(Output):
    id: Id
    room_id: Id
    seat_no: str
    has_fixed_socket: bool
    has_mobile_socket: bool
    is_active: bool


class SlotView(Output):
    seat_id: Id
    slot_start: UtcDateTime
    slot_end: UtcDateTime
    status: str
    primary_block_id: Id | None
    source_module: str | None
    source_count: int
    resource_version: int
    bookable: bool


class AvailabilityView(Output):
    room_id: Id
    date: date
    seats: list[SeatView]
    slots: list[SlotView]


class BuildingView(Output):
    id: Id
    code: str
    name: str
    is_active: bool


class BuildingInput(Input):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class RoomInput(Input):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    building_id: Id
    capacity: int = Field(gt=0, le=100000)
    department_code: str | None = Field(default=None, max_length=32)
    open_time: time = time(8)
    close_time: time = time(22)
    is_active: bool = True

    @model_validator(mode="after")
    def hours(self):
        if self.open_time >= self.close_time:
            raise ValueError("旧版日开放时间必须为正区间，跨夜请使用周规则")
        if self.open_time.tzinfo or self.close_time.tzinfo:
            raise ValueError("开放时间为校园本地时间")
        return self


class SeatInput(Input):
    seat_no: str = Field(min_length=1, max_length=32)
    has_fixed_socket: bool = False
    has_mobile_socket: bool = False
    is_active: bool = True


class WeeklyInput(Input):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def hours(self):
        if self.start_time == self.end_time or self.start_time.tzinfo or self.end_time.tzinfo:
            raise ValueError("周规则必须为非空校园本地时段")
        return self


class ExceptionInput(Input):
    kind: Literal["OPEN", "CLOSE"]
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def period(self):
        from app.core.time import to_utc

        if to_utc(self.end_at) <= to_utc(self.start_at):
            raise ValueError("特殊时段必须为正区间且包含时区")
        return self


class ScheduleInput(Input):
    weekly: list[WeeklyInput] = Field(max_length=100)
    exceptions: list[ExceptionInput] = Field(default_factory=list, max_length=200)
