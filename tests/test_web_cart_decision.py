"""Kiểm thử bước kiểm tra giỏ trước khi bỏ giỏ (web).

Dùng cart_runner / bộ điền giỏ giả, không mở trình duyệt. Chứng minh:
giỏ trống chạy thẳng; giỏ có đồ thì lượt dừng chờ, keep / clear / cancel đi đúng
nhánh; quá hạn = cancel; làm trống không thành công thì dừng lượt và báo lỗi;
không bao giờ tự làm trống; quét lại (retry_cart) không kiểm tra giỏ.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import lkorder.web as web                                            # noqa: E402
from lkorder.browser.cart import (                                   # noqa: E402
    ADDED, NOT_FOUND, CartItem, CartState, LineResult,
)
from lkorder.seed import CATALOG, SHOPS                              # noqa: E402
from lkorder.web import WAITING_CART, App, make_handler              # noqa: E402

BOM = "ten,so_luong,ky_hieu\nNE555,5,U1\nLM358,2,U2\nTL431,3,U3\n"
URL = "https://s.vn/collections/all"


class FakeFiller:
    """Bộ điền giỏ giả: có sẵn `items` trong giỏ (None = không đọc được)."""

    def __init__(self, items=(), *, unknown=False, clear_works=True):
        self.items = [CartItem(n, q) for n, q in items]
        self.unknown, self.clear_works = unknown, clear_works
        self.cart_floor = None
        self.inspected = self.cleared = 0

    def inspect_cart(self):
        self.inspected += 1
        if self.unknown:
            return CartState()
        return CartState(count=len(self.items), items=list(self.items),
                         empty_confirmed=not self.items)

    def clear_cart(self, log=None):
        self.cleared += 1
        for it in list(self.items):
            if self.clear_works:
                self.items.remove(it)
                if log:
                    log(f"Đã xoá khỏi giỏ: {it.label()}")
        return self.inspect_cart()


class FakeRunner:
    """cart_runner giả có đủ các bước của runner thật: kiểm tra giỏ rồi bỏ giỏ."""

    def __init__(self, filler, not_found=("LM358",)):
        self.filler = filler
        self.not_found = set(not_found)       # dòng không thấy ở lượt đầu
        self.added: list[str] = []

    def __call__(self, lines, shop_url, job, shops):
        if getattr(job, "check_cart", False) \
                and not web.cart_precheck(self.filler, job):
            return
        for i, ln in enumerate(lines):
            if job.cancelled:
                return
            job.start_line(i)
            self.added.append(ln.raw)
            st = NOT_FOUND if ln.raw in self.not_found else ADDED
            job.finish_line(i, LineResult(ln, st, units=ln.qty, pieces=ln.qty,
                                          query=ln.raw, message="giỏ tăng"))


class Base(unittest.TestCase):
    def make_app(self, filler, timeout=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "shops.json").write_text(
            json.dumps({"shops": SHOPS}, ensure_ascii=False), encoding="utf-8")
        (root / "catalog.csv").write_text(CATALOG, encoding="utf-8")
        self.filler = filler
        self.runner = FakeRunner(filler)
        self.app = App(root / "shops.json", root / "catalog.csv",
                       cart_runner=self.runner)
        if timeout is not None:
            self.app.cart_decision_timeout = timeout
        return self.app

    def start(self):
        r = self.app.start_cart({"bom": BOM, "shop_url": URL})
        self.assertTrue(r.get("ok"), r)
        return r["id"]

    def job(self, jid):
        return self.app.job(jid)["job"]

    def wait_status(self, jid, want, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            j = self.job(jid)
            if j["status"] == want:
                return j
            time.sleep(0.02)
        self.fail(f"không tới trạng thái {want}: {self.job(jid)['status']}")

    def finish(self, jid):
        self.app.wait_job(jid)
        return self.job(jid)


class TestEmptyCart(Base):
    def test_gio_trong_chay_thang(self):
        self.make_app(FakeFiller([]))
        jid = self.start()
        j = self.finish(jid)
        self.assertEqual(j["status"], "done")
        self.assertEqual(self.runner.added, ["NE555", "LM358", "TL431"])
        self.assertEqual(self.filler.inspected, 1)
        self.assertEqual(self.filler.cleared, 0)
        self.assertEqual(self.filler.cart_floor, 0)        # mốc cho _wait_added
        self.assertEqual(j["phase"], "")
        self.assertIsNone(j["cart"])


class TestCartHasItems(Base):
    ITEMS = [("IC NE555P", 10), ("Tụ 100nF", 20)]

    def test_job_cho_quyet_dinh_kem_danh_sach(self):
        self.make_app(FakeFiller(self.ITEMS))
        jid = self.start()
        j = self.wait_status(jid, WAITING_CART)
        self.assertEqual(j["cart"]["message"], "Giỏ đang có 2 món")
        self.assertEqual(j["cart"]["count"], 2)
        self.assertTrue(j["cart"]["known"])
        self.assertEqual(j["cart"]["items"],
                         [{"name": "IC NE555P", "qty": 10},
                          {"name": "Tụ 100nF", "qty": 20}])
        self.assertGreater(j["cart_wait_left"], 0)
        self.assertEqual(self.runner.added, [])             # chưa bỏ gì
        self.assertEqual(self.filler.cleared, 0)            # chưa xoá gì
        self.assertTrue(all(it["status"] == "pending" for it in j["items"]))
        # đang chờ vẫn là lượt đang chạy: không cho mở lượt khác
        r = self.app.start_cart({"bom": BOM, "shop_url": URL})
        self.assertIn("error", r)
        self.app.cart_decision({"id": jid, "choice": "cancel"})
        self.finish(jid)

    def test_keep(self):
        self.make_app(FakeFiller(self.ITEMS))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.assertEqual(self.app.cart_decision({"id": jid, "choice": "keep"})["ok"], True)
        j = self.finish(jid)
        self.assertEqual(j["status"], "done")
        self.assertEqual(self.runner.added, ["NE555", "LM358", "TL431"])
        self.assertEqual(self.filler.cleared, 0)
        self.assertEqual(len(self.filler.items), 2)         # món cũ còn nguyên
        self.assertEqual(self.filler.cart_floor, 2)         # mốc = số món lúc kiểm tra
        self.assertIsNone(j["cart"])

    def test_clear(self):
        self.make_app(FakeFiller(self.ITEMS))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.assertTrue(self.app.cart_decision({"id": jid, "choice": "clear"})["ok"])
        j = self.finish(jid)
        self.assertEqual(j["status"], "done")
        self.assertEqual(self.filler.cleared, 1)
        self.assertEqual(self.filler.items, [])
        self.assertEqual(self.filler.cart_floor, 0)
        self.assertEqual(self.runner.added, ["NE555", "LM358", "TL431"])
        log = "\n".join(self.app._jobs[jid].log_lines)
        self.assertIn("Đã xoá khỏi giỏ: IC NE555P × 10", log)   # log từng món
        self.assertIn("Đã xoá khỏi giỏ: Tụ 100nF × 20", log)

    def test_cancel(self):
        self.make_app(FakeFiller(self.ITEMS))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.assertTrue(self.app.cart_decision({"id": jid, "choice": "cancel"})["ok"])
        j = self.finish(jid)
        self.assertEqual(j["status"], "cancelled")
        self.assertEqual(self.runner.added, [])
        self.assertEqual(self.filler.cleared, 0)             # không đụng vào giỏ
        self.assertEqual(len(self.filler.items), 2)
        self.assertTrue(all(it["status"] == "skipped" for it in j["items"]))

    def test_het_thoi_gian_cho_thanh_cancel(self):
        self.make_app(FakeFiller(self.ITEMS), timeout=0.2)
        jid = self.start()
        j = self.finish(jid)
        self.assertEqual(j["status"], "cancelled")
        self.assertEqual(self.runner.added, [])
        self.assertEqual(self.filler.cleared, 0)             # không tự làm trống
        self.assertIn("Quá thời gian", "\n".join(self.app._jobs[jid].log_lines))
        # chọn muộn sau khi hết hạn: bị từ chối
        self.assertIn("error", self.app.cart_decision({"id": jid, "choice": "clear"}))

    def test_bam_dung_khi_dang_cho_thanh_cancel(self):
        self.make_app(FakeFiller(self.ITEMS))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.app.stop_cart({"id": jid})
        j = self.finish(jid)
        self.assertEqual(j["status"], "cancelled")
        self.assertEqual(self.runner.added, [])
        self.assertEqual(self.filler.cleared, 0)

    def test_lam_trong_khong_thanh_cong_dung_luot_bao_loi(self):
        self.make_app(FakeFiller(self.ITEMS, clear_works=False))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.app.cart_decision({"id": jid, "choice": "clear"})
        j = self.finish(jid)
        self.assertEqual(j["status"], "error")
        self.assertIn("Làm trống giỏ không thành công", j["error"])
        self.assertIn("còn 2 món", j["error"])
        self.assertEqual(self.runner.added, [])               # không bỏ giỏ tiếp
        self.assertEqual(self.filler.cleared, 1)

    def test_nut_thanh_toan_bi_chan_thi_dung_luot(self):
        from lkorder.browser.cart import CheckoutRefused

        class Evil(FakeFiller):
            def clear_cart(self, log=None):
                raise CheckoutRefused("Từ chối bấm nút có vẻ là thanh toán")

        self.make_app(Evil(self.ITEMS))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.app.cart_decision({"id": jid, "choice": "clear"})
        j = self.finish(jid)
        self.assertEqual(j["status"], "error")
        self.assertIn("CheckoutRefused", j["error"])
        self.assertEqual(self.runner.added, [])

    def test_lua_chon_sai(self):
        self.make_app(FakeFiller(self.ITEMS))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.assertIn("error", self.app.cart_decision({"id": jid, "choice": "xoa-het"}))
        self.assertIn("error", self.app.cart_decision({"id": jid}))
        self.assertIn("error", self.app.cart_decision({"id": "khong-co", "choice": "keep"}))
        self.assertEqual(self.job(jid)["status"], WAITING_CART)   # vẫn đang chờ
        self.app.cart_decision({"id": jid, "choice": "cancel"})
        self.finish(jid)

    def test_khong_dang_cho_thi_tu_choi_lua_chon(self):
        self.make_app(FakeFiller([]))
        jid = self.start()
        self.finish(jid)
        self.assertIn("error", self.app.cart_decision({"id": jid, "choice": "clear"}))


class TestUnknownCart(Base):
    def test_khong_doc_duoc_gio_van_canh_bao_va_khong_tu_xoa(self):
        self.make_app(FakeFiller(unknown=True), timeout=0.2)
        jid = self.start()
        end = time.time() + 3
        seen = None
        while time.time() < end and seen is None:
            j = self.job(jid)
            if j["status"] == WAITING_CART:
                seen = j["cart"]
            time.sleep(0.01)
        self.assertIsNotNone(seen)
        self.assertFalse(seen["known"])
        self.assertIn("Không xác định được giỏ có đồ hay không", seen["message"])
        j = self.finish(jid)
        self.assertEqual(j["status"], "cancelled")           # hết hạn -> cancel
        self.assertEqual(self.filler.cleared, 0)             # KHÔNG tự làm trống
        self.assertEqual(self.runner.added, [])

    def test_keep_khi_khong_biet_thi_khong_co_moc(self):
        self.make_app(FakeFiller(unknown=True))
        jid = self.start()
        self.wait_status(jid, WAITING_CART)
        self.app.cart_decision({"id": jid, "choice": "keep"})
        self.assertEqual(self.finish(jid)["status"], "done")
        self.assertIsNone(self.filler.cart_floor)            # không có mốc
        self.assertEqual(self.filler.cleared, 0)

    def test_loi_khi_doc_gio_cung_la_khong_biet(self):
        class Broken(FakeFiller):
            def inspect_cart(self):
                raise RuntimeError("trang giỏ sập")

        self.make_app(Broken(), timeout=0.2)
        jid = self.start()
        j = self.finish(jid)
        self.assertEqual(j["status"], "cancelled")
        self.assertEqual(self.filler.cleared, 0)


class TestRetryDoesNotCheckCart(Base):
    def test_retry_cart_khong_kiem_tra_gio(self):
        filler = FakeFiller([("món của lượt trước", 5)])      # giỏ có đồ
        self.make_app(filler)
        # lượt đầu: giỏ trống -> chạy thẳng
        filler.items.clear()
        jid = self.start()
        self.assertEqual(self.finish(jid)["status"], "done")
        inspected = filler.inspected
        filler.items.append(CartItem("món của lượt trước", 5))   # giỏ giờ có đồ

        r = self.app.retry_cart({"id": jid, "items": [{"i": 1, "query": "LM358 DIP"}]})
        self.assertTrue(r.get("ok"), r)
        j = self.finish(jid)
        self.assertEqual(j["status"], "done")                # không dừng chờ
        self.assertEqual(filler.inspected, inspected)        # không đọc giỏ
        self.assertEqual(filler.cleared, 0)
        self.assertEqual(self.runner.added[-1], "LM358 DIP")

    def test_retry_view_khong_bat_kiem_tra(self):
        job = web.CartJob([], URL)
        self.assertTrue(job.check_cart)
        self.assertFalse(web.RetryView(job, []).check_cart)

    def test_runner_that_chi_kiem_tra_gio_o_luot_day_du(self):
        from unittest import mock

        import lkorder.browser as browser_pkg
        import lkorder.browser.cart as cart_mod

        events: list[tuple] = []

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def open_shop(self, url, notify=None, **kw):
                return object()

        class Filler:
            cart_floor = None

            def __init__(self, page, base, **kw):
                pass

            def inspect_cart(self):
                events.append(("inspect",))
                return CartState(count=1, items=[CartItem("món cũ", 1)])

            def clear_cart(self, log=None):
                events.append(("clear",))
                raise AssertionError("không được làm trống")

            def add_line(self, line):
                events.append(("add", line.raw))
                st = NOT_FOUND if line.raw == "NE555" else ADDED
                return LineResult(line, st, units=line.qty, query=line.raw)

        self.make_app(FakeFiller([]))
        self.app.cart_runner = web.browser_cart_runner
        with mock.patch.object(browser_pkg, "BrowserSession", Session), \
                mock.patch.object(cart_mod, "CartFiller", Filler), \
                mock.patch.object(web.time, "sleep", lambda s: None), \
                mock.patch.object(cart_mod, "DEFAULT_DELAY", (0, 0)):
            jid = self.start()
            self.wait_status(jid, WAITING_CART)
            self.assertEqual(events, [("inspect",)])
            self.app.cart_decision({"id": jid, "choice": "keep"})
            j = self.finish(jid)
            self.assertEqual(j["status"], "done", j["error"])
            self.assertEqual([e[0] for e in events],
                             ["inspect", "add", "add", "add"])
            n_before = len(events)
            # quét lại bằng runner thật: không đọc giỏ, không dừng chờ
            r = self.app.retry_cart({"id": jid, "items": [{"i": 0, "query": "NE555 DIP"}]})
            self.assertTrue(r.get("ok"), r)
            self.app.wait_job(jid)
            j = self.job(jid)
            self.assertEqual(j["status"], "done", j["error"])
            self.assertEqual(events[n_before:], [("add", "NE555 DIP")])


class TestHttp(Base):
    def test_api_cart_decision_qua_http(self):
        import urllib.request
        from http.server import ThreadingHTTPServer

        self.make_app(FakeFiller([("IC NE555P", 10)]))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def call(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, headers={
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())

        try:
            jid = self.start()
            self.wait_status(jid, WAITING_CART)
            j = call(f"/api/job?id={jid}")["job"]
            self.assertEqual(j["status"], WAITING_CART)
            self.assertEqual(j["cart"]["message"], "Giỏ đang có 1 món")
            self.assertTrue(call("/api/cart/decision",
                                 {"id": jid, "choice": "keep"})["ok"])
            self.assertEqual(self.finish(jid)["status"], "done")
            self.assertIn("error", call("/api/cart/decision", {"id": jid, "choice": "keep"}))

            with urllib.request.urlopen(base + "/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
            for text in ("/api/cart/decision", "Đang kiểm tra giỏ…",
                         "Giữ nguyên và bỏ thêm", "Làm trống giỏ rồi bỏ",
                         "Xoá ${n} món trong giỏ?", "waiting_cart_decision"):
                self.assertIn(text, html)
            self.assertNotIn("#ff0000", html.lower())        # không dùng đỏ tươi
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
