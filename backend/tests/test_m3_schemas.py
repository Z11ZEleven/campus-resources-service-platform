from datetime import datetime
from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError


def schema(name):
    try:
        module = import_module("app.modules.m3.schemas")
    except ModuleNotFoundError:
        pytest.fail("M3 request and response schemas have not been implemented")
    return getattr(module, name)


def valid_create(**changes):
    return {
        "room_id": "9007199254740993",
        "title": " 空调无法启动 ",
        "description": " 检查供电和控制面板。 ",
        "category": "AIR_CONDITIONING",
        "request_key": str(uuid4()),
        **changes,
    }


def test_create_accepts_string_ids_and_normalizes_text_and_uuid():
    payload = valid_create()
    created = schema("CreateWorkOrder")(**payload)
    assert created.room_id == 9007199254740993
    assert created.title == "空调无法启动"
    assert created.description == "检查供电和控制面板。"
    assert str(created.request_key) == payload["request_key"]
    assert created.model_dump(mode="json")["room_id"] == payload["room_id"]


@pytest.mark.parametrize("value", [True, False, 1.0, 1.5, 0, -1, "-1", "1.0", "1e2", "", "18446744073709551616"])
@pytest.mark.parametrize("model,field", [("CreateWorkOrder", "room_id"), ("AssignWorkOrder", "assignee_id")])
def test_input_id_rejects_lossy_or_invalid_values(model, field, value):
    payload = valid_create(room_id=value) if model == "CreateWorkOrder" else {field: value}
    with pytest.raises(ValidationError):
        schema(model)(**payload)


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"title": "故" * 101},
    {"description": "\t"}, {"description": "故" * 2001},
    {"category": "NETWORK"}, {"request_key": "not-a-uuid"},
    {"reporter_id": "9"},
])
def test_create_rejects_invalid_content_or_client_reporter(changes):
    with pytest.raises(ValidationError):
        schema("CreateWorkOrder")(**valid_create(**changes))


@pytest.mark.parametrize("note", ["", "  ", "修" * 2001])
def test_completion_requires_nonblank_bounded_note(note):
    with pytest.raises(ValidationError):
        schema("SubmitCompletion")(completion_note=note)


def test_completion_strips_note_and_refuses_client_status():
    completion = schema("SubmitCompletion")(completion_note=" 已修复 ")
    assert completion.completion_note == "已修复"
    with pytest.raises(ValidationError):
        schema("SubmitCompletion")(completion_note="已修复", status="COMPLETED")


@pytest.mark.parametrize("value", [1, 0, "true", "false", None])
def test_acceptance_requires_json_boolean(value):
    with pytest.raises(ValidationError):
        schema("Acceptance")(passed=value, expected_completion_round=1)


@pytest.mark.parametrize("comment", [None, "", "  "])
def test_rejected_acceptance_requires_reason(comment):
    with pytest.raises(ValidationError):
        schema("Acceptance")(passed=False, comment=comment, expected_completion_round=1)


@pytest.mark.parametrize("round_value", [True, 1.0, 0, -1, 4294967296])
def test_acceptance_requires_positive_unsigned_integer_round(round_value):
    with pytest.raises(ValidationError):
        schema("Acceptance")(passed=True, expected_completion_round=round_value)


def test_acceptance_validates_comment_and_preserves_passed_flag():
    acceptance = schema("Acceptance")(
        passed=False, comment=" 仍有异响 ", expected_completion_round=2
    )
    assert acceptance.passed is False
    assert acceptance.comment == "仍有异响"
    assert acceptance.expected_completion_round == 2
    assert schema("Acceptance")(passed=True, expected_completion_round=2).comment is None
    with pytest.raises(ValidationError):
        schema("Acceptance")(passed=True, comment="验" * 501, expected_completion_round=1)


def work_order_data():
    now = datetime(2026, 10, 21, 8, 30, 0, 123000)
    return {
        "id": 9007199254740993, "created_at": now,
        "reporter_id": 1, "room_id": 2,
        "title": "空调故障", "description": "无法启动", "category": "AIR_CONDITIONING",
        "status": "WAIT_ACCEPTANCE", "assignee_id": 3, "assigned_by": 4,
        "assigned_at": now, "started_at": now, "completion_note": "已更换控制器",
        "submitted_at": now, "completion_round": 1, "accepted_by": None,
        "accepted_at": None, "acceptance_comment": None,
        "request_key": str(uuid4()), "updated_at": now,
        "stage_started_at": now, "escalation_count": 1,
    }


def test_detail_serializes_all_ids_and_history_times_without_precision_loss():
    values = work_order_data()
    values["history"] = [{
        "id": 9007199254740995, "module": "M3", "business_id": values["id"],
        "action": "SUBMIT_COMPLETION", "actor_id": 3,
        "from_status": "REPAIRING", "to_status": "WAIT_ACCEPTANCE",
        "note": "已更换控制器", "created_at": values["created_at"],
    }]
    values["rating"] = {
        "id": 9007199254740996, "work_order_id": values["id"], "reporter_id": 1,
        "score": 5, "comment": "处理及时", "created_at": values["created_at"],
    }
    values["escalations"] = [{
        "id": 9007199254740997, "work_order_id": values["id"], "stage": "REPAIRING",
        "completion_round": 0, "stage_started_at": values["created_at"],
        "due_at": values["created_at"], "escalated_at": values["created_at"],
        "reason": "维修阶段超时", "triggered_by": 9007199254740998,
    }]
    values["timeout"] = {
        "due_at": values["created_at"], "is_overdue": True, "current_stage_escalated": False,
    }
    body = schema("WorkOrderDetail")(**values).model_dump(mode="json")
    for field in ("id", "reporter_id", "room_id", "assignee_id", "assigned_by"):
        assert body[field] == str(values[field])
    assert body["accepted_by"] is None
    assert body["completion_round"] == 1
    assert body["created_at"] == "2026-10-21T08:30:00.123000Z"
    assert body["history"][0]["business_id"] == str(values["id"])
    assert body["history"][0]["id"] == "9007199254740995"
    assert body["history"][0]["actor_id"] == "3"
    assert body["history"][0]["created_at"].endswith("Z")
    assert body["stage_started_at"] == body["created_at"]
    assert body["escalation_count"] == 1
    assert body["rating"]["id"] == "9007199254740996"
    assert body["rating"]["work_order_id"] == str(values["id"])
    assert body["rating"]["reporter_id"] == "1"
    assert body["rating"]["created_at"].endswith("Z")
    escalation = body["escalations"][0]
    assert escalation["id"] == "9007199254740997"
    assert escalation["work_order_id"] == str(values["id"])
    assert escalation["triggered_by"] == "9007199254740998"
    for field in ("stage_started_at", "due_at", "escalated_at"):
        assert escalation[field].endswith("Z")
    assert body["timeout"]["due_at"].endswith("Z")


def test_work_order_page_retains_pagination_and_uses_view_serialization():
    body = schema("WorkOrderPage")(
        items=[schema("WorkOrderView")(**work_order_data())], total=1, page=1, page_size=20
    ).model_dump(mode="json")
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["items"][0]["id"] == "9007199254740993"
