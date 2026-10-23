"""真实网络故障注入与并发验证，不替换 D 的资源业务逻辑。"""

import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Event, Thread
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from test_m2 import (
    create,
    decide,
    detail,
    first_approved,
    headers,
)
from test_m2 import (
    m2_data as m2_data,
)
from test_m2 import (
    resource_server as resource_server,
)

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.errors import DomainError
from app.core.time import as_utc, utcnow
from app.modules.m2 import service
from app.modules.m2.dispatch import dispatch_one
from app.modules.m2.models import Application, ApprovalStep, ResourceJob
from app.modules.m2.schemas import Review, Withdrawal
from app.modules.m2.worker import run_scan
from app.modules.resources.commands import ResourceCommand
from app.modules.resources.models import ResourceBlock
from app.modules.resources.service import occupy_room, start_maintenance


def test_first_network_command_rechecks_scope_even_if_source_exists(
    client, m2_data, factory, resource_server
):
    app = first_approved(client, m2_data)
    app_id, _ = prepare_confirmation(factory, m2_data, app)
    with factory() as session, session.begin():
        occupy_room(
            session,
            room_id=m2_data["whole"],
            application_id=app_id,
            applicant_id=m2_data["users"]["student"].user_id,
            start_at=m2_data["start"],
            end_at=m2_data["end"],
            actor=m2_data["users"]["room_reviewer"],
        )
        payload = dict(session.scalar(select(ResourceJob.payload)))
    payload["actor_id"] = str(m2_data["users"]["wrong_room"].user_id)
    result = httpx.post(
        resource_server + "/api/v1/resource-commands/m2",
        json=payload,
        headers={
            "Authorization": "Bearer " + get_settings().resource_service_key.get_secret_value(),
        },
        timeout=5,
        trust_env=False,
    )
    assert result.status_code == 200 and result.json()["status"] == "REJECTED"
    assert result.json()["reason_code"] == "FORBIDDEN"


pytestmark = pytest.mark.mysql


