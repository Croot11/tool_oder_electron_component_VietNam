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


if __name__ == "__main__":
    unittest.main(verbosity=2)
