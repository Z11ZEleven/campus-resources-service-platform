"""Read-only persistence. Callers retain authorization and transaction ownership."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.m1.models import Reservation, ReservationEvent


class ReservationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, reservation_id: int) -> Reservation | None:
        return self.session.get(Reservation, reservation_id)

    def get_by_request_key(self, user_id: int, request_key: str) -> Reservation | None:
        return self.session.scalar(
            select(Reservation).where(
                Reservation.user_id == user_id,
                Reservation.create_request_key == request_key,
            )
        )

    def list_for_user(self, user_id: int, *, limit: int = 50, offset: int = 0) -> list[Reservation]:
        return list(
            self.session.scalars(
                select(Reservation)
                .where(
                    Reservation.user_id == user_id,
                )
                .order_by(Reservation.created_at.desc(), Reservation.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    def list_events(self, reservation_id: int) -> list[ReservationEvent]:
        return list(
            self.session.scalars(
                select(ReservationEvent)
                .where(
                    ReservationEvent.reservation_id == reservation_id,
                )
                .order_by(ReservationEvent.created_at, ReservationEvent.id)
            )
        )
