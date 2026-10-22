from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_has_server_generated_request_id():
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"X-Request-ID": "untrusted-client-input"})
    assert response.status_code == 200
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]
    assert response.headers["X-Request-ID"] != "untrusted-client-input"


def test_validation_errors_are_structured_and_redact_input():
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/auth/login", json={"password": "private-test-value"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_ERROR"
    assert detail["request_id"] == response.headers["X-Request-ID"]
    assert detail["fields"][0]["location"] == ["body", "username"]
    assert "private-test-value" not in response.text


def test_unexpected_errors_are_redacted_and_correlated(caplog):
    app = create_app()

    @app.get("/test-only-error")
    def fail():
        raise RuntimeError("private-test-credential")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-only-error")
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert response.json()["detail"]["request_id"] == response.headers["X-Request-ID"]
    assert "private-test-credential" not in response.text + caplog.text
    assert response.headers["X-Request-ID"] in caplog.text


def test_domain_error_has_same_request_id():
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "NOT_AUTHENTICATED"
    assert response.json()["detail"]["request_id"] == response.headers["X-Request-ID"]


def test_readiness_uses_real_database(mysql_engine):
    with TestClient(create_app()) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_unknown_path_and_wrong_method_have_structured_errors():
    with TestClient(create_app()) as client:
        missing = client.get("/api/v1/not-a-route")
        wrong_method = client.post("/health")
    for response, code in [(missing, "NOT_FOUND"), (wrong_method, "METHOD_NOT_ALLOWED")]:
        assert response.json()["detail"]["code"] == code
        assert response.json()["detail"]["request_id"] == response.headers["X-Request-ID"]
    assert missing.status_code == 404 and wrong_method.status_code == 405
    assert "GET" in wrong_method.headers["Allow"]


def test_readiness_reports_real_connection_failure(monkeypatch, mysql_engine):
    from app.core.config import get_settings
    from app.core.database import get_engine

    get_engine().dispose()
    get_engine.cache_clear()
    try:
        with monkeypatch.context() as env:
            env.setenv("MYSQL_PORT", "1")  # Real refused connection, not a mocked query.
            get_settings.cache_clear()
            with TestClient(create_app()) as client:
                assert client.get("/health").status_code == 200
                response = client.get("/ready")
            assert response.status_code == 503
            assert response.json()["status"] == "database_unavailable"
            assert response.headers["X-Request-ID"]
    finally:
        get_engine().dispose()
        get_engine.cache_clear()
        get_settings.cache_clear()
