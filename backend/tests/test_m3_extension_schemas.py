from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.modules.m3.schemas import EscalationView, RatingView, SubmitRating, TimeoutInfo


@pytest.mark.parametrize("score", [True, False, "4", 4.0, 0, 6, None])
def test_rating_requires_strict_integer_between_one_and_five(score):
    with pytest.raises(ValidationError):
        SubmitRating(score=score)


def test_rating_trims_comments_and_normalizes_blank_retries():
    assert SubmitRating(score=1, comment="  ").comment is None
    assert SubmitRating(score=5, comment="  处理及时  ").comment == "处理及时"
    with pytest.raises(ValidationError):
        SubmitRating(score=5, comment="字" * 501)
    with pytest.raises(ValidationError):
        SubmitRating(score=5, reporter_id="8")


def test_extension_output_keeps_ids_strings_and_utc_datetimes():
    now = datetime(2026, 10, 21, 8, tzinfo=UTC)
    rating = RatingView(
        id=1, work_order_id=2, reporter_id=3, score=5, comment=None, created_at=now,
    ).model_dump(mode="json")
    assert rating["id"] == "1" and rating["work_order_id"] == "2"
    assert rating["reporter_id"] == "3" and rating["created_at"].endswith("Z")
    escalation = EscalationView(
        id=1, work_order_id=2, stage="REPAIRING", completion_round=0,
        stage_started_at=now, due_at=now, escalated_at=now,
        reason="维修中超时", triggered_by=None,
    ).model_dump(mode="json")
    assert escalation["triggered_by"] is None and escalation["id"] == "1"
    assert TimeoutInfo(
        due_at=None, is_overdue=False, current_stage_escalated=False,
    ).model_dump(mode="json")["due_at"] is None
