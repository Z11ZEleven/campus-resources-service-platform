"""本地开发启动器；父进程仅使用 Python 3.12 标准库。"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


class LaunchError(Exception):
    pass


def run_step(command, cwd, label, *, env=None, quiet=False, timeout=None):
    print(f"[启动] {label}", flush=True)
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.STDOUT if quiet else None,
        timeout=timeout,
    )
    if result.returncode:
        # 配置/数据库错误的原始 traceback 可能包含密码，不转发到控制台。
        raise LaunchError(
            f"{label}失败（退出码 {result.returncode}）。请参考 docs/local-start.md。"
        )


def ensure_env(root):
    target = root / ".env"
    if target.exists():
        return
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace(
        "REPLACE_WITH_RANDOM_KEY_AT_LEAST_32_CHARACTERS", secrets.token_urlsafe(48)
    )
    content = content.replace(
        "REPLACE_WITH_INDEPENDENT_RANDOM_KEY_AT_LEAST_32_CHARACTERS",
        secrets.token_urlsafe(48),
    )
    # x 模式防止覆盖另一个启动进程刚写入的配置。
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    raise LaunchError(
        f"已生成 {target} 及独立的 JWT / 资源服务密钥。请填写本机 MYSQL_* 和至少 12 位的 "
        "SEED_USER_PASSWORD，创建数据库并授权后，再运行 start.cmd --setup。已有配置不会覆盖。"
    )


def node_tools():
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise LaunchError(
            "未找到 Node.js/npm。请安装 Node.js 22.12 或以上版本，并重新打开终端。"
        )
    result = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=False
    )
    try:
        version = tuple(
            int(part) for part in result.stdout.strip().lstrip("v").split(".")
        )
    except ValueError as exc:
        raise LaunchError("无法识别 Node.js 版本，请检查 PATH。") from exc
    if result.returncode or version < (22, 12, 0):
        raise LaunchError("Node.js 版本不足，需要 22.12 或以上。")
    return npm


def venv_python(root):
    return (
        root
        / "backend"
        / ".venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )


def check_venv(python):
    if not python.is_file():
        raise LaunchError("尚未安装后端环境，请先运行 start.cmd --setup。")
    run_step(
        [python, "-c", "import sys; sys.exit(sys.version_info[:2] != (3, 12))"],
        python.parent,
        "检查虚拟环境 Python 3.12",
        quiet=True,
    )


def check_ports(backend_port, frontend_port, resource_port=8002):
    ports = (backend_port, frontend_port, resource_port)
    if len(set(ports)) != len(ports):
        raise LaunchError("主后端、资源服务和前端必须使用不同端口。")
    for port in ports:
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise LaunchError(
                    f"端口 {port} 已被占用或不可用，请关闭原服务或指定其他端口。"
                ) from exc


def backend_helper(python, backend, module, arguments=(), *, env=None):
    command = [python, "-m", module, *arguments]
    result = subprocess.run(
        command, cwd=backend, env=env, capture_output=True, timeout=40, check=False
    )
    # 仅调用仓库内的诊断脚本，其 stdout 不包含配置值；不转发第三方 stderr。
    message = result.stdout.decode("utf-8", errors="replace").strip()
    if message:
        print(message, flush=True)
    if result.returncode:
        raise LaunchError("后端配置/预检查失败，请修正上述问题后重试。")


def backend_check(
    python, backend, *, before_migration=False, env=None, resource_url=None
):
    arguments = []
    if before_migration:
        arguments.append("--before-migration")
    if resource_url is not None:
        arguments.extend(["--resource-url", resource_url])
    backend_helper(python, backend, "scripts.dev_check", arguments, env=env)


def prepare(root, npm, env):
    backend = root / "backend"
    python = venv_python(root)
    if not python.exists():
        run_step(
            [sys.executable, "-m", "venv", backend / ".venv"],
            root,
            "创建 Python 虚拟环境",
        )
    check_venv(python)
    run_step(
        [python, "-m", "pip", "install", "-r", "requirements.txt"],
        backend,
        "安装锁定的后端依赖",
    )
    print("[启动] 检查并补齐 M2 独立服务密钥", flush=True)
    backend_helper(python, backend, "scripts.dev_config", env=env)
    backend_check(python, backend, before_migration=True, env=env)
    run_step([npm, "ci"], root / "frontend", "安装锁定的前端依赖")
    run_step(
        [python, "-m", "alembic", "upgrade", "head"],
        backend,
        "升级数据库结构",
        quiet=True,
        env=env,
    )
    run_step(
        [python, "-m", "scripts.seed_m2"],
        backend,
        "初始化公共层、M3 和 M2 演示账号、教室及审批范围（保留已有数据）",
        quiet=True,
        env=env,
    )


def start_process(command, cwd, env=None):
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    job = WindowsJob() if os.name == "nt" else None
    try:
        process = subprocess.Popen(
            [str(part) for part in command], cwd=cwd, env=env, **options
        )
        if job is not None:
            job.assign(process)
            process.dev_job = job
        return process
    except BaseException:
        if job is not None:
            job.close()
        if "process" in locals() and process.poll() is None:
            process.kill()
            process.wait()
        raise


class WindowsJob:
    """关闭 Job 时清理整个子进程树，即使 npm / reload 父进程已提前退出。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        import ctypes

        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def stop_process(process):
    job = getattr(process, "dev_job", None)
    if job is not None:
        job.close()
        process.wait(timeout=5)
        return
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@contextmanager
def managed_processes():
    processes = []
    try:
        yield processes
    finally:
        first_error = None
        for process in reversed(processes):
            try:
                stop_process(process)
            except (OSError, subprocess.SubprocessError) as exc:
                first_error = first_error or exc
        if first_error:
            raise first_error


