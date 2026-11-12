"""M1 conditions layered on the public D authorization contract, never a second RBAC."""

from app.core.errors import forbidden, not_found
from app.modules.resources.service import check_booking_eligibility


def require_own(actor, owner_id: int, permission: str):
    actor.require(permission)
    if actor.user_id != owner_id:
        raise not_found()


def require_student_for_room(actor, room):
    actor.require_for_room("m1:reservation:create", room)
    if actor.identity_type != "STUDENT":
        raise forbidden()
    check_booking_eligibility(actor, room)
