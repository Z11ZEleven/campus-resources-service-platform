"""启动前的安全诊断：不输出配置值、SQL 或数据库异常原文。"""

import argparse
import sys


def check(*, before_migration=False):
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
    print("数据库连接检查通过。" if before_migration else "数据库连接及迁移版本检查通过。")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-migration", action="store_true")
    args = parser.parse_args()
    return check(before_migration=args.before_migration)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
