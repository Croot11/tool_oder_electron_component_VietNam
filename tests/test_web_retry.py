"""Kiểm thử quét lại các dòng chưa thêm được vào giỏ bằng tên đã sửa.

Dùng cart_runner giả, không mở trình duyệt thật.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder.seed import CATALOG, SHOPS          # noqa: E402
from lkorder.web import App, make_handler        # noqa: E402


class FakeRunner:
    """Giả lập shop: chỉ bán NE555 và LM358 (khớp theo khoá chuẩn hoá).

    Ghi lại mọi dòng được chạy để kiểm tra chỉ đúng các dòng gửi lên được
    quét lại, và giữ nguyên số lượng / designator.
    """

    def __init__(self):
        self.calls: list[list] = []

    def __call__(self, lines, shop_url, job, shops):
        from lkorder.browser.cart import ADDED, NOT_FOUND, LineResult
        from lkorder.browser.matcher import Candidate
        self.calls.append([(ln.raw, ln.key, ln.qty, ln.designator, shop_url)
                           for ln in lines])
        for i, ln in enumerate(lines):
            if job.cancelled:
                return
            job.start_line(i)
            if "ne555" in ln.key or "lm358" in ln.key:
                name = "NE555" if "ne555" in ln.key else "LM358"
                cand = Candidate(f"IC {name} DIP-8", f"https://s.vn/p/{name}", 4000,
                                 score=0.95)
                res = LineResult(ln, ADDED, cand, units=ln.qty, pieces=ln.qty,
                                 candidates=[cand], query=ln.raw,
                                 message="giỏ tăng")
            else:
                res = LineResult(ln, NOT_FOUND, query=ln.raw,
                                 message="không có kết quả")
            job.finish_line(i, res)


class RetryBase(unittest.TestCase):
    # dòng 0 thêm được; dòng 1, 2 không thấy vì tên BOM khác tên shop
    BOM = "ten,so_luong,ky_hieu\nNE555,5,U1\nIC_OPAMP_KICAD,2,U2\nTEN_LA_XYZ,3,U3\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.shops_path = root / "shops.json"
        self.catalog_path = root / "catalog.csv"
        self.shops_path.write_text(
            json.dumps({"shops": SHOPS}, ensure_ascii=False), encoding="utf-8")
        self.catalog_path.write_text(CATALOG, encoding="utf-8")
        self.runner = FakeRunner()
        self.app = App(self.shops_path, self.catalog_path, cart_runner=self.runner)

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, url="https://s.vn/collections/all"):
        r = self.app.start_cart({"bom": self.BOM, "shop_url": url})
        self.assertTrue(r.get("ok"), r)
        self.app.wait_job(r["id"])
        return r["id"]

    def job(self, job_id):
        return self.app.job(job_id)["job"]


class TestRetryOk(RetryBase):
    def test_quet_lai_mot_dong_not_found_thanh_added(self):
        jid = self.run_job()
        before = self.job(jid)
        self.assertEqual(before["counts"], {"added": 1, "not_found": 2,
                                            "out_of_stock": 0, "check": 0})
        r = self.app.retry_cart({"id": jid, "items": [{"i": 1, "query": "LM358"}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["total"], 1)
        self.app.wait_job(jid)
        j = self.job(jid)

        self.assertEqual(j["status"], "done")
        self.assertIsNone(j["retry"])
        self.assertEqual(j["counts"], {"added": 2, "not_found": 1,
                                       "out_of_stock": 0, "check": 0})
        self.assertEqual(j["done"], 3)
        row = j["items"][1]
        self.assertEqual(row["status"], "added")
        self.assertEqual(row["group"], "added")
        self.assertEqual(row["need"], "IC_OPAMP_KICAD")      # tên gốc vẫn còn
        self.assertEqual(row["retry_query"], "LM358")        # tìm bằng tên mới
        self.assertEqual(row["units"], 2)                    # giữ số lượng
        # các dòng khác giữ nguyên
        self.assertEqual(j["items"][0], before["items"][0])
        self.assertEqual(j["items"][2], before["items"][2])

        # chỉ chạy lại đúng dòng gửi lên, giữ qty + designator + shop_url
        self.assertEqual(len(self.runner.calls), 2)
        self.assertEqual(self.runner.calls[1],
                         [("LM358", "lm358", 2, "U2",
                           "https://s.vn/collections/all")])

    def test_quet_lai_nhieu_dong_va_van_not_found(self):
        jid = self.run_job()
        r = self.app.retry_cart({"id": jid, "items": [
            {"i": 2, "query": "  van   khong co  "}, {"i": 1, "query": "LM358"}]})
        self.assertTrue(r.get("ok"), r)
        self.app.wait_job(jid)
        j = self.job(jid)
        self.assertEqual(j["counts"]["added"], 2)
        self.assertEqual(j["counts"]["not_found"], 1)
        self.assertEqual(j["items"][2]["retry_query"], "van khong co")
        self.assertEqual(j["items"][2]["query"], "van khong co")
        # chạy theo thứ tự dòng trong BOM
        self.assertEqual([c[0] for c in self.runner.calls[1]],
                         ["LM358", "van khong co"])

    def test_qua_http(self):
        import time
        import urllib.request
        from http.server import ThreadingHTTPServer

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            def call(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(base + path, data=data, headers={
                    "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read())

            jid = self.run_job()
            r = call("/api/cart/retry", {"id": jid, "items": [{"i": 1, "query": "LM358"}]})
            self.assertTrue(r.get("ok"), r)
            deadline = time.time() + 5
            while True:
                j = call(f"/api/job?id={jid}")["job"]
                if j["status"] != "running" or time.time() > deadline:
                    break
                time.sleep(0.05)
            self.assertEqual(j["counts"]["added"], 2)
            self.assertIn("error", call("/api/cart/retry", {"id": "khong-co", "items": []}))

            with urllib.request.urlopen(base + "/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
            self.assertIn("/api/cart/retry", html)
            self.assertIn("Quét lại các dòng đã sửa", html)
            self.assertIn("tìm bằng:", html)
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestRetryErrors(RetryBase):
    def test_job_id_khong_ton_tai(self):
        self.run_job()
        self.assertIn("error", self.app.retry_cart(
            {"id": "khong-co", "items": [{"i": 1, "query": "LM358"}]}))
        self.assertIn("error", self.app.retry_cart(
            {"items": [{"i": 1, "query": "LM358"}]}))

    def test_chi_so_dong_khong_hop_le(self):
        jid = self.run_job()
        before = self.job(jid)
        for bad in ([{"i": 99, "query": "LM358"}], [{"i": -1, "query": "LM358"}],
                    [{"i": "1", "query": "LM358"}], [{"i": True, "query": "LM358"}],
                    [{"query": "LM358"}], ["1"], [], None,
                    [{"i": 1, "query": "   "}],
                    # dòng hợp lệ đi cùng dòng sai: từ chối cả lô, không chạy gì
                    [{"i": 1, "query": "LM358"}, {"i": 7, "query": "NE555"}]):
            r = self.app.retry_cart({"id": jid, "items": bad})
            self.assertIn("error", r, bad)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.job(jid), {**before, "elapsed": self.job(jid)["elapsed"]})

    def test_khong_quet_lai_dong_da_added(self):
        jid = self.run_job()
        r = self.app.retry_cart({"id": jid, "items": [{"i": 0, "query": "NE555P"}]})
        self.assertIn("error", r)
        self.assertEqual(len(self.runner.calls), 1)

    def test_chi_quet_lai_luot_gan_nhat(self):
        old = self.run_job()
        self.run_job()
        self.assertIn("error", self.app.retry_cart(
            {"id": old, "items": [{"i": 1, "query": "LM358"}]}))


class TestRetryConcurrency(RetryBase):
    def make_slow_app(self):
        gate = threading.Event()
        started = threading.Event()
        runner = FakeRunner()

        def slow(lines, url, job, shops):
            if len(runner.calls) == 0:          # lượt đầu chạy nhanh
                return runner(lines, url, job, shops)
            runner.calls.append([ln.raw for ln in lines])
            from lkorder.browser.cart import ADDED, LineResult
            for i, ln in enumerate(lines):
                if job.cancelled:
                    return
                job.start_line(i)
                started.set()
                gate.wait(5)
                job.finish_line(i, LineResult(ln, ADDED, units=ln.qty, query=ln.raw))
        app = App(self.shops_path, self.catalog_path, cart_runner=slow)
        return app, gate, started, runner

    def test_dang_chay_luot_khac_thi_tu_choi(self):
        gate = threading.Event()

        def slow(lines, url, job, shops):
            for i in range(len(lines)):
                job.start_line(i)
                gate.wait(5)
                if job.cancelled:
                    return
        app = App(self.shops_path, self.catalog_path, cart_runner=slow)
        r = app.start_cart({"bom": self.BOM, "shop_url": "https://s.vn"})
        self.assertEqual(app.job()["job"]["status"], "running")
        err = app.retry_cart({"id": r["id"], "items": [{"i": 1, "query": "LM358"}]})
        self.assertIn("Đang chạy", err.get("error", ""))
        app.stop_cart({"id": r["id"]})
        gate.set()
        app.wait_job(r["id"])

    def test_dang_quet_lai_thi_tu_choi_ca_quet_lai_lan_bat_dau(self):
        app, gate, started, _ = self.make_slow_app()
        r = app.start_cart({"bom": self.BOM, "shop_url": "https://s.vn"})
        app.wait_job(r["id"])
        jid = r["id"]
        self.assertTrue(app.retry_cart(
            {"id": jid, "items": [{"i": 1, "query": "LM358"}]}).get("ok"))
        started.wait(5)
        j = app.job(jid)["job"]
        self.assertEqual(j["status"], "running")
        self.assertEqual(j["mode"], "retry")
        self.assertEqual(j["retry"], {"total": 1, "done": 0})
        self.assertIn("error", app.retry_cart(
            {"id": jid, "items": [{"i": 2, "query": "NE555"}]}))
        self.assertIn("error", app.start_cart(
            {"bom": "NE555", "shop_url": "https://s.vn"}))
        gate.set()
        app.wait_job(jid)
        self.assertEqual(app.job(jid)["job"]["status"], "done")

    def test_dung_giua_luot_quet_lai(self):
        app, gate, started, runner = self.make_slow_app()
        r = app.start_cart({"bom": self.BOM, "shop_url": "https://s.vn"})
        app.wait_job(r["id"])
        jid = r["id"]
        before = app.job(jid)["job"]
        self.assertTrue(app.retry_cart({"id": jid, "items": [
            {"i": 1, "query": "LM358"}, {"i": 2, "query": "TEN_MOI"}]}).get("ok"))
        started.wait(5)
        self.assertTrue(app.stop_cart({"id": jid}).get("ok"))
        gate.set()
        app.wait_job(jid)
        j = app.job(jid)["job"]

        self.assertEqual(j["status"], "cancelled")
        self.assertEqual(runner.calls[-1], ["LM358", "TEN_MOI"])
        # dòng đang chạy lúc bấm Dừng vẫn xong và có kết quả mới
        self.assertEqual(j["items"][1]["status"], "added")
        self.assertEqual(j["items"][1]["retry_query"], "LM358")
        # dòng chưa chạy tới: giữ kết quả cũ, ghi rõ tên mới chưa được dùng
        row = j["items"][2]
        self.assertEqual(row["status"], "not_found")
        self.assertEqual(row["group"], "not_found")
        self.assertEqual(row["retry_skipped"], "TEN_MOI")
        self.assertNotIn("retry_query", row)
        self.assertEqual(row["message"], before["items"][2]["message"])
        self.assertEqual(j["counts"], {"added": 2, "not_found": 1,
                                       "out_of_stock": 0, "check": 0})
        self.assertEqual(j["done"], 3)
        self.assertIsNone(j["retry"])

        # dừng xong thì quét lại tiếp được, ghi chú 'chưa quét lại' biến mất
        self.assertTrue(app.retry_cart(
            {"id": jid, "items": [{"i": 2, "query": "TEN_MOI"}]}).get("ok"))
        app.wait_job(jid)
        row = app.job(jid)["job"]["items"][2]
        self.assertEqual(row["retry_query"], "TEN_MOI")
        self.assertNotIn("retry_skipped", row)

    def test_runner_loi_khi_quet_lai_giu_ket_qua_cu(self):
        calls = []

        def runner(lines, url, job, shops):
            calls.append(1)
            if len(calls) == 1:
                return FakeRunner()(lines, url, job, shops)
            job.log("Đang mở trình duyệt…")
            raise RuntimeError("không mở được Chrome")
        app = App(self.shops_path, self.catalog_path, cart_runner=runner)
        r = app.start_cart({"bom": self.BOM, "shop_url": "https://s.vn"})
        app.wait_job(r["id"])
        self.assertTrue(app.retry_cart(
            {"id": r["id"], "items": [{"i": 1, "query": "LM358"}]}).get("ok"))
        app.wait_job(r["id"])
        j = app.job(r["id"])["job"]
        self.assertEqual(j["status"], "error")
        self.assertIn("không mở được Chrome", j["error"])
        self.assertEqual(j["items"][1]["status"], "not_found")
        self.assertEqual(j["items"][1]["retry_skipped"], "LM358")


class TestRetryBrowserRunner(RetryBase):
    """browser_cart_runner thật (trình duyệt + CartFiller giả) chạy qua RetryView."""

    def test_dung_lai_runner_that_cung_shop_va_co_do_tre(self):
        from unittest import mock

        import lkorder.browser as browser_pkg
        import lkorder.browser.cart as cart_mod
        import lkorder.web as web
        from lkorder.browser.cart import ADDED, LineResult

        opened, added, sleeps = [], [], []

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def open_shop(self, url, notify=None, **kw):
                opened.append(url)
                return object()

        class Filler:
            def __init__(self, page, base, **kw):
                self.base = base

            def add_line(self, line):
                added.append((self.base, line.raw, line.qty, line.designator))
                return LineResult(line, ADDED, units=line.qty, query=line.raw)

        # lượt đầu dùng runner giả; lượt quét lại dùng runner thật
        jid = self.run_job("https://s.vn/collections/all")
        self.app.cart_runner = web.browser_cart_runner
        with mock.patch.object(browser_pkg, "BrowserSession", Session), \
                mock.patch.object(cart_mod, "CartFiller", Filler), \
                mock.patch.object(web.time, "sleep", sleeps.append):
            r = self.app.retry_cart({"id": jid, "items": [
                {"i": 1, "query": "LM358"}, {"i": 2, "query": "NE555 DIP"}]})
            self.assertTrue(r.get("ok"), r)
            self.app.wait_job(jid)
        j = self.job(jid)
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual(opened, ["https://s.vn/collections/all"])
        self.assertEqual(added, [("https://s.vn", "LM358", 2, "U2"),
                                 ("https://s.vn", "NE555 DIP", 3, "U3")])
        lo, hi = cart_mod.DEFAULT_DELAY
        self.assertEqual(len(sleeps), 1)                 # nghỉ giữa hai dòng
        self.assertTrue(lo <= sleeps[0] <= hi)
        self.assertEqual(j["counts"]["added"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
