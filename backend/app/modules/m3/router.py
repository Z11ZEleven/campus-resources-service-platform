from fastapi import APIRouter, Query

from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.m3 import service
from app.modules.m3.schemas import (
    Acceptance,
    AssignWorkOrder,
    CreateWorkOrder,
    M3Id,
    OperationView,
    SubmitCompletion,
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


@router.get("/{work_order_id}", response_model=WorkOrderDetail)
def detail(work_order_id: M3Id, session: Db, user: CurrentUser):
    order, history = service.work_order_detail(session, order_id=work_order_id, actor=user)
    return WorkOrderDetail(
        **WorkOrderView.model_validate(order).model_dump(),
        history=[OperationView.model_validate(row) for row in history],
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