class WireFault:
    def __init__(self, upstream, mode):
        self.entered, self.release = Event(), Event()
        fault = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                acquire = not self.path.endswith("/cancel")
                if acquire and mode == "delay_before":
                    fault.entered.set()
                    if not fault.release.wait(8):
                        return
                result = httpx.post(
                    upstream + self.path,
                    content=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": self.headers.get("Authorization", ""),
                    },
                    timeout=5,
                    trust_env=False,
                )
                if acquire and mode == "drop_after":
                    fault.entered.set()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                payload = result.content
                if acquire and mode == "corrupt_after":
                    value = result.json()
                    value["source_id"] = "18446744073709551615"
                    payload = json.dumps(value).encode()
                self.send_response(result.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def set_url(monkeypatch, url):
    monkeypatch.setenv("M2_RESOURCE_BASE_URL", url)
    get_settings.cache_clear()


def ready_again(factory, app_id):
    with factory() as session, session.begin():
        job = session.scalar(select(ResourceJob).where(ResourceJob.application_id == int(app_id)))
        job.next_attempt_at = utcnow()


def prepare_confirmation(factory, data, app, *, expires_in=None):
    with factory() as session, session.begin():
        _, row = service.locked(session, int(app["id"]))
        step = session.scalar(
            select(ApprovalStep).where(
                ApprovalStep.application_id == row.id, ApprovalStep.level == 2
            )
        )
        if expires_in is not None:
            row.step_deadline_at = step.deadline_at = utcnow() + timedelta(seconds=expires_in)
        service.review_step(
            session,
            step.id,
            Review(expected_version=row.version),
            data["users"]["room_reviewer"],
            str(uuid4()),
            True,
        )
        return row.id, row.step_deadline_at


@pytest.mark.parametrize("mode", ["drop_after", "corrupt_after"])
def test_unknown_response_recovers_same_source_without_duplicate(
    client, m2_data, factory, resource_server, monkeypatch, mode
):
    app = first_approved(client, m2_data)
    fault = WireFault(resource_server, mode)
    try:
        set_url(monkeypatch, fault.url)
        result = decide(client, m2_data, app, "room_reviewer")
        assert result.status_code == 200 and result.json()["status"] == "RESOURCE_CONFIRMING"
        assert result.json()["resource_message"]
        with factory() as session:
            assert session.scalar(select(ResourceCommand.status)) == "SUCCEEDED"
            assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 1
        denied = client.post(
            f"/api/v1/m2/applications/{app['id']}/withdraw",
            headers=headers(m2_data, "student"),
            json={"expected_version": result.json()["version"]},
        )
        assert denied.status_code == 409
        set_url(monkeypatch, resource_server)
        ready_again(factory, app["id"])
        assert dispatch_one(int(app["id"]))
        approved = detail(client, m2_data, app)
        assert approved["status"] == "APPROVED"
        assert sum(e["event_type"] == "APPROVED" for e in approved["events"]) == 1
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 1
            assert session.scalar(select(ResourceJob.attempts)) == 2
    finally:
        fault.close()
        set_url(monkeypatch, resource_server)


def test_disconnection_and_worker_restart_do_not_invent_approval(
    client, m2_data, factory, resource_server, monkeypatch
):
    app = first_approved(client, m2_data)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
    set_url(monkeypatch, f"http://127.0.0.1:{unused}")
    response = decide(client, m2_data, app, "room_reviewer")
    assert response.json()["status"] == "RESOURCE_CONFIRMING"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 0
    set_url(monkeypatch, resource_server)
    ready_again(factory, app["id"])
    assert run_scan()["resources_confirmed"] == 1
    assert detail(client, m2_data, app)["status"] == "APPROVED"


def test_late_acquire_cannot_resurrect_timed_out_application(
    client, m2_data, factory, resource_server, monkeypatch
):
    app = first_approved(client, m2_data)
    app_id, due = prepare_confirmation(factory, m2_data, app, expires_in=3)
    fault = WireFault(resource_server, "delay_before")
    try:
        set_url(monkeypatch, fault.url)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(dispatch_one, app_id)
            assert fault.entered.wait(5)
            # 等待真实时钟越过本次测试设定的审批截止点。
            Event().wait(max(0, (due - utcnow()).total_seconds()) + 0.03)
            assert run_scan()["expired"] == 1
            assert detail(client, m2_data, app)["status"] == "AUTO_CANCELLED"
            fault.release.set()
            pending.result(timeout=5)
        with factory() as session:
            assert session.scalar(select(ResourceCommand.status)) == "CANCELLED"
            assert session.scalar(select(func.count()).select_from(ResourceBlock)) == 0
            assert session.get(Application, app_id).resource_sync_status == "CANCELLED"
    finally:
        fault.close()
        set_url(monkeypatch, resource_server)


def test_expiry_after_unknown_success_releases_only_own_hold(
    client, m2_data, factory, resource_server, monkeypatch
):
    app = first_approved(client, m2_data)
    app_id, due = prepare_confirmation(factory, m2_data, app, expires_in=3)
    fault = WireFault(resource_server, "drop_after")
    try:
        set_url(monkeypatch, fault.url)
        assert not dispatch_one(app_id)
        with factory() as session, session.begin():
            assert session.scalar(select(ResourceCommand.status)) == "SUCCEEDED"
            start_maintenance(
                session,
                room_id=m2_data["whole"],
                work_order_id=555,
                actor=m2_data["users"]["repairer"],
            )
        set_url(monkeypatch, resource_server)
        Event().wait(max(0, (due - utcnow()).total_seconds()) + 0.03)
        assert run_scan()["expired"] == 1
        with factory() as session:
            rows = session.execute(
                select(ResourceBlock.source_module, ResourceBlock.status).order_by(
                    ResourceBlock.source_module
                )
            ).all()
            assert rows == [("M2", "RELEASED"), ("M3", "ACTIVE")]
            assert session.get(Application, app_id).status == "AUTO_CANCELLED"
            assert session.get(Application, app_id).resource_sync_status == "CANCELLED"
    finally:
        fault.close()
        set_url(monkeypatch, resource_server)


def test_real_concurrent_network_commands_only_one_approval(
    client, m2_data, factory, resource_server
):
    applications = [
        first_approved(client, m2_data),
        first_approved(client, m2_data, actor="teacher"),
    ]
    ids = [prepare_confirmation(factory, m2_data, app)[0] for app in applications]
    barrier = Barrier(2)

    def attempt(app_id):
        barrier.wait(timeout=5)
        return dispatch_one(app_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(attempt, ids))
    with factory() as session:
        assert sorted(session.scalars(select(Application.status))) == ["APPROVED", "REJECTED"]
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceBlock)
                .where(ResourceBlock.status == "ACTIVE")
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 2


