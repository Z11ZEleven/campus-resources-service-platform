from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.core.schema import Id, Input, Output, UtcDateTime
from app.core.time import BUSINESS_TZ

Status = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]


class CreateApplication(Input):
    room_id: Id
    title: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=4000)
    participant_count: int = Field(gt=0, le=4294967295, strict=True)
    start_at: AwareDatetime
    end_at: AwareDatetime
    request_key: UUID

    @field_validator("start_at", "end_at")
    @classmethod
    def whole_hour(cls, value):
        local = value.astimezone(BUSINESS_TZ)
        if local.minute or local.second or local.microsecond:
            raise ValueError("开始与结束时间必须为北京时间整点")
        return value


class ReviewApplication(Input):
    passed: bool = Field(strict=True)
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def rejection_needs_reason(self):
        if not self.passed and not self.comment:
            raise ValueError("驳回时必须填写意见")
        return self


class ApplicationView(Output):
    id: Id
    applicant_id: Id
    room_id: Id
    title: str
    purpose: str
    participant_count: int
    start_at: UtcDateTime
    end_at: UtcDateTime
    status: Status
    display_status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED", "ENDED"]
    reviewer_id: Id | None
    reviewed_at: UtcDateTime | None
    review_comment: str | None
    cancelled_at: UtcDateTime | None
    request_key: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


class EventView(Output):
    id: Id
    action: str
    actor_id: Id
    from_status: str | None
    to_status: str | None
    note: str | None
    created_at: UtcDateTime


class ApplicationDetail(ApplicationView):
    events: list[EventView]


class ApplicationPage(Output):
    items: list[ApplicationView]
    total: int
    page: int
    page_size: int
