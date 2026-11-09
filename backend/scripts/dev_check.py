"""启动前的安全诊断：不输出配置值、SQL 或数据库异常原文。"""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def check_resource(url, key):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid local resource URL")
        request = Request(
            url.rstrip("/") + "/api/v1/resource-commands/m2/9223372036854775807",
            headers={"Authorization": "Bearer " + key.get_secret_value()},
        )
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as response:
                if json.load(response).get("source_module") != "M2":
                    raise ValueError("unexpected resource response")
        except HTTPError as exc:
            if exc.code != 404 or json.load(exc).get("detail", {}).get("code") != "NOT_FOUND":
                raise ValueError("resource authentication or database check failed") from None
    except (OSError, URLError, ValueError, AttributeError):
        print("资源服务鉴权/数据库检查失败，请检查 RESOURCE_SERVICE_KEY、资源端口及数据库配置。")
        return 1
    print("资源服务已就绪，服务密钥鉴权与数据库查询通过。")
    return 0


def check(*, before_migration=False, resource_url=None):
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from dotenv import dotenv_values
        from pydantic import ValidationError
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        from app.core.config import ROOT, get_settings
        from app.core.database import get_engine
        from app.core.time import BUSINESS_TZ  # noqa: F401 - 检查 Windows 时区数据
    except (ImportError, KeyError):
        print("后端依赖或 Asia/Shanghai 时区数据缺失，请重新运行 start.cmd --setup。")
        return 1

    try:
        settings = get_settings()
    except ValidationError as exc:
        fields = sorted({str(item["loc"][0]).upper() for item in exc.errors() if item["loc"]})
        print("请检查 .env 中的配置项（未显示配置值）：" + ", ".join(fields))
        return 1
    if settings.mysql_password.get_secret_value().startswith("REPLACE_"):
        print("请在 .env 中填写 MYSQL_PASSWORD，并确认数据库及用户已创建。")
        return 1
    if settings.resource_service_key is None:
        print("缺少 RESOURCE_SERVICE_KEY，请先运行 start.cmd --setup 自动补齐独立服务密钥。")
        return 1
    if (
        settings.resource_service_key.get_secret_value()
        == settings.jwt_signing_key.get_secret_value()
    ):
        print("RESOURCE_SERVICE_KEY 必须独立于 JWT_SIGNING_KEY，请分别生成密钥。")
        return 1
    if before_migration:
        import os

        values = {
            **dotenv_values(ROOT.parent / ".env"),
            **dotenv_values(ROOT / ".env"),
            **os.environ,
        }
        password = values.get("SEED_USER_PASSWORD") or ""
        if len(password) < 12 or password.startswith("REPLACE_"):
            print("请在 .env 中填写至少 12 位的 SEED_USER_PASSWORD；已有账号密码不会被重置。")
            return 1

    heads = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    if len(heads) != 1:
        print("仓库存在多个或缺失 Alembic head，需由维护者处理迁移分支后再初始化。")
        return 1

    engine = get_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if not before_migration:
                current = set(MigrationContext.configure(connection).get_current_heads())
                if current != heads:
                    print("数据库迁移版本与仓库不一致，请确认目标库后运行 start.cmd --setup。")
                    return 1
    except SQLAlchemyError as exc:
        code = getattr(getattr(exc, "orig", None), "args", (None,))[0]
        messages = {
            1045: "MySQL 认证失败，请检查 MYSQL_USER / MYSQL_PASSWORD。",
            1049: "目标数据库不存在，请先创建 MYSQL_DATABASE 指定的数据库。",
            1044: "MySQL 用户无权访问目标数据库，请检查授权。",
            2003: "无法连接 MySQL，请检查服务、MYSQL_HOST 和 MYSQL_PORT。",
        }
        print(messages.get(code, "数据库检查失败，请检查 MySQL 连接、权限和迁移状态。"))
        return 1
    finally:
        engine.dispose()
    if resource_url is not None:
        return check_resource(resource_url, settings.resource_service_key)
    print("数据库连接检查通过。" if before_migration else "数据库连接及迁移版本检查通过。")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-migration", action="store_true")
    parser.add_argument("--resource-url", help="对本机 D 资源服务执行只读鉴权检查")
    args = parser.parse_args()
    return check(before_migration=args.before_migration, resource_url=args.resource_url)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
