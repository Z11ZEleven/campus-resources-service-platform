"""持久化资源任务：领取、网络请求、确认分三段执行，不持锁等待网络。"""

from datetime import timedelta
from uuid import uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.schema import DecimalId
from app.core.time import to_utc, utcnow
from app.modules.m2 import service
from app.modules.m2.models import ApprovalStep, ResourceJob


class Outcome(BaseModel):
    source_module: str
    source_id: DecimalId
    revision: int
    room_id: DecimalId
    start_at: AwareDatetime
    end_at: AwareDatetime
    status: str
    resource_hold_id: DecimalId | None = None
    reason_code: str | None = None
    reason: str | None = None


def send(payload, action):
    settings = get_settings()
    if settings.resource_service_key is None:
        raise RuntimeError("RESOURCE_SERVICE_NOT_CONFIGURED")
    endpoint = "/api/v1/resource-commands/m2" + ("/cancel" if action == "CANCEL" else "")
    with httpx.Client(
        base_url=str(settings.m2_resource_base_url),
        timeout=settings.m2_resource_timeout_seconds,
        trust_env=False,
    ) as client:
        response = client.post(
            endpoint,
            json=payload,
            headers={
                "Authorization": "Bearer " + settings.resource_service_key.get_secret_value(),
            },
        )
        response.raise_for_status()
        result = Outcome.model_validate(response.json())
    from datetime import datetime

    if (
        result.source_module != "M2"
        or result.source_id != int(payload["source_id"])
        or result.room_id != int(payload["room_id"])
        or result.revision != payload["revision"]
        or to_utc(result.start_at) != to_utc(datetime.fromisoformat(payload["start_at"]))
        or to_utc(result.end_at) != to_utc(datetime.fromisoformat(payload["end_at"]))
    ):
        raise RuntimeError("RESOURCE_RESPONSE_MISMATCH")
    allowed = {"CANCELLED"} if action == "CANCEL" else {"SUCCEEDED", "REJECTED", "CANCELLED"}
    if result.status not in allowed or (
        result.status == "SUCCEEDED" and result.resource_hold_id is None
    ):
        raise RuntimeError("RESOURCE_RESPONSE_INCOMPLETE")
    if result.status == "REJECTED" and not result.reason:
        raise RuntimeError("RESOURCE_REJECTION_INCOMPLETE")
    return result


def claim(app_id):
    with get_session_factory()() as session, session.begin():
        _, app = service.locked(session, app_id)
        service.expire_if_due(session, app)
        job = session.scalar(
            select(ResourceJob)
            .where(ResourceJob.application_id == app.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = utcnow()
        if job is None or job.status != "PENDING" or job.next_attempt_at > now:
            return None
        if job.lease_until and job.lease_until > now:
            return None
        job.lease_token = str(uuid4())
        job.lease_until = now + timedelta(
            seconds=get_settings().m2_resource_timeout_seconds * 3 + 5
        )
        job.attempts += 1
        session.flush()
        return job.action, dict(job.payload), job.lease_token


def dispatch_one(app_id):
    claimed = claim(app_id)
    if claimed is None:
        return False
    action, payload, token = claimed
    try:
        outcome = send(payload, action)
    except (httpx.HTTPError, ValidationError, ValueError, RuntimeError):
        with get_session_factory()() as session, session.begin():
            _, app = service.locked(session, app_id)
            job = session.scalar(
                select(ResourceJob).where(ResourceJob.application_id == app.id).with_for_update()
            )
            if job.lease_token != token:
                return False
            job.lease_token = job.lease_until = None
            job.last_error = "资源结果暂不可确认，后台会使用同一命令重试"
            job.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** min(job.attempts, 8)))
            service.transition(
                session, app, app.status, "RESOURCE_RETRY", reason=job.last_error, notify=False
            )
        return False
    with get_session_factory()() as session, session.begin():
        _, app = service.locked(session, app_id)
        job = session.scalar(
            select(ResourceJob)
            .where(ResourceJob.application_id == app.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.lease_token != token or job.status != "PENDING":
            return False
        if action == "ACQUIRE" and service.expire_if_due(session, app):
            return False
        if action == "CANCEL":
            if app.status != "AUTO_CANCELLED":
                return False
            service.transition(
                session,
                app,
                "AUTO_CANCELLED",
                "RESOURCE_CANCELLED",
                reason="超时资源清理已确认",
                resource_sync_status="CANCELLED",
            )
        else:
            if app.status != "RESOURCE_CONFIRMING":
                return False
            step = session.scalar(
                select(ApprovalStep).where(
                    ApprovalStep.application_id == app.id, ApprovalStep.level == 2
                )
            )
            step.version += 1
            if outcome.status == "SUCCEEDED":
                step.status = "APPROVED"
                service.transition(
                    session,
                    app,
                    "APPROVED",
                    "APPROVED",
                    reason="已确认整教室占用",
                    current_level=None,
                    step_deadline_at=None,
                    finalized_at=utcnow(),
                    resource_hold_id=outcome.resource_hold_id,
                    resource_sync_status="SUCCEEDED",
                )
                service.reject_overlapping(session, app)
            else:
                step.status = "REJECTED"
                reason = outcome.reason or "申请资源命令已撤销"
                target = (
                    "AUTO_CANCELLED"
                    if outcome.reason_code == "EXPIRED" or outcome.status == "CANCELLED"
                    else "REJECTED"
                )
                service.transition(
                    session,
                    app,
                    target,
                    target,
                    reason=reason,
                    current_level=None,
                    step_deadline_at=None,
                    finalized_at=utcnow(),
                    final_reason=reason,
                    resource_sync_status="FAILED",
                )
        job.status, job.last_error = "DONE", None
        job.lease_token = job.lease_until = None
        session.flush()
    return True
