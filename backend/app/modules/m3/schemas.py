from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, StrictBool, model_validator

from app.core.schema import Id, Input, Output, UtcDateTime

WorkOrderCategory = Literal["AIR_CONDITIONING", "LIGHTING", "POWER_SOCKET", "FURNITURE", "OTHER"]
WorkOrderStatus = Literal["REPORTED", "ASSIGNED", "REPAIRING", "WAIT_ACCEPTANCE", "COMPLETED"]


def validate_id_input(value):
    # Accept JSON integer IDs and the project's string IDs without lossy coercion.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("ID must be an integer or a decimal integer string")
    if isinstance(value, str) and (not value.isascii() or not value.isdecimal()):
        raise ValueError("ID must be a positive decimal integer string")
    return value


M3Id = Annotated[Id, BeforeValidator(validate_id_input)]


class CreateWorkOrder(Input):
    room_id: M3Id
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    category: WorkOrderCategory
    request_key: UUID


class AssignWorkOrder(Input):
    assignee_id: M3Id


class SubmitCompletion(Input):
    completion_note: str = Field(min_length=1, max_length=2000)


class Acceptance(Input):
    passed: StrictBool
    comment: str | None = Field(default=None, max_length=500)
    expected_completion_round: int = Field(strict=True, ge=1, le=4294967295)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> Self:
        if not self.passed and not self.comment:
            raise ValueError("A rejection comment is required when acceptance fails")
        return self


class OperationView(Output):
    id: Id
    module: str
    business_id: Id
    action: str
    actor_id: Id
    from_status: str | None
    to_status: str | None
    note: str | None
    created_at: UtcDateTime


class WorkOrderView(Output):
    id: Id
    created_at: UtcDateTime
    reporter_id: Id
    room_id: Id
    title: str
    description: str
    category: WorkOrderCategory
    status: WorkOrderStatus
    assignee_id: Id | None
    assigned_by: Id | None
    assigned_at: UtcDateTime | None
    started_at: UtcDateTime | None
    completion_note: str | None
    submitted_at: UtcDateTime | None
    completion_round: int
    accepted_by: Id | None
    accepted_at: UtcDateTime | None
    acceptance_comment: str | None
    request_key: UUID
    updated_at: UtcDateTime


class WorkOrderDetail(WorkOrderView):
    history: list[OperationView]


class WorkOrderPage(Output):
    items: list[WorkOrderView]
    total: int
    page: int
    page_size: int
