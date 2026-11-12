from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.http import error_body
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.m1 import checkin, reservations
from app.modules.m1.search import search_seats

router = APIRouter(prefix="/api/v1/m1", tags=["M1"])


@router.get("/seats")
def seats(
    session: Db,
    actor: CurrentUser,
    identity_id: int,
    start_at: datetime,
    end_at: datetime,
    room_id: int | None = None,
    has_fixed_socket: bool | None = None,
    has_mobile_socket: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return search_seats(
        session,
        actor,
        identity_id,
        start_at,
        end_at,
        room_id,
        has_fixed_socket,
        has_mobile_socket,
        page,
        page_size,
    )


class ReservationInput(BaseModel):
    identity_id: int = Field(gt=0)
    seat_id: int = Field(gt=0)
    start_at: datetime
    end_at: datetime


@router.post("/reservations", status_code=201)
def create_reservation(
    body: ReservationInput,
    request: Request,
    session: Db,
    actor: CurrentUser,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)],
):
    return reservations.create(
        session,
        actor,
        request.state.auth_session.id,
        body.identity_id,
        body.seat_id,
        body.start_at,
        body.end_at,
        idempotency_key,
        request.state.request_id,
    )


@router.get("/reservations")
def list_reservations(
    session: Db,
    actor: CurrentUser,
    status: Literal["RESERVED", "CHECKED_IN", "CANCELLED", "NO_SHOW", "COMPLETED", "INTERRUPTED"]
    | None = None,
    day: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return reservations.list_own(session, actor, status, day, page, page_size)


@router.get("/reservations/{reservation_id}")
def reservation_detail(reservation_id: int, session: Db, actor: CurrentUser):
    return reservations.get_own(session, actor, reservation_id)


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, request: Request, session: Db, actor: CurrentUser):
    return reservations.cancel(
        session, actor, request.state.auth_session.id, reservation_id, request.state.request_id
    )


class CheckinInput(BaseModel):
    code: Any = None


@router.get("/admin/rooms/{room_id}/check-in-code")
def read_checkin_code(
    room_id: int, request: Request, response: Response, session: Db, actor: CurrentUser
):
    response.headers["Cache-Control"] = "no-store"
    return checkin.admin_code(session, actor, request.state.auth_session.id, room_id)


@router.post("/admin/rooms/{room_id}/check-in-code/rotate")
def rotate_checkin_code(
    room_id: int, request: Request, response: Response, session: Db, actor: CurrentUser
):
    response.headers["Cache-Control"] = "no-store"
    return checkin.admin_code(session, actor, request.state.auth_session.id, room_id, rotate=True)


@router.post("/reservations/{reservation_id}/check-in")
def check_in(
    reservation_id: int, body: CheckinInput, request: Request, session: Db, actor: CurrentUser
):
    result, error = checkin.check_in(
        session,
        actor,
        request.state.auth_session.id,
        reservation_id,
        body.code if isinstance(body.code, str) else "",
        request.state.request_id,
    )
    if error:
        status, code, message = error
        return JSONResponse(
            status_code=status,
            content=error_body(request.state.request_id, code, message),
            headers={"Cache-Control": "no-store"},
        )
    return result
