from datetime import date, time

from app.core.schema import Id, Output, UtcDateTime


class RoomView(Output):
    id: Id
    code: str
    name: str
    building_name: str
    capacity: int
    department_code: str | None
    booking_mode: str
    campus_code: str
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
