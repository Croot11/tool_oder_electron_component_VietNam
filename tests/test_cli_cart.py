"""Kiểm thử lệnh `lk cart`: chạy thêm giỏ / dry-run trên trang giả và báo cáo."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cart import (BASE, HARAVAN_SEARCH, PRODUCT_PAGE, FakePage,  # noqa: E402
                       search_url)

from lkorder import cli, report                                     # noqa: E402
from lkorder.bom import parse_line                                  # noqa: E402
from lkorder.browser import cart as C                               # noqa: E402

SITES = {
    search_url("điện trở 10k"): HARAVAN_SEARCH,
    BASE + "/products/dien-tro-10k-1-4w": PRODUCT_PAGE,
}

UNSURE = {search_url("NE555"): """
    <div class="product-item"><h3 class="product-name">
      <a href="/products/a">IC NE555P</a></h3></div>
    <div class="product-item"><h3 class="product-name">
      <a href="/products/b">IC NE555N</a></h3></div>"""}


def lines():
    return [parse_line("Điện trở 10K 1/4W x150"), parse_line("Điện trở 47K"),
            parse_line("NE555")]


def run(page, dry_run):
    msgs = []
    res = cli.run_cart(page, lines(), BASE, dry_run=dry_run, notify=msgs.append,
                       sleep=lambda s: None, rand=lambda a, b: a,
                       clock=lambda: 0.0, confirm_timeout=0.0)
    return res, msgs


class TestRunCart(unittest.TestCase):
    def test_them_gio(self):
        page = FakePage({**SITES, **UNSURE})
        res, msgs = run(page, dry_run=False)
        self.assertEqual([r.status for r in res],
                         [C.ADDED, C.NOT_FOUND, C.UNCERTAIN])
        self.assertEqual(page.clicked, ["Thêm vào giỏ"])
        self.assertEqual(len(msgs), 3)

    def test_dry_run_khong_bam(self):
        page = FakePage({**SITES, **UNSURE})
        res, msgs = run(page, dry_run=True)
        self.assertEqual([r.status for r in res],
                         [report.CART_MATCHED, C.NOT_FOUND, C.UNCERTAIN])
        self.assertEqual(page.clicked, [])
        self.assertEqual(page.filled, [])
        self.assertEqual(page.cart, 0)
        # không vào trang sản phẩm
        self.assertFalse(any("/products/" in u for u in page.visited))
        self.assertEqual(res[0].units, 2)            # 150 con, gói 100
        self.assertIn("[khớp]", msgs[0])

    def test_dry_run_loi_mot_dong(self):
        class Boom(FakePage):
            def goto(self, url, wait_until=None):
                raise RuntimeError("mất mạng")
        res, _ = run(Boom({}), dry_run=True)
        self.assertTrue(all(r.status == C.ERROR for r in res))


class TestReport(unittest.TestCase):
    def results(self, dry_run):
        return run(FakePage({**SITES, **UNSURE}), dry_run)[0]

    def test_render(self):
        txt = report.render_cart(self.results(False), BASE)
        self.assertIn("ĐÃ THÊM VÀO GIỎ (1)", txt)
        self.assertIn("KHÔNG THẤY TRÊN SHOP (1)", txt)
        self.assertIn("CẦN KIỂM TRA BẰNG MẮT (1)", txt)
        self.assertIn("IC NE555", txt)             # ứng viên để người dùng xem
        self.assertIn("Đã thêm 1/3", txt)
        self.assertIn("KHÔNG thanh toán", txt)

    def test_render_dry_run(self):
        txt = report.render_cart(self.results(True), BASE, dry_run=True)
        self.assertIn("dry-run", txt)
        self.assertIn("Khớp 1/3", txt)

    def test_markdown(self):
        md = report.cart_to_markdown(self.results(False), BASE)
        self.assertTrue(md.startswith("# Giỏ hàng linh kiện"))
        self.assertIn("## ĐÃ THÊM VÀO GIỎ", md)
        self.assertIn("## KHÔNG THẤY TRÊN SHOP", md)
        self.assertIn("## CẦN KIỂM TRA BẰNG MẮT", md)
        self.assertIn("](https://shop.example/products/dien-tro-10k-1-4w)", md)

    def test_rong(self):
        self.assertIn("không có dòng", report.render_cart([]))


class TestCli(unittest.TestCase):
    def test_shop_base(self):
        self.assertEqual(cli._shop_base("hshop.vn/collections/x"),
                         "https://hshop.vn")
        self.assertEqual(cli._shop_base("http://a.vn/"), "http://a.vn")
        with self.assertRaises(SystemExit):
            cli._shop_base("ftp://a.vn")

    def test_parser(self):
        a = cli.build_parser().parse_args(
            ["cart", "bom.txt", "--shop", "https://hshop.vn", "--dry-run",
             "--md", "out.md"])
        self.assertIs(a.func, cli.cmd_cart)
        self.assertTrue(a.dry_run)
        self.assertEqual(a.md, "out.md")
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["cart", "bom.txt"])   # thiếu --shop


if __name__ == "__main__":
    unittest.main()