def test_network_phase_holds_no_database_connection(client, m2_data, resource_server, monkeypatch):
    from app.modules.m2 import dispatch

    original = dispatch.send
    observed = []

    def observe(payload, action):
        observed.append(get_engine().pool.checkedout())
        return original(payload, action)

    monkeypatch.setattr(dispatch, "send", observe)
    app = first_approved(client, m2_data)
    assert decide(client, m2_data, app, "room_reviewer").json()["status"] == "APPROVED"
    assert observed == [0]


def test_service_auth_and_payload_idempotency(client, m2_data, factory, resource_server):
    app = first_approved(client, m2_data)
    app_id, _ = prepare_confirmation(factory, m2_data, app)
    with factory() as session:
        payload = dict(session.scalar(select(ResourceJob.payload)))
    url = resource_server + "/api/v1/resource-commands/m2"
    assert httpx.post(url, json=payload, timeout=5, trust_env=False).status_code == 401
    assert (
        httpx.post(
            url, json=payload, headers=headers(m2_data, "student"), timeout=5, trust_env=False
        ).status_code
        == 401
    )
    token = get_settings().resource_service_key.get_secret_value()
    auth = {"Authorization": "Bearer " + token}
    first = httpx.post(url, json=payload, headers=auth, timeout=5, trust_env=False)
    retry = httpx.post(url, json=payload, headers=auth, timeout=5, trust_env=False)
    assert first.status_code == 200 and first.json() == retry.json()
    changed = {**payload, "end_at": as_utc(m2_data["end"] + timedelta(hours=1)).isoformat()}
    assert (
        httpx.post(url, json=changed, headers=auth, timeout=5, trust_env=False).status_code == 409
    )
    result = httpx.get(url + f"/{app_id}", headers=auth, timeout=5, trust_env=False)
    assert result.json()["resource_hold_id"] == first.json()["resource_hold_id"]
    assert (
        httpx.post(
            url + "/cancel", json=payload, headers=auth, timeout=5, trust_env=False
        ).status_code
        == 409
    )
    assert dispatch_one(app_id)


def test_approval_withdraw_timeout_race_has_one_terminal_result(client, m2_data, factory):
    app = create(client, m2_data)
    current = detail(client, m2_data, app)
    app_id, step_id = int(app["id"]), int(current["steps"][0]["id"])
    with factory() as session, session.begin():
        session.get(Application, app_id).step_deadline_at = utcnow() - timedelta(seconds=1)
    barrier = Barrier(3)

    def action(kind):
        try:
            with factory() as session, session.begin():
                barrier.wait(timeout=5)
                if kind == "approve":
                    service.review_step(
                        session,
                        step_id,
                        Review(expected_version=app["version"]),
                        m2_data["users"]["campus"],
                        str(uuid4()),
                        True,
                    )
                elif kind == "withdraw":
                    service.withdraw(
                        session,
                        app_id,
                        Withdrawal(expected_version=app["version"]),
                        m2_data["users"]["student"],
                        str(uuid4()),
                    )
                else:
                    service.scan_local(session, app_id)
        except DomainError:
            pass

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(action, ["approve", "withdraw", "scan"]))
    result = detail(client, m2_data, app)
    assert result["status"] == "AUTO_CANCELLED"
    assert sum(e["event_type"] == "AUTO_CANCELLED" for e in result["events"]) == 1
