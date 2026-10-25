"""为本机开发补齐独立服务密钥，不回显秘密或覆盖已有有效配置。"""

import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key


def ensure_resource_key(root: Path) -> bool:
    paths = (root / ".env", root / "backend" / ".env")
    if not paths[0].is_file():
        raise ValueError("请先在仓库根目录生成 .env，再运行 start.cmd --setup。")
    files = [(path, dotenv_values(path)) for path in paths]
    values = {**files[0][1], **files[1][1], **os.environ}
    key = values.get("RESOURCE_SERVICE_KEY")
    missing = not key or not key.strip() or key.startswith("REPLACE_")
    if "RESOURCE_SERVICE_KEY" in os.environ and missing:
        raise ValueError("请修正 RESOURCE_SERVICE_KEY 环境变量；启动器不会覆盖进程环境中的配置。")
    if not missing:
        if len(key) < 32:
            raise ValueError("RESOURCE_SERVICE_KEY 至少需要 32 字符，请检查本机配置。")
        if key == values.get("JWT_SIGNING_KEY"):
            raise ValueError("RESOURCE_SERVICE_KEY 必须独立于 JWT_SIGNING_KEY，请分别生成密钥。")
        return False
    target = next(
        (path for path, data in reversed(files) if "RESOURCE_SERVICE_KEY" in data), paths[0]
    )
    key = secrets.token_urlsafe(48)
    set_key(str(target), "RESOURCE_SERVICE_KEY", key, encoding="utf-8")
    return True


def main():
    from app.core.config import ROOT

    try:
        changed = ensure_resource_key(ROOT.parent)
    except (ValueError, OSError) as exc:
        # OSError 的文件名可用于定位；不打印底层配置值。
        if isinstance(exc, ValueError):
            print(str(exc))
        else:
            print("无法保存本机 RESOURCE_SERVICE_KEY，请检查 .env 文件权限。")
        return 1
    print("已补齐独立资源服务密钥。" if changed else "独立资源服务密钥已配置，保留原值。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
