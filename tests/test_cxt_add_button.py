"""Tìm đúng nút CHO VÀO GIỎ của CXT khi đã đăng nhập (không có class addCart)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lkorder.bom import parse_line                                  # noqa: E402
from lkorder.browser import cart as C                               # noqa: E402
from lkorder.browser.matcher import parse_html                      # noqa: E402
from lkorder.browser.shops import cxtvn as X                        # noqa: E402

LOGGED_IN = (HERE / "data" / "cxtvn_loggedin.html").read_text(encoding="utf-8")
LOGGED_OUT = LOGGED_IN.replace(
    """<a href="javascript:;" onclick="addToCart(4173, $('#sl').val())">CHO VÀO GIỎ</a>""",
    """<a href="https://linhkien.cxtvn.com/dang-nhap.html">CHO VÀO GIỎ</a>""")
URL = "https://linhkien.cxtvn.com/4173-tu-dien-100nf-0603.html"


class Handle:
    def __init__(self, page, node):
        self.page, self.node = page, node
        self.text = node.text()

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        v = self.node.get(name)
        return v if v else None

    def is_visible(self):
        return True

    def fill(self, v):
        self.page.filled.append(v)

    def click(self):
        self.page.clicked.append((self.text, self.node.get("onclick")))
        self.page.cart += 1
        self.page.html = self.page.tpl.replace("{count}", str(self.page.cart))


class Page:
    """Trang giả hiểu đúng các bộ chọn mà CxtCartFiller dùng."""

    def __init__(self, tpl, cart=2):
        self.tpl, self.cart = tpl, cart
        self.html = tpl.replace("{count}", str(cart))
        self.url = URL
        self.filled, self.clicked, self.asked = [], [], []

    def goto(self, url, wait_until=None):
        self.url = url
        self.html = self.tpl.replace("{count}", str(self.cart))

    def content(self):
        return self.html

    def query_selector_all(self, sel):
        self.asked.append(sel)
        root = parse_html(self.html)
        if sel == ".sale-button a":
            nodes = [n for n in root.iter() if n.tag == "a" and any(
                "sale-button" in a.classes for a in n.ancestors())]
        elif sel == "a[onclick*='addToCart']":
            nodes = [n for n in root.iter()
                     if n.tag == "a" and "addToCart" in n.get("onclick")]
        elif sel == ".addCart":
            nodes = [n for n in root.iter() if "addcart" in n.classes]
        else:
            nodes = []
        return [Handle(self, n) for n in nodes]

    def query_selector(self, sel):
        if sel in ("#sl", "input#sl") and 'id="sl"' in self.html:
            return Handle(self, parse_html(self.html).find(lambda n: n.id == "sl"))
        return None


def filler(page):
    clock = [0.0]

    def sleep(s):
        clock[0] += s

    return X.CxtCartFiller(page, "https://linhkien.cxtvn.com", sleep=sleep,
                           rand=lambda a, b: a, clock=lambda: clock[0],
                           confirm_timeout=1.0, poll=0.5,
                           no_image_out_of_stock=False)


class AddToCartOnclickTest(unittest.TestCase):
    def test_plain_add(self):
        self.assertTrue(X.is_add_to_cart_onclick("addToCart(4173, $('#sl').val())"))
        self.assertTrue(X.is_add_to_cart_onclick("addToCart(1)"))

    def test_buy_now_fifth_arg_true(self):
        self.assertFalse(X.is_add_to_cart_onclick(
            "addToCart(4173, $('#sl').val(), 0, 0, true)"))
        self.assertFalse(X.is_add_to_cart_onclick(
            "addToCart(4173,$('#sl').val(),'','', TRUE);return false;"))

    def test_fifth_arg_false_is_ok(self):
        self.assertTrue(X.is_add_to_cart_onclick(
            "addToCart(4173, $('#sl').val(), 0, 0, false)"))

    def test_checkout_and_garbage(self):
        self.assertFalse(X.is_add_to_cart_onclick("addToCart(1,'checkout')"))
        self.assertFalse(X.is_add_to_cart_onclick("buyNow(1)"))
        self.assertFalse(X.is_add_to_cart_onclick("addToCart(1, $('#sl').val("))


class AddButtonTest(unittest.TestCase):
    def test_finds_sale_button_link_without_addcart_class(self):
        page = Page(LOGGED_IN)
        h = filler(page)._add_button()
        self.assertIsNotNone(h)
        self.assertEqual(h.text, "CHO VÀO GIỎ")
        self.assertEqual(h.get_attribute("onclick"), "addToCart(4173, $('#sl').val())")
        self.assertNotIn("btn-buy", h.get_attribute("class") or "")

    def test_never_picks_buy_now_or_order(self):
        # Chỉ còn MUA NGAY + ĐẶT HÀNG: không được trả về nút nào.
        html = LOGGED_IN.replace(
            """<a href="javascript:;" onclick="addToCart(4173, $('#sl').val())">CHO VÀO GIỎ</a>""",
            "")
        self.assertIsNone(filler(Page(html))._add_button())
        # MUA NGAY mà ghi nhầm chữ "CHO VÀO GIỎ" vẫn bị loại nhờ class/onclick.
        html2 = html.replace(">MUA NGAY<", ">CHO VÀO GIỎ<")
        self.assertIsNone(filler(Page(html2))._add_button())
        html3 = html2.replace('class="btn-buy" ', "")
        self.assertIsNone(filler(Page(html3))._add_button())

    def test_logged_out_link_not_clicked(self):
        self.assertIsNone(filler(Page(LOGGED_OUT))._add_button())

    def test_click_add_refuses_buy_now(self):
        page = Page(LOGGED_IN)
        f = filler(page)
        buy = page.query_selector_all(".sale-button a")[0]
        self.assertIn("btn-buy", buy.get_attribute("class"))
        with self.assertRaises(C.CheckoutRefused):
            f._click_add(buy)
        self.assertEqual(page.clicked, [])

    def test_add_to_cart_confirms_by_slcart(self):
        page = Page(LOGGED_IN, cart=2)
        f = filler(page)
        line = parse_line("100nF 0603")
        cand = X.CxtCandidate(title="Tụ điện 100nF 0603 (50c)", url=URL, price=15000,
                              pack=50)
        res = f.add_to_cart(line, cand)
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertIn("giỏ tăng 2 -> 3", res.message)
        self.assertEqual(page.clicked,
                         [("CHO VÀO GIỎ", "addToCart(4173, $('#sl').val())")])

    def test_add_to_cart_logged_out(self):
        page = Page(LOGGED_OUT)
        f = filler(page)
        res = f.add_to_cart(parse_line("100nF 0603"),
                            X.CxtCandidate(title="Tụ 100nF", url=URL, price=15000))
        self.assertEqual(res.status, C.ERROR)
        self.assertIn("chưa đăng nhập", res.message)
        self.assertEqual(page.clicked, [])


class ParseProductTest(unittest.TestCase):
    def test_logged_in_detected_from_sale_button(self):
        info = X.parse_cxt_product(LOGGED_IN.replace("{count}", "2"))
        self.assertTrue(info.logged_in)
        self.assertEqual(info.cart_count, 2)

    def test_logged_out_detected(self):
        self.assertFalse(X.parse_cxt_product(LOGGED_OUT).logged_in)


if __name__ == "__main__":
    unittest.main()
