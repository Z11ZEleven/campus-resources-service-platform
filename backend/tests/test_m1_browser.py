"""Opt-in real Chromium → Vite → Uvicorn → MySQL acceptance."""

import json
import os
import shutil
import socket
import subprocess
import threading
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.core.time import to_utc


@pytest.mark.skipif(os.environ.get("RUN_BROWSER_E2E") != "1", reason="RUN_BROWSER_E2E=1 required")
def test_student_and_admin_browser_flow(data, factory, monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv(
        "M1_CHECKIN_SECRET", "test-only-browser-checkin-secret-at-least-32-characters"
    )
    get_settings.cache_clear()
    now = to_utc(data["start"]) - timedelta(minutes=10)
    for module in ["reservations", "period", "search", "checkin"]:
        monkeypatch.setattr(f"app.modules.m1.{module}.precise_utcnow", lambda: now)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    api_port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        web_port = probe.getsockname()[1]
    env = {**os.environ, "VITE_API_PROXY_TARGET": f"http://127.0.0.1:{api_port}"}
    vite = subprocess.Popen(
        [shutil.which("npm"), "run", "dev", "--", "--port", str(web_port), "--strictPort"],
        cwd=frontend,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import time

        url = f"http://127.0.0.1:{web_port}"
        for _ in range(100):
            try:
                if httpx.get(url + "/api/v1/resources/rooms", timeout=1).status_code == 401:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("Browser stack failed to start")
        result = subprocess.run(
            [
                os.environ.get("PLAYWRIGHT_PYTHON", "python"),
                str(Path(__file__).with_name("browser_flow.py")),
            ],
            input=json.dumps(
                {
                    "url": url,
                    "day": str(data["day"]),
                    "room": data["room"],
                    "chrome": os.environ.get("BROWSER_CHANNEL") == "chrome",
                }
            ),
            text=True,
            capture_output=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout)
        verify_service_http(factory, data, f"http://127.0.0.1:{api_port}")
    finally:
        vite.terminate()
        vite.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        get_settings.cache_clear()


def verify_service_http(factory, data, url):
    """Real TCP contract probe; not a substitute for an actual M2/M3 caller."""
    from app.modules.auth.models import ServicePrincipal, ServiceRoomScope
    from app.modules.auth.sessions import credential_hash

    token = "test-only-real-http-m3-token"
    with factory() as session, session.begin():
        service = ServicePrincipal(
            name="real-http-probe", module="M3", token_hash=credential_hash(token)
        )
        session.add(service)
        session.flush()
        session.add(ServiceRoomScope(service_id=service.id, room_id=data["room"]))
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}) as client:
        path = "/api/v1/internal/resource-holds/M3/8001"
        body = {
            "room_id": str(data["room"]),
            "active": True,
            "revision": 1,
            "reason": "网络契约验收",
        }
        created = client.put(path, json=body, headers={"Idempotency-Key": "tcp-create"})
        assert created.status_code == 200, created.text
        # Discard/recover the first response through the operation ledger.
        recovered = client.get("/api/v1/internal/resource-operations/tcp-create")
        assert recovered.json() == created.json()
        assert (
            client.put(path, json=body, headers={"Idempotency-Key": "tcp-create"}).json()
            == created.json()
        )
        released = client.put(
            path,
            json={**body, "active": False, "revision": 2},
            headers={"Idempotency-Key": "tcp-release"},
        )
        assert released.status_code == 200 and released.json()["status"] == "RELEASED"
        assert (
            client.put(path, json=body, headers={"Idempotency-Key": "tcp-stale"}).status_code == 409
        )
        assert client.get(path).json()["status"] == "RELEASED"
    print("真实TCP来源接口：操作核对、重放、释放和旧请求拒绝通过；调用方为测试探针")
