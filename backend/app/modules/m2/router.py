from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.core.database import get_session_factory
from app.core.errors import DomainError
from app.core.schema import DecimalId
from app.modules.audit.service import record_operation
from app.modules.auth.dependencies import Db, bearer
from app.modules.auth.permissions import Principal, load_principal
from app.modules.auth.scopes import scope_grants
from app.modules.auth.security import token_user_id
from app.modules.m2 import service
from app.modules.m2.dispatch import dispatch_one
from app.modules.m2.models import Application, ApplicationEvent, ApprovalStep, ResourceJob
from app.modules.m2.schemas import (
    ApplicationView,
    CreateApplication,
    EventView,
    Page,
    Review,
    StepView,
    Withdrawal,
)
from app.modules.resources.models import Room
from app.modules.resources.schemas import RoomView

router = APIRouter(prefix="/api/v1/m2", tags=["M2 两级审批"])


def actor_identity(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None:
        raise DomainError(401, "NOT_AUTHENTICATED", "请先登录")
    # 鉴权事务先结束，审批编排的网络阶段没有请求级 Db 事务悬挂。
    with get_session_factory()() as session, session.begin():
        return load_principal(session, token_user_id(credentials.credentials))


Actor = Annotated[Principal, Depends(actor_identity)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]


def details(session, app):
    job = session.scalar(select(ResourceJob).where(ResourceJob.application_id == app.id))
    return {
        **ApplicationView.model_validate(app).model_dump(mode="json"),
        "steps": [
            StepView.model_validate(step).model_dump(mode="json")
            for step in service.steps_for(session, app)
        ],
        "events": [
            EventView.model_validate(event).model_dump(mode="json")
            for event in session.scalars(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == app.id)
                .order_by(ApplicationEvent.id)
            )
        ],
        "resource_attempts": job.attempts if job else 0,
        "resource_message": job.last_error if job else None,
        "room": RoomView.model_validate(session.get(Room, app.room_id)).model_dump(mode="json"),
    }


@router.get("/me")
def me(session: Db, actor: Actor):
    return {
        "can_apply": True,
        "scopes": [
            {"role": role, "type": kind, "key": key}
            for role, kind, key in scope_grants(session, actor.user_id)
        ],
    }


@router.get("/rooms")
def rooms(
    session: Db,
    actor: Actor,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    result = service.candidate_rooms(session, actor, page, page_size)
    result["items"] = [RoomView.model_validate(room) for room in result["items"]]
    return result


@router.post("/applications", response_model=ApplicationView, status_code=201)
def create(body: CreateApplication, response: Response, actor: Actor, key: Key):
    with get_session_factory()() as session, session.begin():
        app, created = service.create_application(session, body, actor, str(key))
        result = ApplicationView.model_validate(app)
    response.status_code = 201 if created else 200
    return result


@router.get("/applications", response_model=Page)
def applications(
    session: Db,
    actor: Actor,
    mine: bool = False,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return service.list_applications(
        session, actor, mine=mine, status=status, page=page, page_size=page_size
    )


@router.get("/applications/me", response_model=Page)
def my_applications(
    session: Db,
    actor: Actor,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return service.list_applications(session, actor, mine=True, page=page, page_size=page_size)


@router.get("/applications/{app_id}")
def detail(app_id: DecimalId, session: Db, actor: Actor):
    return details(session, service.get_application(session, app_id, actor))


@router.get("/applications/{app_id}/events", response_model=list[EventView])
def history(app_id: DecimalId, session: Db, actor: Actor):
    service.get_application(session, app_id, actor)
    return list(
        session.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == app_id)
            .order_by(ApplicationEvent.id)
        )
    )


@router.post("/applications/{app_id}/withdraw", response_model=ApplicationView)
def withdraw(app_id: DecimalId, body: Withdrawal, actor: Actor, key: Key):
    with get_session_factory()() as session, session.begin():
        return ApplicationView.model_validate(
            service.withdraw(session, app_id, body, actor, str(key))
        )


@router.get("/approval-tasks")
def tasks(
    session: Db,
    actor: Actor,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    result = service.approval_tasks(session, actor, page, page_size)
    result["items"] = [
        {
            **StepView.model_validate(row).model_dump(mode="json"),
            "application": ApplicationView.model_validate(
                session.get(Application, row.application_id)
            ).model_dump(mode="json"),
        }
        for row in result["items"]
    ]
    return result


def decide(step_id, body, actor, key, passed):
    try:
        with get_session_factory()() as session, session.begin():
            app = service.review_step(session, step_id, body, actor, str(key), passed)
            app_id, status = app.id, app.status
    except DomainError as error:
        if error.status in {403, 404}:
            with get_session_factory()() as session, session.begin():
                app_id = session.scalar(
                    select(ApprovalStep.application_id).where(ApprovalStep.id == step_id)
                )
                if app_id:
                    record_operation(
                        session,
                        module="M2",
                        business_id=app_id,
                        action="REVIEW_DENIED",
                        actor_id=actor.user_id,
                        note="审批角色、数据范围或本人限制校验未通过",
                    )
        raise
    if status == "RESOURCE_CONFIRMING":
        dispatch_one(app_id)  # 此处没有数据库事务或锁；技术失败返回明确的确认中状态。
    with get_session_factory()() as session, session.begin():
        return details(session, service.get_application(session, app_id, actor))


@router.post("/approval-tasks/{step_id}/approve")
def approve(step_id: DecimalId, body: Review, actor: Actor, key: Key):
    return decide(step_id, body, actor, key, True)


@router.post("/approval-tasks/{step_id}/reject")
def reject(step_id: DecimalId, body: Review, actor: Actor, key: Key):
    return decide(step_id, body, actor, key, False)
