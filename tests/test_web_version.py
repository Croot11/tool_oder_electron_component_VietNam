"""Kiểm thử: báo rõ khi cổng bận, và /api/version cho biết code đã đổi chưa."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder import cli                                      # noqa: E402
from lkorder import web                                      # noqa: E402
from lkorder.web import (App, PortBusy, latest_source_mtime,  # noqa: E402
                         make_handler, make_server, parse_netstat_pid)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TmpApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "src"
        self.src.mkdir()
        self.py = self.src / "mod.py"
        self.py.write_text("x = 1\n", encoding="utf-8")
        old = time.time() - 3600
        os.utime(self.py, (old, old))
        self.app = App(self.root / "shops.json", self.root / "catalog.csv",
                       source_root=self.src)

    def tearDown(self):
        self.tmp.cleanup()


class TestVersion(TmpApp):
    def test_chua_doi_thi_khong_canh_bao(self):
        v = self.app.version()
        self.assertFalse(v["source_changed"])
        self.assertEqual(v["pid"], os.getpid())
        self.assertAlmostEqual(v["started"], self.app.started)
        self.assertIn("commit", v)

    def test_sua_file_sau_khi_khoi_dong_thi_canh_bao(self):
        future = time.time() + 5
        os.utime(self.py, (future, future))
        v = self.app.version()
        self.assertTrue(v["source_changed"])
        self.assertGreater(v["source_mtime"], v["started"])

    def test_them_file_py_moi_cung_tinh(self):
        new = self.src / "sub" / "moi.py"
        new.parent.mkdir()
        new.write_text("", encoding="utf-8")
        future = time.time() + 5
        os.utime(new, (future, future))
        self.assertTrue(self.app.version()["source_changed"])

    def test_doi_commit_thi_canh_bao(self):
        self.app.commit = "aaaaaaa"
        with mock.patch.object(web, "git_commit", return_value="bbbbbbb"):
            v = self.app.version()
        self.assertTrue(v["source_changed"])
        self.assertEqual(v["commit_now"], "bbbbbbb")

    def test_khong_co_git_thi_commit_rong(self):
        self.assertEqual(web.git_commit(self.root), "")

    def test_mtime_moi_nhat(self):
        self.assertAlmostEqual(latest_source_mtime(self.src),
                               self.py.stat().st_mtime)
        self.assertEqual(latest_source_mtime(self.root / "khong_co"), 0.0)


class TestVersionHttp(TmpApp):
    def test_api_version_qua_http(self):
        port = free_port()
        httpd = make_server(port, make_handler(self.app))
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/version", timeout=5) as r:
                v = json.loads(r.read())
            self.assertFalse(v["source_changed"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                html = r.read().decode("utf-8")
            self.assertIn("/api/version", html)
            self.assertIn('id="ver"', html)
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestPortBusy(unittest.TestCase):
    def test_cong_ban_thi_bao_loi_ngay(self):
        port = free_port()
        app = App(None, None)
        first = make_server(port, make_handler(app))
        try:
            with mock.patch.object(web, "find_pid_on_port", return_value=4321):
                with self.assertRaises(PortBusy) as cm:
                    make_server(port, make_handler(app))
            msg = str(cm.exception)
            self.assertIn(f"Cổng {port} đang được tiến trình khác dùng", msg)
            self.assertIn("PID 4321", msg)
            self.assertIn("--port", msg)
        finally:
            first.server_close()

    def test_khong_tim_duoc_pid(self):
        self.assertIn("PID không rõ", str(PortBusy(8765)))

    def test_cli_in_loi_va_thoat(self):
        port = free_port()
        holder = make_server(port, make_handler(App(None, None)))
        try:
            err = io.StringIO()
            with mock.patch.object(web, "find_pid_on_port", return_value=99), \
                    redirect_stderr(err):
                code = cli.main(["web", "--port", str(port), "--no-open"])
            self.assertEqual(code, 2)
            self.assertIn(f"Cổng {port}", err.getvalue())
            self.assertIn("PID 99", err.getvalue())
        finally:
            holder.server_close()

    def test_doc_netstat(self):
        text = (
            "\nActive Connections\n\n"
            "  Proto  Local Address          Foreign Address        State           PID\n"
            "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1000\n"
            "  TCP    127.0.0.1:87650        0.0.0.0:0              LISTENING       1\n"
            "  TCP    127.0.0.1:8765         127.0.0.1:50000        ESTABLISHED     2\n"
            "  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       5555\n"
        )
        self.assertEqual(parse_netstat_pid(text, 8765), 5555)
        self.assertIsNone(parse_netstat_pid(text, 9999))


if __name__ == "__main__":
    unittest.main()
