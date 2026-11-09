"""真实 dotenv 文件验证：补齐旧配置，并保留有效秘密与优先级。"""

import pytest
from dotenv import dotenv_values


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    monkeypatch.delenv("RESOURCE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    (tmp_path / "backend").mkdir()
    (tmp_path / ".env").write_text(
        "MYSQL_PASSWORD='keep-local-password'\nJWT_SIGNING_KEY='keep-local-signing-key-at-least-32-characters'\n",
        encoding="utf-8",
    )
    return tmp_path


def test_legacy_config_gets_an_independent_key_once(local_config):
    from scripts.dev_config import ensure_resource_key

    assert ensure_resource_key(local_config)
    values = dotenv_values(local_config / ".env")
    assert len(values["RESOURCE_SERVICE_KEY"]) >= 32
    assert values["RESOURCE_SERVICE_KEY"] != values["JWT_SIGNING_KEY"]
    assert values["MYSQL_PASSWORD"] == "keep-local-password"
    original = (local_config / ".env").read_bytes()
    assert not ensure_resource_key(local_config)
    assert (local_config / ".env").read_bytes() == original


def test_backend_override_placeholder_is_filled_without_changing_root(local_config):
    from scripts.dev_config import ensure_resource_key

    path = local_config / "backend" / ".env"
    path.write_text(
        "RESOURCE_SERVICE_KEY=REPLACE_WITH_LOCAL_KEY\nMYSQL_PASSWORD=keep-backend-password\n",
        encoding="utf-8",
    )
    original = (local_config / ".env").read_bytes()
    assert ensure_resource_key(local_config)
    assert not dotenv_values(path)["RESOURCE_SERVICE_KEY"].startswith("REPLACE_")
    assert dotenv_values(path)["MYSQL_PASSWORD"] == "keep-backend-password"
    assert (local_config / ".env").read_bytes() == original


def test_valid_environment_key_wins_without_writing_files(local_config, monkeypatch):
    from scripts.dev_config import ensure_resource_key

    monkeypatch.setenv(
        "RESOURCE_SERVICE_KEY", "environment-service-key-with-at-least-32-characters"
    )
    original = (local_config / ".env").read_bytes()
    assert not ensure_resource_key(local_config)
    assert (local_config / ".env").read_bytes() == original


@pytest.mark.parametrize("value", ["short", "REPLACE_WITH_ENVIRONMENT_SECRET", " "])
def test_invalid_environment_key_is_rejected_without_echo(local_config, monkeypatch, value):
    from scripts.dev_config import ensure_resource_key

    monkeypatch.setenv("RESOURCE_SERVICE_KEY", value)
    with pytest.raises(ValueError, match="RESOURCE_SERVICE_KEY") as exc:
        ensure_resource_key(local_config)
    if value.strip():
        assert value not in str(exc.value)


def test_existing_key_equal_to_jwt_is_rejected_and_preserved(local_config):
    from scripts.dev_config import ensure_resource_key

    with (local_config / ".env").open("a", encoding="utf-8") as stream:
        stream.write("RESOURCE_SERVICE_KEY='keep-local-signing-key-at-least-32-characters'\n")
    original = (local_config / ".env").read_bytes()
    with pytest.raises(ValueError, match="JWT_SIGNING_KEY"):
        ensure_resource_key(local_config)
    assert (local_config / ".env").read_bytes() == original
