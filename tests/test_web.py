"""Kiểm thử lớp web: API trả đúng dữ liệu, và ghi file cấu hình an toàn.

Dùng thư mục tạm riêng cho mỗi test, không đụng vào data/ thật của người dùng.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder.seed import CATALOG, SHOPS          # noqa: E402
from lkorder.web import App, shop_json           # noqa: E402


class WebBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.shops_path = root / "shops.json"
        self.catalog_path = root / "catalog.csv"
        self.shops_path.write_text(
            json.dumps({"shops": SHOPS}, ensure_ascii=False), encoding="utf-8")
        self.catalog_path.write_text(CATALOG, encoding="utf-8")
        self.app = App(self.shops_path, self.catalog_path)

    def tearDown(self):
        self.tmp.cleanup()


class TestState(WebBase):
    def test_tra_ve_shop_va_so_san_pham(self):
        st = self.app.state()
        self.assertEqual(len(st["shops"]), len(SHOPS))
        self.assertGreater(st["catalog"]["n_offers"], 0)
        self.assertIn("id", st["shops"][0])


class TestSolve(WebBase):
    BOM = "STM32F103C8T6 x2\nNE555 x5\nĐiện trở 10K 1/4W x100"

    def test_giai_duoc_va_tra_du_truong(self):
        r = self.app.solve({"bom": self.BOM, "penalty": 20_000})
        self.assertNotIn("error", r)
        s = r["solution"]
        self.assertGreaterEqual(s["n_shops"], 1)
        self.assertEqual(s["total"], s["parts_cost"] + s["shipping_cost"])
        self.assertEqual(
            s["parts_cost"],
            sum(i["subtotal"] for o in s["orders"] for i in o["items"]),
        )

    def test_bom_rong_bao_loi_ro_rang(self):
        self.assertIn("error", self.app.solve({"bom": ""}))
        self.assertIn("error", self.app.solve({"bom": "   \n# chỉ có chú thích"}))

    def test_max_shops_duoc_ton_trong(self):
        r = self.app.solve({"bom": self.BOM, "penalty": 0, "max_shops": 1})
        self.assertLessEqual(r["solution"]["n_shops"], 1)

    def test_penalty_cao_khong_lam_giam_so_mon_mua_duoc(self):
        lo = self.app.solve({"bom": self.BOM, "penalty": 0})
        hi = self.app.solve({"bom": self.BOM, "penalty": 500_000})
        self.assertEqual(len(lo["solution"]["unmet"]), len(hi["solution"]["unmet"]))
        self.assertLessEqual(hi["solution"]["n_shops"], lo["solution"]["n_shops"])

    def test_bao_mon_khong_tim_thay(self):
        r = self.app.solve({"bom": "MOT_CON_KHONG_AI_BAN_XYZ x1"})
        self.assertEqual(len(r["solution"]["unmet"]), 1)

    def test_kich_ban_thieu_hang_duoc_danh_dau(self):
        """Phương án ít đơn hơn có thể rẻ hơn chỉ vì nó thiếu đồ — phải nói rõ."""
        r = self.app.solve({"bom": self.BOM + "\nDHT22 x1\nNRF24L01 x2"})
        for sc in r["scenarios"]:
            self.assertIn("unmet", sc)
        cheap_incomplete = [s for s in r["scenarios"] if s["unmet"]]
        for s in cheap_incomplete:
            self.assertGreater(s["unmet"], 0)

    def test_gop_dong_trung_trong_bom(self):
        r = self.app.solve({"bom": "NE555 x2\nIC NE555 x3"})
        self.assertEqual(r["n_lines"], 1)
        qty = sum(i["need_qty"] for o in r["solution"]["orders"] for i in o["items"])
        self.assertEqual(qty, 5)


class TestMatch(WebBase):
    def test_liet_ke_ung_vien_kem_diem(self):
        r = self.app.matches({"bom": "NE555 x5"})
        self.assertEqual(len(r["rows"]), 1)
        found = r["rows"][0]["found"]
        self.assertTrue(found)
        self.assertLessEqual(found[1]["score"], found[0]["score"])   # đã sắp hạng

    def test_bom_rong_tra_danh_sach_rong(self):
        self.assertEqual(self.app.matches({"bom": ""})["rows"], [])


class TestSaveShops(WebBase):
    def test_luu_va_doc_lai(self):
        shops = [shop_json(s) for s in self.app.shops().values()]
        shops[0]["shipping_fee"] = 12_345
        shops[0]["enabled"] = False
        r = self.app.save_shops({"shops": shops})
        self.assertTrue(r.get("ok"))

        again = list(self.app.shops().values())
        self.assertEqual(again[0].shipping_fee, 12_345)
        self.assertFalse(again[0].enabled)

    def test_tu_choi_danh_sach_rong(self):
        self.assertIn("error", self.app.save_shops({"shops": []}))

    def test_khong_nhan_so_am(self):
        shops = [shop_json(s) for s in self.app.shops().values()]
        shops[0]["shipping_fee"] = -5000
        self.app.save_shops({"shops": shops})
        self.assertEqual(list(self.app.shops().values())[0].shipping_fee, 0)


class TestSaveCatalog(WebBase):
    def test_luu_va_dem_lai_san_pham(self):
        csv = "shop,ten,gia\nnshop,IC NE555,4000\nnshop,IC LM358,3000\n"
        r = self.app.save_catalog({"csv": csv})
        self.assertEqual(r["n_offers"], 2)
        self.assertIn("IC NE555", self.app.read_catalog()["csv"])

    def test_tu_choi_noi_dung_rong(self):
        self.assertIn("error", self.app.save_catalog({"csv": "   "}))
        # file cũ phải còn nguyên
        self.assertIn("thegioiic", self.app.read_catalog()["csv"])


def fake_runner(lines, shop_url, job, shops):
    """Giả lập trình duyệt: NE555 thêm được, LM358 cần kiểm tra, còn lại không thấy."""
    from lkorder.browser.cart import (ADDED, NOT_FOUND, UNCERTAIN, LineResult)
    from lkorder.browser.matcher import Candidate
    job.log(f"vào {shop_url}")
    for i, ln in enumerate(lines):
        if job.cancelled:
            return
        job.start_line(i)
        if "ne555" in ln.key:
            cand = Candidate("IC NE555P DIP-8", "https://s.vn/p/ne555", 4000, score=0.95)
            res = LineResult(ln, ADDED, cand, units=ln.qty, pieces=ln.qty,
                             candidates=[cand], message="giỏ tăng 0 -> 1")
        elif "lm358" in ln.key:
            cand = Candidate("IC LM358 SOP", "https://s.vn/p/lm358", 3000, score=0.6)
            res = LineResult(ln, UNCERTAIN, cand, candidates=[cand], message="chưa chắc")
        else:
            res = LineResult(ln, NOT_FOUND, message="không có kết quả")
        job.finish_line(i, res)


class TestCartJob(WebBase):
    BOM = "NE555 x5\nLM358 x2\nMOT_CON_KHONG_AI_BAN_XYZ x1"

    def setUp(self):
        super().setUp()
        self.app = App(self.shops_path, self.catalog_path, cart_runner=fake_runner)

    def run_job(self, bom=BOM, url="https://s.vn/collections/all"):
        r = self.app.start_cart({"bom": bom, "shop_url": url, "filename": "bom.csv"})
        self.assertTrue(r.get("ok"), r)
        self.app.wait_job(r["id"])
        return self.app.job(r["id"])["job"]

    def test_chay_xong_va_chia_nhom(self):
        j = self.run_job()
        self.assertEqual(j["status"], "done")
        self.assertEqual(j["total"], 3)
        self.assertEqual(j["done"], 3)
        self.assertEqual(j["counts"], {"added": 1, "not_found": 1, "check": 1})
        first = j["items"][0]
        self.assertEqual(first["status"], "added")
        self.assertEqual(first["title"], "IC NE555P DIP-8")
        self.assertEqual(first["units"], 5)
        self.assertEqual(j["filename"], "bom.csv")
        self.assertIn("vào https://s.vn/collections/all", j["log"])

    def test_doc_bom_dang_csv(self):
        j = self.run_job("ten,so_luong\nNE555,3\nLM358,1\n")
        self.assertEqual(j["total"], 2)
        self.assertEqual(j["items"][0]["qty"], 3)

    def test_bom_rong_hoac_link_sai_bao_loi(self):
        self.assertIn("error", self.app.start_cart({"bom": "", "shop_url": "https://s.vn"}))
        self.assertIn("error", self.app.start_cart({"bom": "NE555", "shop_url": "s.vn"}))
        self.assertIn("error", self.app.start_cart({"bom": "NE555", "shop_url": "ftp://s.vn"}))

    def test_runner_loi_thi_bao_loi_va_danh_dau_bo_qua(self):
        def boom(lines, url, job, shops):
            raise RuntimeError("không mở được Chrome")
        app = App(self.shops_path, self.catalog_path, cart_runner=boom)
        r = app.start_cart({"bom": "NE555 x1", "shop_url": "https://s.vn"})
        app.wait_job(r["id"])
        j = app.job()["job"]
        self.assertEqual(j["status"], "error")
        self.assertIn("không mở được Chrome", j["error"])
        self.assertEqual(j["items"][0]["status"], "skipped")
        self.assertEqual(j["counts"]["check"], 1)

    def test_khong_cho_chay_hai_luot_cung_luc_va_dung_duoc(self):
        import threading
        gate = threading.Event()

        def slow(lines, url, job, shops):
            for i in range(len(lines)):
                job.start_line(i)
                gate.wait(5)
                if job.cancelled:
                    return

        app = App(self.shops_path, self.catalog_path, cart_runner=slow)
        r = app.start_cart({"bom": "NE555\nLM358", "shop_url": "https://s.vn"})
        self.assertEqual(app.job()["job"]["status"], "running")
        self.assertIn("error", app.start_cart({"bom": "NE555", "shop_url": "https://s.vn"}))
        self.assertTrue(app.stop_cart({"id": r["id"]}).get("ok"))
        gate.set()
        app.wait_job(r["id"])
        j = app.job(r["id"])["job"]
        self.assertEqual(j["status"], "cancelled")
        self.assertTrue(all(it["status"] == "skipped" for it in j["items"]))
        # xong rồi thì chạy lượt mới được
        r2 = app.start_cart({"bom": "NE555", "shop_url": "https://s.vn"})
        self.assertTrue(r2.get("ok"))
        app.wait_job(r2["id"])

    def test_chua_co_luot_nao(self):
        self.assertEqual(self.app.job(), {"job": None})
        self.assertIn("error", self.app.job("khong-co"))

    def test_shop_base_url(self):
        from lkorder.web import shop_base_url
        self.assertEqual(shop_base_url("https://www.a.vn/x/y?q=1"), "https://www.a.vn")
        self.assertEqual(shop_base_url("a.vn"), "")


class TestHttp(WebBase):
    """Gọi thật qua HTTP: /api/start rồi poll /api/job."""

    def test_start_va_poll(self):
        import threading
        import time
        import urllib.request
        from http.server import ThreadingHTTPServer
        from lkorder.web import make_handler

        app = App(self.shops_path, self.catalog_path, cart_runner=fake_runner)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            def call(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(base + path, data=data, headers={
                    "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read())

            r = call("/api/start", {"bom": "NE555 x2\nXYZ_KHONG_CO x1",
                                    "shop_url": "https://s.vn"})
            self.assertTrue(r["ok"])
            deadline = time.time() + 5
            while True:
                j = call(f"/api/job?id={r['id']}")["job"]
                if j["status"] != "running" or time.time() > deadline:
                    break
                time.sleep(0.05)
            self.assertEqual(j["status"], "done")
            self.assertEqual(j["counts"]["added"], 1)
            self.assertEqual(j["counts"]["not_found"], 1)

            with urllib.request.urlopen(base + "/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
            self.assertIn('id="startbtn"', html)
            self.assertIn('id="shopurl"', html)
            self.assertIn('data-tab="order"', html)      # thẻ cũ vẫn còn
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
