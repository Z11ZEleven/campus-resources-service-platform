"""启动器真实子进程/端口验证；不连接数据库，不清理业务数据。"""

import argparse
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts import dev


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LauncherTests(unittest.TestCase):
    def assert_port_closed(self, port):
        # Windows Job 回收完成与 TCP 监听释放可能不同步，等待实际关闭条件。
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pass
            except OSError:
                return
            time.sleep(0.05)
        self.fail(f"进程退出后端口仍可连接：{port}")

    def test_create_config_once_without_replacing_existing_secret(self):
        with tempfile.TemporaryDirectory(prefix="启动 测试 ") as directory:
            root = Path(directory)
            (root / ".env.example").write_text(
                "JWT_SIGNING_KEY=REPLACE_WITH_RANDOM_KEY_AT_LEAST_32_CHARACTERS\n"
                "RESOURCE_SERVICE_KEY=REPLACE_WITH_INDEPENDENT_RANDOM_KEY_AT_LEAST_32_CHARACTERS\n"
                "MYSQL_PASSWORD=REPLACE_WITH_LOCAL_DATABASE_PASSWORD\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(dev.LaunchError, "已生成"):
                dev.ensure_env(root)
            content = (root / ".env").read_text(encoding="utf-8")
            key = content.splitlines()[0].split("=", 1)[1]
            self.assertGreaterEqual(len(key), 32)
            self.assertNotIn("REPLACE_", key)
            resource_key = content.splitlines()[1].split("=", 1)[1]
            self.assertGreaterEqual(len(resource_key), 32)
            self.assertNotIn("REPLACE_", resource_key)
            self.assertNotEqual(key, resource_key)
            original = b"MYSQL_PASSWORD=keep-this-local-value\r\n"
            (root / ".env").write_bytes(original)
            dev.ensure_env(root)
            self.assertEqual((root / ".env").read_bytes(), original)

    def test_port_collision_is_detected_without_terminating_listener(self):
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            with self.assertRaisesRegex(dev.LaunchError, str(occupied)):
                dev.check_ports(occupied, free_port())
            with socket.create_connection(("127.0.0.1", occupied), timeout=1):
                pass

    def test_equal_and_invalid_ports_are_rejected(self):
        with self.assertRaisesRegex(dev.LaunchError, "不同端口"):
            dev.check_ports(18001, 18001)
        for value in ("0", "65536", "abc"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                dev.port_number(value)

    def test_resource_port_is_distinct_and_checked_for_collision(self):
        for ports in ((18101, 18102, 18101), (18101, 18102, 18102)):
            with (
                self.subTest(ports=ports),
                self.assertRaisesRegex(dev.LaunchError, "不同端口"),
            ):
                dev.check_ports(*ports)
        with socket.socket() as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            occupied = listener.getsockname()[1]
            with self.assertRaisesRegex(dev.LaunchError, str(occupied)):
                dev.check_ports(free_port(), free_port(), occupied)

    def test_three_processes_are_cleaned_when_last_service_fails(self):
        processes = []
        with self.assertRaisesRegex(dev.LaunchError, "就绪前退出"):
            with dev.managed_processes() as managed:
                for _ in range(2):
                    process = dev.start_process(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        Path.cwd(),
                    )
                    managed.append(process)
                    processes.append(process)
                failed = dev.start_process(
                    [sys.executable, "-c", "raise SystemExit(9)"], Path.cwd()
                )
                managed.append(failed)
                processes.append(failed)
                failed.wait(timeout=5)
                dev.wait_http(f"http://127.0.0.1:{free_port()}/", managed, timeout=1)
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_failed_sensitive_step_does_not_echo_child_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(dev.LaunchError):
            dev.run_step(
                [
                    sys.executable,
                    "-c",
                    "print('fake-local-secret'); raise SystemExit(7)",
                ],
                Path.cwd(),
                "测试失败诊断",
                quiet=True,
            )
        self.assertNotIn("fake-local-secret", output.getvalue())

    def test_real_http_readiness_and_process_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="启动 HTTP ") as directory:
            root = Path(directory)
            (root / "index.html").write_text(
                json.dumps({"status": "ready"}), encoding="utf-8"
            )
            port = free_port()
            process = dev.start_process(
                [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                root,
            )
            try:
                # 特意设置错误代理，localhost 的检查仍应直接连接。
                original = os.environ.get("http_proxy")
                os.environ["http_proxy"] = "http://127.0.0.1:1"
                try:
                    dev.wait_http(
                        f"http://127.0.0.1:{port}/",
                        [process],
                        timeout=10,
                        expected_status="ready",
                    )
                finally:
                    if original is None:
                        os.environ.pop("http_proxy", None)
                    else:
                        os.environ["http_proxy"] = original
                with self.assertRaisesRegex(dev.LaunchError, "超时"):
                    dev.wait_http(
                        f"http://127.0.0.1:{port}/",
                        [process],
                        timeout=0.3,
                        expected_status="wrong",
                    )
                with self.assertRaisesRegex(dev.LaunchError, "超时"):
                    dev.wait_http(
                        f"http://127.0.0.1:{port}/",
                        [process],
                        timeout=0.3,
                        expected_status="ready",
                        expected_component="D-resource-service",
                    )
            finally:
                dev.stop_process(process)
            self.assertIsNotNone(process.poll())
            self.assert_port_closed(port)

    def test_exited_child_is_not_reported_as_ready(self):
        process = dev.start_process(
            [sys.executable, "-c", "raise SystemExit(8)"], Path.cwd()
        )
        try:
            process.wait(timeout=5)
            with self.assertRaisesRegex(dev.LaunchError, "就绪前退出"):
                dev.wait_http(f"http://127.0.0.1:{free_port()}/", [process], timeout=1)
        finally:
            dev.stop_process(process)

    @unittest.skipUnless(os.name == "nt", "Windows Job 进程树回收")
    def test_grandchild_is_cleaned_even_after_parent_exits(self):
        with tempfile.TemporaryDirectory(prefix="启动 子进程 ") as directory:
            root = Path(directory)
            port = free_port()
            code = (
                "import subprocess,sys,time; time.sleep(0.3); "
                "p=subprocess.Popen([sys.executable,'-m','http.server',sys.argv[1],'--bind','127.0.0.1']); "
                "p.wait()"
            )
            process = dev.start_process([sys.executable, "-c", code, str(port)], root)
            try:
                dev.wait_http(f"http://127.0.0.1:{port}/", [process], timeout=10)
                process.kill()  # 模拟 npm/reload 父进程崩溃，孙进程仍然存活。
                process.wait(timeout=5)
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
            finally:
                dev.stop_process(process)
            self.assert_port_closed(port)


if __name__ == "__main__":
    unittest.main()
