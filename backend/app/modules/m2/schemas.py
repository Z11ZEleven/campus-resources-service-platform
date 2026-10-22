from pydantic import AwareDatetime, Field

from app.core.schema import DecimalId, Id, Input, Output, UtcDateTime


class CreateApplication(Input):
    room_id: DecimalId
    purpose: str = Field(min_length=1, max_length=2000)
    start_at: AwareDatetime
    end_at: AwareDatetime


class Review(Input):
    comment: str = Field(default="", max_length=500)
    expected_version: int = Field(strict=True, ge=1)


class Withdrawal(Input):
    expected_version: int = Field(strict=True, ge=1)


class ApplicationView(Output):
    id: Id
    applicant_id: Id
    applicant_identity_id: Id
    identity_type: str
    department_code: str
    room_id: Id
    purpose: str
    start_at: UtcDateTime
    end_at: UtcDateTime
    status: str
    current_level: int | None
    step_deadline_at: UtcDateTime | None
    resource_sync_status: str
    resource_hold_id: Id | None
    final_reason: str | None
    finalized_at: UtcDateTime | None
    version: int
    created_at: UtcDateTime
    submitted_at: UtcDateTime
    updated_at: UtcDateTime


class StepView(Output):
    id: Id
    application_id: Id
    level: int
    required_role: str
    scope_type: str
    scope_key: str
    status: str
    deadline_at: UtcDateTime
    decided_by: Id | None
    decision_reason: str | None
    decided_at: UtcDateTime | None
    version: int


class EventView(Output):
    id: Id
    event_type: str
    actor_type: str
    actor_id: Id | None
    from_status: str | None
    to_status: str
    reason: str | None
    version: int
    created_at: UtcDateTime


class ApplicationDetail(ApplicationView):
    steps: list[StepView]
    events: list[EventView]


class Page(Output):
    items: list[ApplicationView]
    total: int
    page: int
    page_size: int
