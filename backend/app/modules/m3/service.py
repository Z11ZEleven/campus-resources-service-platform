"""M3 business transitions, inside the caller's single database transaction."""

import json

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import require_transaction
from app.core.errors import DomainError, conflict, forbidden, not_found
from app.core.time import utcnow
from app.modules.audit.service import list_operations, record_operation
from app.modules.auth.models import User, UserIdentity, UserRole
from app.modules.auth.permissions import Principal, load_principal
from app.modules.m3.models import WorkOrder, WorkOrderEscalation, WorkOrderRating
from app.modules.m3.schemas import (
    Acceptance,
    AssignWorkOrder,
    CreateWorkOrder,
    SubmitCompletion,
    SubmitRating,
)
from app.modules.notifications.service import publish_notifications
from app.modules.resources.service import (
    check_room_access,
    lock_room,
    release_source,
    start_maintenance,
)


def _visibility(actor: Principal):
    if actor.has("m3:assign"):
        return None
    conditions = [WorkOrder.reporter_id == actor.user_id]
    if actor.has("m3:start"):
        conditions.append(WorkOrder.assignee_id == actor.user_id)
    return or_(*conditions)


def _can_read(actor: Principal, order: WorkOrder) -> bool:
    return (
        actor.has("m3:assign")
        or order.reporter_id == actor.user_id
        or (actor.has("m3:start") and order.assignee_id == actor.user_id)
    )


