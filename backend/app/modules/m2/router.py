from fastapi import APIRouter, Query, Response

from app.core.schema import Id
from app.modules.audit.service import list_operations
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.m2 import service
from app.modules.m2.schemas import (
    ApplicationDetail,
    ApplicationPage,
    ApplicationView,
    CreateApplication,
    EventView,
    ReviewApplication,
    Status,
)

router = APIRouter(prefix="/api/v1/m2/applications", tags=["M2 教室申请"])


@router.post("", response_model=ApplicationView, status_code=201)
def create(body: CreateApplication, response: Response, session: Db, actor: CurrentUser):
    row, created = service.create_application(session, body, actor)
    response.status_code = 201 if created else 200
    return row


@router.get("", response_model=ApplicationPage)
def applications(
    session: Db,
    actor: CurrentUser,
    status: Status | None = None,
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return service.list_applications(
        session, actor, status=status, mine=mine, page=page, page_size=page_size
    )


@router.get("/{application_id}", response_model=ApplicationDetail)
def detail(application_id: Id, session: Db, actor: CurrentUser):
    row = service.get_application(session, application_id, actor)
    return ApplicationDetail(
        **ApplicationView.model_validate(row).model_dump(),
        events=[
            EventView.model_validate(event)
            for event in list_operations(session, module="M2", business_id=row.id)
        ],
    )


@router.post("/{application_id}/review", response_model=ApplicationView)
def review(application_id: Id, body: ReviewApplication, session: Db, actor: CurrentUser):
    return service.review_application(session, application_id, body, actor)


@router.post("/{application_id}/cancel", response_model=ApplicationView)
def cancel(application_id: Id, session: Db, actor: CurrentUser):
    return service.cancel_application(session, application_id, actor)
