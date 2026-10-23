"""本地业务事务。所有修改先锁教室再锁申请，服务只 flush。"""

from datetime import datetime, time, timedelta

from sqlalchemy import and_, exists, func, or_, select, update

from app.core.config import get_settings
from app.core.errors import DomainError, conflict, forbidden, not_found
from app.core.time import BUSINESS_TZ, as_utc, to_utc, utcnow
from app.modules.audit.service import record_operation
from app.modules.auth.models import UserIdentity
from app.modules.auth.permissions import load_principal
from app.modules.auth.scopes import approval_requirement, approver_ids, require_scope, scope_grants
from app.modules.m2.models import (
    ACTIVE,
    WAITING,
    Application,
    ApplicationEvent,
    ApprovalStep,
    ResourceJob,
)
from app.modules.notifications.service import publish_notifications
from app.modules.resources.models import Room
from app.modules.resources.service import (
    check_booking_eligibility,
    lock_room,
    validate_whole_room_period,
)

LABELS = {
    "SUBMITTED": "已提交一级审批",
    "FIRST_APPROVED": "进入二级审批",
    "CONFIRM_RESOURCE": "正在确认资源",
    "APPROVED": "已批准",
    "REJECTED": "已驳回",
    "WITHDRAWN": "已撤回",
    "AUTO_CANCELLED": "已超时撤销",
    "REMINDER": "临近使用时间，请及时审批",
    "RESOURCE_CANCELLED": "资源清理已完成",
}


def deadline(now):
    local = as_utc(now).astimezone(BUSINESS_TZ)
    return to_utc(
        datetime.combine(local.date() + timedelta(days=1), time(23, 59, 59), tzinfo=BUSINESS_TZ)
    )


def steps_for(session, app):
    return list(
        session.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.application_id == app.id)
            .order_by(ApprovalStep.level)
        )
    )


def visibility(session, actor):
    grants = scope_grants(session, actor.user_id)
    if not grants:
        return Application.applicant_id == actor.user_id
    matching = scope_conditions(grants)
    return or_(
        Application.applicant_id == actor.user_id,
        exists(
            select(ApprovalStep.id).where(
                ApprovalStep.application_id == Application.id,
                or_(*matching),
            )
        ),
    )


def scope_conditions(grants):
    matching = []
    for role, kind, key in grants:
        actual_room = (
            Room.department_code == key
            if kind == "DEPARTMENT"
            else and_(Room.department_code.is_(None), Room.campus_code == key)
            if kind == "CAMPUS"
            else Room.id == key
        )
        matching.append(
            and_(
                ApprovalStep.required_role == role,
                ApprovalStep.scope_type == kind,
                ApprovalStep.scope_key == key,
                exists(select(Room.id).where(Room.id == Application.room_id, actual_room)),
            )
        )
    return matching


def get_application(session, app_id, actor):
    row = session.scalar(
        select(Application).where(Application.id == app_id, visibility(session, actor))
    )
    if row is None:
        raise not_found()
    return row


