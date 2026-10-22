from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError, conflict, forbidden, not_found
from app.core.time import as_utc, to_utc, utcnow
from app.modules.audit.service import record_operation
from app.modules.auth.permissions import Principal
from app.modules.m2.models import Application
from app.modules.m2.schemas import CreateApplication, ReviewApplication
from app.modules.resources.service import (
    check_booking_eligibility,
    lock_room,
    occupy_room,
    validate_period,
)


def can_read(actor: Principal, applicant_id: int) -> bool:
    return actor.user_id == applicant_id or actor.has("m2:approve")


def get_application(session: Session, application_id: int, actor: Principal) -> Application:
    row = session.get(Application, application_id)
    if row is None or not can_read(actor, row.applicant_id):
        raise not_found()
    return row


def list_applications(session, actor, *, status=None, mine=False, page=1, page_size=20):
    query = select(Application)
    if mine or not actor.has("m2:approve"):
        query = query.where(Application.applicant_id == actor.user_id)
    if status is not None:
        query = query.where(Application.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        session.scalars(
            query.order_by(Application.created_at.desc(), Application.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


def create_application(session: Session, body: CreateApplication, actor: Principal):
    actor.require("m2:create")
    if actor.identity_type != "TEACHER":
        raise forbidden()
    room = lock_room(session, body.room_id)
    payload = {
        "room_id": body.room_id,
        "title": body.title,
        "purpose": body.purpose,
        "participant_count": body.participant_count,
        "start_at": to_utc(body.start_at),
        "end_at": to_utc(body.end_at),
    }
    existing = session.scalar(
        select(Application)
        .where(
            Application.applicant_id == actor.user_id,
            Application.request_key == str(body.request_key),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in payload.items()):
            raise conflict("同一请求标识不能提交不同申请内容")
        return existing, False
    check_booking_eligibility(actor, room)
    validate_period(room, body.start_at, body.end_at, m1=False)
    if body.participant_count > room.capacity:
        raise DomainError(422, "CAPACITY_EXCEEDED", "申请人数超过教室容量")
    row = Application(**payload, applicant_id=actor.user_id, request_key=str(body.request_key))
    session.add(row)
    session.flush()
    record_operation(
        session,
        module="M2",
        business_id=row.id,
        action="CREATE_APPLICATION",
        actor_id=actor.user_id,
        to_status="PENDING",
    )
    return row, True


def locked_application(session, application_id, actor):
    # Read immutable routing/ownership fields first; acquire room lock BEFORE application lock.
    route = session.execute(
        select(Application.room_id, Application.applicant_id).where(
            Application.id == application_id
        )
    ).one_or_none()
    if route is None or not can_read(actor, route.applicant_id):
        raise not_found()
    room = lock_room(session, route.room_id)
    row = session.scalar(
        select(Application)
        .where(Application.id == application_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise not_found()
    return room, row


def review_application(
    session: Session, application_id: int, body: ReviewApplication, actor: Principal
) -> Application:
    actor.require("m2:approve")
    room, row = locked_application(session, application_id, actor)
    if row.applicant_id == actor.user_id:
        raise forbidden()
    target = "APPROVED" if body.passed else "REJECTED"
    if row.status == target:
        return row  # Same decision retries preserve the first reviewer, comment and audit.
    if row.status != "PENDING":
        raise conflict("申请已处理，请刷新后查看最新状态")
    if body.passed:
        if row.participant_count > room.capacity:
            raise DomainError(422, "CAPACITY_EXCEEDED", "申请人数超过教室当前容量")
        occupy_room(
            session,
            room_id=row.room_id,
            application_id=row.id,
            applicant_id=row.applicant_id,
            start_at=as_utc(row.start_at),
            end_at=as_utc(row.end_at),
            actor=actor,
        )
    row.status = target
    row.reviewer_id, row.reviewed_at = actor.user_id, utcnow()
    row.review_comment = body.comment or None
    record_operation(
        session,
        module="M2",
        business_id=row.id,
        action="REVIEW_APPLICATION",
        actor_id=actor.user_id,
        from_status="PENDING",
        to_status=target,
        note=row.review_comment,
    )
    session.flush()
    return row


def cancel_application(session: Session, application_id: int, actor: Principal) -> Application:
    _, row = locked_application(session, application_id, actor)
    if row.applicant_id != actor.user_id:
        raise forbidden()
    if row.status == "CANCELLED":
        return row
    if row.status != "PENDING":
        raise conflict("只能撤销待审批申请")
    row.status, row.cancelled_at = "CANCELLED", utcnow()
    record_operation(
        session,
        module="M2",
        business_id=row.id,
        action="CANCEL_APPLICATION",
        actor_id=actor.user_id,
        from_status="PENDING",
        to_status="CANCELLED",
    )
    session.flush()
    return row
