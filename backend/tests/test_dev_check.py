"""预检查在连接数据库前拒绝无效配置，且不输出配置中的秘密。"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run_check(**overrides):
    env = dict(
        os.environ,
        JWT_SIGNING_KEY="test-signing-key-with-at-least-32-characters",
        MYSQL_PASSWORD="test-only-value",
        SEED_USER_PASSWORD="test-only-seed-password",
    )
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-m", "scripts.dev_check", "--before-migration"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=20,
    )


def test_short_signing_key_is_rejected_without_echoing_its_value():
    secret = "canary-private-jwt-value"
    result = run_check(JWT_SIGNING_KEY=secret)
    assert result.returncode == 1
    assert "JWT_SIGNING_KEY" in result.stdout
    assert secret not in result.stdout + result.stderr


def test_placeholder_database_password_stops_before_any_connection():
    secret = "REPLACE_WITH_CANARY_PRIVATE_DATABASE_VALUE"
    result = run_check(MYSQL_PASSWORD=secret)
    assert result.returncode == 1
    assert "MYSQL_PASSWORD" in result.stdout
    assert secret not in result.stdout + result.stderr


def test_short_seed_password_stops_before_migration():
    secret = "canary-seed"
    result = run_check(SEED_USER_PASSWORD=secret)
    assert result.returncode == 1
    assert "SEED_USER_PASSWORD" in result.stdout
    assert secret not in result.stdout + result.stderr