def locked(session, app_id):
    room_id = session.scalar(select(Application.room_id).where(Application.id == app_id))
    if room_id is None:
        raise not_found()
    room = lock_room(session, room_id)
    app = session.scalar(
        select(Application)
        .where(Application.id == app_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if app is None:
        raise not_found()
    return room, app


def event(session, app, action, actor=None, previous=None, reason=None, *, notify=True):
    session.add(
        ApplicationEvent(
            application_id=app.id,
            event_type=action,
            actor_type="USER" if actor else "SYSTEM",
            actor_id=actor.user_id if actor else None,
            from_status=previous,
            to_status=app.status,
            reason=reason,
            version=app.version,
        )
    )
    record_operation(
        session,
        module="M2",
        business_id=app.id,
        action=action,
        actor_id=actor.user_id if actor else None,
        actor_type="USER" if actor else "SYSTEM",
        from_status=previous,
        to_status=app.status,
        note=reason,
    )
    if not notify:
        session.flush()
        return
    recipients = {app.applicant_id}
    if app.current_level:
        current = next(
            (step for step in steps_for(session, app) if step.level == app.current_level), None
        )
        if current:
            recipients.update(
                approver_ids(
                    session, (current.required_role, current.scope_type, current.scope_key)
                )
            )
            recipients.discard(app.applicant_id)
            if action != "REMINDER":
                recipients.add(app.applicant_id)
    publish_notifications(
        session,
        recipients=recipients,
        source_module="M2",
        business_id=app.id,
        event_key=f"application:{app.id}:{app.version}:{action}",
        title=f"教室申请{LABELS.get(action, action)}",
        content=f"申请 #{app.id}：{reason or LABELS.get(action, action)}"[:1000],
        link=f"/m2/applications/{app.id}",
    )
    session.flush()


def transition(session, app, target, action, actor=None, reason=None, *, notify=True, **fields):
    previous, version = app.status, app.version
    fields.update(status=target, version=version + 1, updated_at=utcnow())
    changed = session.execute(
        update(Application)
        .where(
            Application.id == app.id,
            Application.version == version,
            Application.status == previous,
        )
        .values(**fields)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise conflict("申请状态或版本已变化，请刷新")
    session.refresh(app)
    event(session, app, action, actor, previous, reason, notify=notify)
    return app


def add_step(session, app, room, level, now):
    role, kind, key = approval_requirement(room, level)
    step = ApprovalStep(
        application_id=app.id,
        level=level,
        required_role=role,
        scope_type=kind,
        scope_key=key,
        deadline_at=deadline(now),
    )
    session.add(step)
    session.flush()
    return step


def ensure_room_eligible(actor, room):
    if room.booking_mode != "WHOLE_ROOM":
        raise DomainError(422, "ROOM_MODE", "请选择仅整教室预约类型的教室")
    check_booking_eligibility(actor, room)


def create_application(session, body, actor, request_key):
    room = lock_room(session, body.room_id)
    actor = load_principal(session, actor.user_id)
    start, end = to_utc(body.start_at), to_utc(body.end_at)
    existing = session.scalar(
        select(Application)
        .where(
            Application.applicant_id == actor.user_id,
            Application.request_key == request_key,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing:
        if (existing.room_id, existing.purpose, existing.start_at, existing.end_at) != (
            body.room_id,
            body.purpose,
            start,
            end,
        ):
            raise conflict("相同幂等键不能用于不同申请内容")
        return existing, False
    ensure_room_eligible(actor, room)
    validate_whole_room_period(room, body.start_at, body.end_at, minimum_days=2)
    identity = session.scalar(select(UserIdentity).where(UserIdentity.user_id == actor.user_id))
    now = utcnow()
    app = Application(
        applicant_id=actor.user_id,
        applicant_identity_id=identity.id,
        identity_type=actor.identity_type,
        department_code=actor.department_code,
        room_id=room.id,
        purpose=body.purpose,
        start_at=start,
        end_at=end,
        status=WAITING[0],
        current_level=1,
        step_deadline_at=deadline(now),
        request_key=request_key,
    )
    session.add(app)
    session.flush()
    add_step(session, app, room, 1, now)
    event(session, app, "SUBMITTED", actor)
    return app, True


def close_steps(session, app, status):
    for step in steps_for(session, app):
        if step.status in {"PENDING", "CONFIRMING"}:
            step.status = status
            step.version += 1


def expire_if_due(session, app, now=None):
    now = now or utcnow()
    if app.status not in ACTIVE or (now < app.start_at and now < app.step_deadline_at):
        return False
    close_steps(session, app, "EXPIRED")
    job = session.scalar(
        select(ResourceJob).where(ResourceJob.application_id == app.id).with_for_update()
    )
    sync = "NONE"
    if job is not None:
        # 使正在网络调用的旧租约失效；其返回不能把已撤销申请改回批准。
        job.action, job.status = "CANCEL", "PENDING"
        job.lease_token = job.lease_until = None
        job.next_attempt_at = now
        sync = "CANCEL_PENDING"
    transition(
        session,
        app,
        "AUTO_CANCELLED",
        "AUTO_CANCELLED",
        reason="审批期限或使用开始时间已到，申请自动撤销",
        current_level=None,
        step_deadline_at=None,
        finalized_at=now,
        final_reason="审批超时或使用时间已到",
        resource_sync_status=sync,
    )
    return True


def withdraw(session, app_id, body, actor, request_key):
    get_application(session, app_id, actor)
    _, app = locked(session, app_id)
    if app.applicant_id != actor.user_id:
        raise forbidden()
    if app.status == "WITHDRAWN" and app.withdrawn_request_key == request_key:
        return app
    if expire_if_due(session, app):
        return app
    if app.status not in WAITING:
        raise conflict("只有两级待审批中的申请可以撤回")
    if app.version != body.expected_version:
        raise conflict("申请版本已变化，请刷新")
    close_steps(session, app, "CANCELLED")
    return transition(
        session,
        app,
        "WITHDRAWN",
        "WITHDRAWN",
        actor,
        "申请人撤回",
        current_level=None,
        step_deadline_at=None,
        finalized_at=utcnow(),
        withdrawn_request_key=request_key,
        final_reason="申请人撤回",
    )


def review_step(session, step_id, body, actor, request_key, passed):
    route = session.scalar(select(ApprovalStep.application_id).where(ApprovalStep.id == step_id))
    if route is None:
        raise not_found()
    get_application(session, route, actor)
    room, app = locked(session, route)
    step = session.get(ApprovalStep, step_id, populate_existing=True)
    require_scope(session, actor, (step.required_role, step.scope_type, step.scope_key))
    if actor.user_id == app.applicant_id:
        raise forbidden()
    decision = "APPROVE" if passed else "REJECT"
    if not passed and not body.comment:
        raise DomainError(422, "REASON_REQUIRED", "驳回必须填写理由")
    if step.request_key == request_key:
        if (step.decided_by, step.decision, step.decision_reason) != (
            actor.user_id,
            decision,
            body.comment,
        ):
            raise conflict("相同幂等键已用于其他审批内容")
        return app
    if expire_if_due(session, app):
        return app
    if app.status not in WAITING or app.current_level != step.level or step.status != "PENDING":
        raise conflict("当前审批步骤已处理或已失效")
    if app.version != body.expected_version:
        raise conflict("申请版本已变化，请刷新")
    if approval_requirement(room, step.level) != (
        step.required_role,
        step.scope_type,
        step.scope_key,
    ):
        raise conflict("教室归属已变化，需要重新确认审批范围")
    if passed:
        applicant = load_principal(session, app.applicant_id)
        ensure_room_eligible(applicant, room)
        validate_whole_room_period(room, as_utc(app.start_at), as_utc(app.end_at))
    step.decided_by, step.decided_at = actor.user_id, utcnow()
    step.decision, step.decision_reason, step.request_key = decision, body.comment, request_key
    step.version += 1
    if not passed:
        step.status = "REJECTED"
        close_steps(session, app, "CANCELLED")
        return transition(
            session,
            app,
            "REJECTED",
            "REJECTED",
            actor,
            body.comment,
            current_level=None,
            step_deadline_at=None,
            finalized_at=utcnow(),
            final_reason=body.comment,
        )
    if step.level == 1:
        step.status = "APPROVED"
        next_step = add_step(session, app, room, 2, utcnow())
        return transition(
            session,
            app,
            WAITING[1],
            "FIRST_APPROVED",
            actor,
            current_level=2,
            step_deadline_at=next_step.deadline_at,
        )
    step.status = "CONFIRMING"
    payload = {
        "source_id": str(app.id),
        "revision": 1,
        "room_id": str(app.room_id),
        "applicant_id": str(app.applicant_id),
        "actor_id": str(actor.user_id),
        "start_at": as_utc(app.start_at).isoformat(),
        "end_at": as_utc(app.end_at).isoformat(),
        "expires_at": as_utc(min(app.step_deadline_at, app.start_at)).isoformat(),
    }
    session.add(ResourceJob(application_id=app.id, payload=payload))
    return transition(
        session,
        app,
        "RESOURCE_CONFIRMING",
        "CONFIRM_RESOURCE",
        actor,
        resource_sync_status="PENDING",
    )


def reject_overlapping(session, winner):
    rows = list(
        session.scalars(
            select(Application)
            .where(
                Application.room_id == winner.room_id,
                Application.id != winner.id,
                Application.status.in_(WAITING),
                Application.start_at < winner.end_at,
                Application.end_at > winner.start_at,
            )
            .order_by(Application.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for app in rows:
        close_steps(session, app, "CANCELLED")
        transition(
            session,
            app,
            "REJECTED",
            "REJECTED",
            reason="教室对应时段已被其他申请占用",
            current_level=None,
            step_deadline_at=None,
            finalized_at=utcnow(),
            final_reason="教室对应时段已被其他申请占用",
        )


def scan_local(session, app_id):
    _, app = locked(session, app_id)
    if expire_if_due(session, app):
        return "expired"
    if app.status not in WAITING:
        return "unchanged"
    threshold = utcnow() + timedelta(hours=get_settings().m2_reminder_hours)
    if app.start_at > threshold:
        return "unchanged"
    existing = session.scalar(
        select(ApplicationEvent.id).where(
            ApplicationEvent.application_id == app.id,
            ApplicationEvent.version == app.version,
            ApplicationEvent.event_type == "REMINDER",
        )
    )
    if existing is None:
        event(session, app, "REMINDER", reason="临近使用时间，请及时处理当前审批步骤")
        return "reminded"
    return "unchanged"


def list_applications(session, actor, *, mine=False, status=None, page=1, page_size=20):
    q = select(Application).where(
        Application.applicant_id == actor.user_id if mine else visibility(session, actor)
    )
    if status:
        q = q.where(Application.status == status)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    return {
        "items": list(
            session.scalars(
                q.order_by(Application.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def approval_tasks(session, actor, page, page_size):
    grants = scope_grants(session, actor.user_id)
    if not grants:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    matching = or_(*scope_conditions(grants))
    q = (
        select(ApprovalStep)
        .join(Application, Application.id == ApprovalStep.application_id)
        .where(
            matching,
            ApprovalStep.status == "PENDING",
            Application.status.in_(WAITING),
            Application.applicant_id != actor.user_id,
            Application.start_at > utcnow(),
            ApprovalStep.deadline_at > utcnow(),
        )
    )
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    return {
        "items": list(
            session.scalars(
                q.order_by(ApprovalStep.deadline_at, ApprovalStep.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def candidate_rooms(session, actor, page, page_size):
    q = select(Room).where(
        Room.booking_mode == "WHOLE_ROOM",
        Room.is_active.is_(True),
        or_(Room.department_code.is_(None), Room.department_code == actor.department_code),
    )
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    return {
        "items": list(
            session.scalars(q.order_by(Room.id).offset((page - 1) * page_size).limit(page_size))
        ),
        "total": total,
        "page": page,
        "page_size": page_size,
    }