def wait_http(
    url, processes, *, timeout=45, expected_status=None, expected_component=None
):
    # localhost 不走 HTTP_PROXY，避免本机代理将健康检查转发到外部。
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise LaunchError("服务在就绪前退出，请查看上方服务日志。")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status != 200:
                    continue
                if expected_status is None and expected_component is None:
                    return
                body = json.load(response)
                if (
                    isinstance(body, dict)
                    and (
                        expected_status is None or body.get("status") == expected_status
                    )
                    and (
                        expected_component is None
                        or body.get("component") == expected_component
                    )
                ):
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.2)
    raise LaunchError(f"等待 {url} 超时，请查看上方服务日志。")


def serve(root, python, npm, backend_port, frontend_port, resource_port, env):
    backend = root / "backend"
    resource_url = f"http://127.0.0.1:{resource_port}"
    with managed_processes() as processes:
        processes.append(
            start_process(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.resource_api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(resource_port),
                ],
                backend,
                env,
            )
        )
        wait_http(
            f"{resource_url}/health",
            processes,
            expected_status="ok",
            expected_component="D-resource-service",
        )
        backend_check(python, backend, env=env, resource_url=resource_url)
        processes.append(
            start_process(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--reload",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                ],
                backend,
                env,
            )
        )
        wait_http(
            f"http://127.0.0.1:{backend_port}/ready", processes, expected_status="ready"
        )
        frontend_env = dict(
            env, VITE_API_PROXY_TARGET=f"http://127.0.0.1:{backend_port}"
        )
        processes.append(
            start_process(
                [
                    npm,
                    "run",
                    "dev",
                    "--",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(frontend_port),
                    "--strictPort",
                ],
                root / "frontend",
                frontend_env,
            )
        )
        wait_http(f"http://127.0.0.1:{frontend_port}/", processes)
        print(
            f"\n前端：http://127.0.0.1:{frontend_port}/m3/work-orders\n"
            f"M2：http://127.0.0.1:{frontend_port}/m2/applications\n"
            f"接口：http://127.0.0.1:{backend_port}/docs\n"
            f"D 资源服务：{resource_url}/docs\n"
            "请保持此终端打开；按 Ctrl+C 停止全部三个服务。\n",
            flush=True,
        )
        while True:
            if any(process.poll() is not None for process in processes):
                raise LaunchError("一个服务已退出，正在停止其他服务；请查看上方日志。")
            time.sleep(0.5)


def port_number(value):
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 1～65535 之间")
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="校园资源平台本地开发启动器（无需 Docker）"
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="补齐服务密钥、安装依赖、迁移和初始化公共层/M2/M3 演示数据，再启动",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查环境和数据库；结合 --setup 时初始化后退出",
    )
    parser.add_argument("--backend-port", type=port_number, default=8001)
    parser.add_argument("--frontend-port", type=port_number, default=5173)
    parser.add_argument(
        "--resource-port",
        type=port_number,
        default=8002,
        help="D 资源服务端口，自动同步 M2 调用地址",
    )
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        raise LaunchError("请使用 Python 3.12 运行启动器。")
    if os.name == "nt" and not sysconfig.get_platform().startswith("win"):
        raise LaunchError(
            "Windows 请使用原生 Windows Python 3.12，暂不支持 MSYS2 Python。"
        )
    npm = node_tools()
    if args.setup:
        ensure_env(ROOT)
    elif not (ROOT / ".env").is_file():
        raise LaunchError("缺少仓库根目录 .env，请先运行 start.cmd --setup。")
    check_ports(args.backend_port, args.frontend_port, args.resource_port)
    env = dict(
        os.environ, M2_RESOURCE_BASE_URL=f"http://127.0.0.1:{args.resource_port}"
    )
    if args.setup:
        prepare(ROOT, npm, env)
    python = venv_python(ROOT)
    check_venv(python)
    if not (ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js").is_file():
        raise LaunchError("尚未安装前端依赖，请先运行 start.cmd --setup。")
    backend_check(python, ROOT / "backend", env=env)
    if args.check:
        print(
            "环境、独立服务密钥、数据库迁移版本及三个端口检查通过；未启动服务。",
            flush=True,
        )
        return
    serve(
        ROOT,
        python,
        npm,
        args.backend_port,
        args.frontend_port,
        args.resource_port,
        env,
    )


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except KeyboardInterrupt:
        print("\n开发服务已停止。")
    except (LaunchError, OSError, subprocess.SubprocessError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        sys.exit(1)
