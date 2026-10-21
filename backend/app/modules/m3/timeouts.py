"""Timeout policy and per-order transactional escalation scans."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.time import to_utc, utcnow
from app.modules.m3.models import WorkOrder, WorkOrderEscalation
from app.modules.m3.schemas import TimeoutInfo, TimeoutPolicy
from app.modules.m3.service import _locked_order, property_admin_ids
from app.modules.notifications.service import publish_notifications

logger = logging.getLogger(__name__)


def timeout_policy() -> TimeoutPolicy:
    settings = get_settings()
    return TimeoutPolicy(
        reported_hours=settings.m3_reported_timeout_hours,
        assigned_hours=settings.m3_assigned_timeout_hours,
        repairing_hours=settings.m3_repairing_timeout_hours,
        acceptance_hours=settings.m3_acceptance_timeout_hours,
        scan_interval_seconds=settings.m3_timeout_scan_interval_seconds,
        automatic_scan_enabled=settings.m3_timeout_scan_enabled,
    )


def stage_hours() -> dict[str, int]:
    policy = timeout_policy()
    return {
        "REPORTED": policy.reported_hours,
        "ASSIGNED": policy.assigned_hours,
        "REPAIRING": policy.repairing_hours,
        "WAIT_ACCEPTANCE": policy.acceptance_hours,
    }


def due_at(order: WorkOrder) -> datetime | None:
    hours = stage_hours().get(order.status)
    return order.stage_started_at + timedelta(hours=hours) if hours is not None else None


def _same_stage(order: WorkOrder):
    return (
        WorkOrderEscalation.work_order_id == order.id,
        WorkOrderEscalation.stage == order.status,
        WorkOrderEscalation.completion_round == order.completion_round,
    )


def timeout_info(session: Session, order: WorkOrder) -> TimeoutInfo:
    deadline = due_at(order)
    escalated = deadline is not None and session.scalar(
        select(WorkOrderEscalation.id).where(*_same_stage(order)).limit(1)
    ) is not None
    return TimeoutInfo(
        due_at=deadline,
        is_overdue=deadline is not None and utcnow() > deadline,
        current_stage_escalated=escalated,
    )


def _candidate_query(now: datetime, limit: int):
    # Exclude already escalated stages BEFORE LIMIT. A backlog of old escalated
    # orders must never hide later, still-unhandled work orders from the worker.
    already_escalated = exists().where(
        WorkOrderEscalation.work_order_id == WorkOrder.id,
        WorkOrderEscalation.stage == WorkOrder.status,
        WorkOrderEscalation.completion_round == WorkOrder.completion_round,
    )
    overdue = or_(*(
        and_(WorkOrder.status == stage, WorkOrder.stage_started_at < now - timedelta(hours=hours))
        for stage, hours in stage_hours().items()
    ))
    return (
        select(WorkOrder.id).where(overdue, ~already_escalated)
        .order_by(WorkOrder.stage_started_at, WorkOrder.id).limit(limit)
    )


def _escalate_one(session: Session, order: WorkOrder, *, now: datetime, triggered_by: int | None):
    deadline = due_at(order)
    if deadline is None or now <= deadline:
        return False
    if session.scalar(select(WorkOrderEscalation.id).where(*_same_stage(order))) is not None:
        return False
    labels = {"REPORTED": "待派单", "ASSIGNED": "已派单", "REPAIRING": "维修中",
              "WAIT_ACCEPTANCE": "待验收"}
    reason = f"{labels[order.status]}阶段超过 {stage_hours()[order.status]} 小时，请物业管理员跟进。"
    escalation = WorkOrderEscalation(
        work_order_id=order.id, stage=order.status, completion_round=order.completion_round,
        stage_started_at=order.stage_started_at, due_at=deadline, escalated_at=now,
        reason=reason, triggered_by=triggered_by,
    )
    session.add(escalation)
    order.escalation_count += 1
    session.flush()
    publish_notifications(
        session, recipients=property_admin_ids(session), source_module="M3", business_id=order.id,
        event_key=f"{order.id}:TIMEOUT:{order.status}:{order.completion_round}",
        title="报修工单超时升级", content=f"工单「{order.title}」{reason}",
        link=f"/m3/work-orders/{order.id}",
    )
    return True


def run_timeout_scan(
    *, now: datetime | None = None, limit: int = 200, triggered_by: int | None = None,
) -> dict[str, int]:
    """Scan without initial row locks, then recheck under room -> order locks.

    Explicit ``now`` is an internal test/scheduler facility; HTTP callers can
    never provide it. Each successful order commits independently. A failed
    order is rolled back and left available for the next worker cycle.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if now is not None and now.tzinfo is not None:
        now = to_utc(now)
    cutoff = now or utcnow()
    factory = get_session_factory()
    with factory() as session:
        candidates = list(session.scalars(_candidate_query(cutoff, limit)))
    checked = escalated = 0
    for order_id in candidates:
        checked += 1
        try:
            with factory() as session, session.begin():
                order = _locked_order(session, order_id)
                changed = _escalate_one(
                    session, order, now=now or utcnow(), triggered_by=triggered_by,
                )
            escalated += int(changed)
        except Exception:
            # Deliberately avoid exception text, which may include SQL params.
            logger.error("M3 timeout escalation failed for work order %s; retry next scan", order_id)
    return {"checked": checked, "escalated": escalated}
