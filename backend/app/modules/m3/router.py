from fastapi import APIRouter, Query

from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.m3 import service
from app.modules.m3.schemas import (
    Acceptance,
    AssignWorkOrder,
    CreateWorkOrder,
    EscalationView,
    M3Id,
    OperationView,
    RatingView,
    SubmitCompletion,
    SubmitRating,
    TimeoutPolicy,
    TimeoutScanResult,
    WorkOrderCategory,
    WorkOrderDetail,
    WorkOrderPage,
    WorkOrderStatus,
    WorkOrderView,
)

router = APIRouter(prefix="/api/v1/m3/work-orders", tags=["M3 报修工单"])


@router.post("", response_model=WorkOrderView, status_code=201)
def create(body: CreateWorkOrder, session: Db, user: CurrentUser):
    return service.create_work_order(session, body=body, actor=user)


@router.get("", response_model=WorkOrderPage)
def list_orders(
    session: Db,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: WorkOrderStatus | None = None,
    category: WorkOrderCategory | None = None,
    room_id: M3Id | None = None,
):
    rows, total = service.list_work_orders(
        session, actor=user, page=page, page_size=page_size,
        status=status, category=category, room_id=room_id,
    )
    return WorkOrderPage(
        items=[WorkOrderView.model_validate(row) for row in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/timeout-policy", response_model=TimeoutPolicy)
def policy(user: CurrentUser):
    from app.modules.m3.timeouts import timeout_policy

    return timeout_policy()


@router.post("/scan-timeouts", response_model=TimeoutScanResult)
def scan_timeouts(user: CurrentUser):
    from app.modules.m3.timeouts import run_timeout_scan

    user.require("m3:assign")
    return run_timeout_scan(triggered_by=user.user_id)


@router.get("/{work_order_id}", response_model=WorkOrderDetail)
def detail(work_order_id: M3Id, session: Db, user: CurrentUser):
    order, history = service.work_order_detail(session, order_id=work_order_id, actor=user)
    rating, escalations, timeout = service.work_order_extensions(session, order)
    return WorkOrderDetail(
        **WorkOrderView.model_validate(order).model_dump(),
        history=[OperationView.model_validate(row) for row in history],
        rating=RatingView.model_validate(rating) if rating is not None else None,
        escalations=[EscalationView.model_validate(row) for row in escalations],
        timeout=timeout,
    )


@router.post("/{work_order_id}/assign", response_model=WorkOrderView)
def assign(work_order_id: M3Id, body: AssignWorkOrder, session: Db, user: CurrentUser):
    return service.assign_work_order(session, order_id=work_order_id, body=body, actor=user)


@router.post("/{work_order_id}/start", response_model=WorkOrderView)
def start(work_order_id: M3Id, session: Db, user: CurrentUser):
    return service.start_work_order(session, order_id=work_order_id, actor=user)


@router.post("/{work_order_id}/submit-completion", response_model=WorkOrderView)
def complete(work_order_id: M3Id, body: SubmitCompletion, session: Db, user: CurrentUser):
    return service.submit_completion(session, order_id=work_order_id, body=body, actor=user)


@router.post("/{work_order_id}/acceptance", response_model=WorkOrderView)
def acceptance(work_order_id: M3Id, body: Acceptance, session: Db, user: CurrentUser):
    return service.accept_work_order(session, order_id=work_order_id, body=body, actor=user)


@router.post("/{work_order_id}/rating", response_model=RatingView, status_code=201)
def rating(work_order_id: M3Id, body: SubmitRating, session: Db, user: CurrentUser):
    return service.submit_rating(session, order_id=work_order_id, body=body, actor=user)
