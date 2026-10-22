"""通过 M3/D 公开 HTTP 接口验证 M2 接入；不修改被测模块。"""

from uuid import uuid4

import pytest
from alembic.config import Config
from conftest import token_headers
from sqlalchemy import inspect, text
from test_m2 import create, review

from alembic import command
from app.core.time import to_utc

pytestmark = pytest.mark.mysql


def m3_action(client, data, order, action, actor, body=None):
    response = client.post(
        f"/api/v1/m3/work-orders/{order['id']}/{action}",
        headers=token_headers(data["users"][actor]),
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def repairing_order(client, data):
    response = client.post(
        "/api/v1/m3/work-orders",
        headers=token_headers(data["users"]["student"]),
        json={
            "room_id": str(data["room"]),
            "title": "M2 联调教室故障",
            "description": "验证维修与整室申请共存",
            "category": "OTHER",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    order = response.json()
    m3_action(
        client,
        data,
        order,
        "assign",
        "property",
        {
            "assignee_id": str(data["users"]["repairer"].user_id),
        },
    )
    return m3_action(client, data, order, "start", "repairer")


def complete_and_accept(client, data, order):
    order = m3_action(
        client,
        data,
        order,
        "submit-completion",
        "repairer",
        {
            "completion_note": "维修完成，等待验收",
        },
    )
    return m3_action(
        client,
        data,
        order,
        "acceptance",
        "property",
        {
            "passed": True,
            "comment": "验收通过",
            "expected_completion_round": order["completion_round"],
        },
    )


def test_m3_http_repair_and_acceptance_gate_m2_approval(client, data):
    application = create(client, data)
    order = repairing_order(client, data)
    assert review(client, data, application["id"]).status_code == 409
    order = m3_action(
        client,
        data,
        order,
        "submit-completion",
        "repairer",
        {
            "completion_note": "第一次提交完工",
        },
    )
    assert order["status"] == "WAIT_ACCEPTANCE"
    assert review(client, data, application["id"]).status_code == 409
    order = m3_action(
        client,
        data,
        order,
        "acceptance",
        "property",
        {
            "passed": False,
            "comment": "需要返修",
            "expected_completion_round": order["completion_round"],
        },
    )
    assert order["status"] == "REPAIRING"
    assert review(client, data, application["id"]).status_code == 409
    order = complete_and_accept(client, data, order)
    assert order["status"] == "COMPLETED" and order["completion_round"] == 2
    approved = review(client, data, application["id"])
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"

    # M3 评价、详情和 D 通知保持原行为，不依赖 M2 的实现。
    headers = token_headers(data["users"]["student"])
    rated = client.post(
        f"/api/v1/m3/work-orders/{order['id']}/rating",
        headers=headers,
        json={"score": 5, "comment": "联调验收完成"},
    )
    assert rated.status_code == 201, rated.text
    detail = client.get(f"/api/v1/m3/work-orders/{order['id']}", headers=headers)
    assert detail.status_code == 200 and detail.json()["rating"]["score"] == 5
    inbox = client.get("/api/v1/notifications", headers=headers)
    assert inbox.status_code == 200 and inbox.json()["total"] > 0
    assert all(item["source_module"] == "M3" for item in inbox.json()["items"])
    assert all(item["link"] == f"/m3/work-orders/{order['id']}" for item in inbox.json()["items"])
    notice = inbox.json()["items"][0]
    assert (
        client.post(f"/api/v1/notifications/{notice['id']}/read", headers=headers).status_code
        == 200
    )


def test_m3_acceptance_preserves_existing_m2_room_hold(client, data, mysql_engine):
    application = create(client, data)
    assert review(client, data, application["id"]).status_code == 200
    order = repairing_order(client, data)
    complete_and_accept(client, data, order)
    result = client.get(
        f"/api/v1/resources/rooms/{data['room']}/availability",
        params={"date": data["day"]},
        headers=token_headers(data["users"]["student"]),
    )
    assert result.status_code == 200
    start = to_utc(data["start"]).isoformat() + "Z"
    slots = [slot for slot in result.json()["slots"] if slot["slot_start"] == start]
    assert len(slots) == 3
    assert all(
        (slot["status"], slot["source_module"], slot["source_count"]) == ("ROOM_BOOKED", "M2", 1)
        for slot in slots
    )
    with mysql_engine.connect() as connection:
        sources = connection.execute(
            text("SELECT source_module, status FROM d_resource_blocks ORDER BY source_module")
        ).all()
    assert sources == [("M2", "ACTIVE"), ("M3", "RELEASED")]


def test_m2_upgrade_and_rollback_preserve_all_existing_m3_and_d_data(client, data, mysql_engine):
    order = repairing_order(client, data)
    # Capture actual existing D/M3 rows, including active maintenance, notifications and audit.
    tables = [
        name for name in inspect(mysql_engine).get_table_names() if name.startswith(("d_", "m3_"))
    ]

    def snapshot():
        with mysql_engine.connect() as connection:
            return {
                name: connection.execute(text(f"SELECT * FROM `{name}` ORDER BY id")).all()
                for name in tables
            }

    before = snapshot()
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "0003_m3_extensions")
    assert "m2_applications" not in inspect(mysql_engine).get_table_names()
    assert snapshot() == before
    command.upgrade(cfg, "head")
    command.check(cfg)
    assert snapshot() == before
    response = client.get(
        f"/api/v1/m3/work-orders/{order['id']}", headers=token_headers(data["users"]["repairer"])
    )
    assert response.status_code == 200 and response.json()["status"] == "REPAIRING"
    application = create(client, data)
    assert review(client, data, application["id"]).status_code == 409