def _locked_order(session: Session, order_id: int) -> WorkOrder:
    require_transaction(session)
    # room_id is immutable. Read it without a business-row lock, then always
    # take the shared room lock before locking and refreshing the work order.
    room_id = session.scalar(select(WorkOrder.room_id).where(WorkOrder.id == order_id))
    if room_id is None:
        raise not_found()
    lock_room(session, room_id)
    order = session.scalar(
        select(WorkOrder)
        .where(WorkOrder.id == order_id, WorkOrder.room_id == room_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise not_found()
    return order


def property_admin_ids(session: Session) -> list[int]:
    """Resolve current valid administrators through the public identity models."""
    return list(session.scalars(
        select(User.id)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .where(
            User.is_active.is_(True), UserIdentity.is_active.is_(True),
            UserRole.role_code == "PROPERTY_ADMIN",
        )
        .distinct()
    ))


def _notify_event(session: Session, order: WorkOrder, action: str):
    recipients = set(property_admin_ids(session)) | {order.reporter_id}
    if order.assignee_id is not None:
        recipients.add(order.assignee_id)
    labels = {
        "CREATE_WORK_ORDER": "已上报",
        "ASSIGN_WORK_ORDER": "已派单",
        "START_REPAIR": "开始维修",
        "SUBMIT_COMPLETION": "待验收",
        "ACCEPT_WORK_ORDER": "验收通过",
        "REJECT_WORK_ORDER": "已驳回返修",
        "RATE_WORK_ORDER": "已评价",
    }
    label = labels[action]
    publish_notifications(
        session,
        recipients=recipients,
        source_module="M3",
        business_id=order.id,
        event_key=f"{order.id}:{action}:{order.completion_round}",
        title=f"报修工单{label}",
        content=f"工单「{order.title}」{label}，请查看工单详情。",
        link=f"/m3/work-orders/{order.id}",
    )


def _transition(session, order, actor, action, status, *, note=None):
    previous = order.status
    if previous == status:
        return order
    order.status = status
    order.stage_started_at = utcnow()
    session.flush()
    record_operation(
        session,
        module="M3",
        business_id=order.id,
        action=action,
        actor_id=actor.user_id,
        from_status=previous,
        to_status=status,
        note=json.dumps(note, ensure_ascii=False) if note is not None else None,
    )
    _notify_event(session, order, action)
    return order


def create_work_order(session: Session, *, body: CreateWorkOrder, actor: Principal) -> WorkOrder:
    actor.require("m3:create")
    require_transaction(session)
    room = lock_room(session, body.room_id)
    request_key = str(body.request_key)
    existing = session.scalar(
        select(WorkOrder)
        .where(WorkOrder.reporter_id == actor.user_id, WorkOrder.request_key == request_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        original = (existing.room_id, existing.title, existing.description, existing.category)
        if original != (body.room_id, body.title, body.description, body.category):
            raise conflict("相同请求键的上报内容不一致")
        return existing
    check_room_access(actor, room)
    order = WorkOrder(
        reporter_id=actor.user_id,
        room_id=room.id,
        title=body.title,
        description=body.description,
        category=body.category,
        status="REPORTED",
        request_key=request_key,
        completion_round=0,
    )
    session.add(order)
    # A simultaneous use of this key for another room is serialized by the
    # database unique key. IntegrityError reaches the shared 409 handler;
    # the shared request dependency rolls back the entire losing transaction.
    session.flush()
    record_operation(
        session,
        module="M3",
        business_id=order.id,
        action="CREATE_WORK_ORDER",
        actor_id=actor.user_id,
        to_status="REPORTED",
    )
    _notify_event(session, order, "CREATE_WORK_ORDER")
    return order


def list_work_orders(
    session: Session,
    *,
    actor: Principal,
    page: int,
    page_size: int,
    status: str | None = None,
    category: str | None = None,
    room_id: int | None = None,
):
    query = select(WorkOrder)
    visible = _visibility(actor)
    if visible is not None:
        query = query.where(visible)
    if status is not None:
        query = query.where(WorkOrder.status == status)
    if category is not None:
        query = query.where(WorkOrder.category == category)
    if room_id is not None:
        query = query.where(WorkOrder.room_id == room_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        session.scalars(
            query.order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


def work_order_detail(session: Session, *, order_id: int, actor: Principal):
    order = session.get(WorkOrder, order_id)
    if order is None or not _can_read(actor, order):
        raise not_found()
    history = list_operations(session, module="M3", business_id=order.id)
    return order, history


def work_order_extensions(session: Session, order: WorkOrder):
    from app.modules.m3.timeouts import timeout_info

    rating = session.scalar(
        select(WorkOrderRating).where(WorkOrderRating.work_order_id == order.id)
    )
    escalations = list(session.scalars(
        select(WorkOrderEscalation)
        .where(WorkOrderEscalation.work_order_id == order.id)
        .order_by(WorkOrderEscalation.escalated_at, WorkOrderEscalation.id)
    ))
    return rating, escalations, timeout_info(session, order)


def submit_rating(
    session: Session, *, order_id: int, body: SubmitRating, actor: Principal
) -> WorkOrderRating:
    order = _locked_order(session, order_id)
    if not _can_read(actor, order):
        raise not_found()
    if order.reporter_id != actor.user_id:
        raise forbidden()
    if order.status != "COMPLETED":
        raise conflict("只有验收完成的工单可以评价")
    existing = session.scalar(
        select(WorkOrderRating).where(WorkOrderRating.work_order_id == order.id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if existing is not None:
        if (existing.score, existing.comment) != (body.score, body.comment):
            raise conflict("评价提交后不能修改")
        return existing
    rating = WorkOrderRating(
        work_order_id=order.id, reporter_id=actor.user_id,
        score=body.score, comment=body.comment,
    )
    session.add(rating)
    session.flush()
    record_operation(
        session, module="M3", business_id=order.id,
        action="RATE_WORK_ORDER", actor_id=actor.user_id,
        from_status=order.status, to_status=order.status,
        note=json.dumps({"score": body.score, "comment": body.comment}, ensure_ascii=False),
    )
    _notify_event(session, order, "RATE_WORK_ORDER")
    return rating


def assign_work_order(
    session: Session, *, order_id: int, body: AssignWorkOrder, actor: Principal
) -> WorkOrder:
    actor.require("m3:assign")
    order = _locked_order(session, order_id)
    if order.status != "REPORTED":
        raise conflict("只有待派单工单可以派单，本轮不支持转派")
    try:
        assignee = load_principal(session, body.assignee_id)
    except DomainError as error:
        raise DomainError(422, "INVALID_ASSIGNEE", "请选择有效的维修人员") from error
    if not assignee.has("m3:start"):
        raise DomainError(422, "INVALID_ASSIGNEE", "请选择有效的维修人员")
    order.assignee_id = assignee.user_id
    order.assigned_by = actor.user_id
    order.assigned_at = utcnow()
    return _transition(
        session, order, actor, "ASSIGN_WORK_ORDER", "ASSIGNED",
        note={"assignee_id": str(assignee.user_id)},
    )


def start_work_order(session: Session, *, order_id: int, actor: Principal) -> WorkOrder:
    actor.require("m3:start")
    order = _locked_order(session, order_id)
    if order.status not in {"ASSIGNED", "REPAIRING"}:
        raise conflict("工单当前状态不允许开始维修")
    if order.assignee_id != actor.user_id:
        raise forbidden()
    if order.status == "REPAIRING":
        return order
    start_maintenance(session, room_id=order.room_id, work_order_id=order.id, actor=actor)
    order.started_at = utcnow()
    return _transition(session, order, actor, "START_REPAIR", "REPAIRING")


def submit_completion(
    session: Session, *, order_id: int, body: SubmitCompletion, actor: Principal
) -> WorkOrder:
    actor.require("m3:complete")
    order = _locked_order(session, order_id)
    if order.status != "REPAIRING":
        raise conflict("只有维修中的工单可以提交完工")
    if order.assignee_id != actor.user_id:
        raise forbidden()
    if order.completion_round == 4294967295:
        raise conflict("工单验收批次已达到上限")
    order.completion_note = body.completion_note
    order.submitted_at = utcnow()
    order.completion_round += 1
    return _transition(
        session, order, actor, "SUBMIT_COMPLETION", "WAIT_ACCEPTANCE",
        note={"completion_round": order.completion_round, "completion_note": body.completion_note},
    )


def accept_work_order(
    session: Session, *, order_id: int, body: Acceptance, actor: Principal
) -> WorkOrder:
    actor.require("m3:accept")
    order = _locked_order(session, order_id)
    if order.assignee_id == actor.user_id:
        raise forbidden()
    # Check the completion batch BEFORE the completed-state replay path, so an
    # old acceptance can never succeed after a later repair/acceptance round.
    if body.expected_completion_round != order.completion_round:
        raise conflict("验收批次已变化，请刷新工单后重试")
    if order.status == "COMPLETED" and body.passed:
        return order
    if order.status != "WAIT_ACCEPTANCE":
        raise conflict("只有待验收工单可以处理本轮验收")
    order.acceptance_comment = body.comment
    note = {"completion_round": order.completion_round, "passed": body.passed, "comment": body.comment}
    if body.passed:
        release_source(
            session, room_id=order.room_id, source_module="M3", source_id=order.id, actor=actor
        )
        order.accepted_by = actor.user_id
        order.accepted_at = utcnow()
        return _transition(session, order, actor, "ACCEPT_WORK_ORDER", "COMPLETED", note=note)
    return _transition(session, order, actor, "REJECT_WORK_ORDER", "REPAIRING", note=note)
