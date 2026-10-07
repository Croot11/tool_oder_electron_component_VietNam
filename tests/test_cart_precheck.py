"""Kiểm tra giỏ có sẵn đồ trước khi bỏ giỏ, làm trống giỏ, và sửa lỗi xác nhận
'đã thêm' sai khi không có số đo trước khi bấm.

Phần đọc là hàm thuần trên chuỗi HTML; phần thao tác dùng trang giả (không mở
trình duyệt, không cần mạng).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lkorder.browser import cart as C                               # noqa: E402
from lkorder.browser.shops import cxtvn as X                        # noqa: E402

BASE = "https://shop.example"
SAMPLE = (HERE / "data" / "cart_page_sample.html").read_text(encoding="utf-8")
EMPTY_PAGE = ('<html><body><span class="cart-count">0</span>'
              '<p>Giỏ hàng của bạn đang trống</p>'
              '<a href="/checkout">Thanh toán</a></body></html>')


# ------------------------------------------------------------ đọc HTML


class ReadCartPageTest(unittest.TestCase):
    def test_count_and_items(self):
        self.assertEqual(C.read_cart_count(SAMPLE), 2)
        items = C.read_cart_items(SAMPLE)
        self.assertEqual([(i.name, i.qty) for i in items],
                         [("IC NE555P DIP-8", 10),
                          ("Điện trở 10K 1/4W (gói 100 con)", 3)])
        self.assertFalse(C.cart_page_says_empty(SAMPLE))

    def test_item_name_not_taken_from_remove_or_checkout_links(self):
        for it in C.read_cart_items(SAMPLE):
            self.assertNotIn(it.name, ("Xoá", "×", "Thanh toán", "Mua ngay"))

    def test_div_layout_and_nested_input(self):
        html = ('<div class="cart"><div class="row"><a href="/p/1">Tụ 100nF</a>'
                '<input name="quantity" value="5"></div>'
                '<div class="row"><a href="/p/2">LM358</a>'
                '<div><input name="quantity" value="2"></div></div></div>')
        self.assertEqual([(i.name, i.qty) for i in C.read_cart_items(html)],
                         [("Tụ 100nF", 5), ("LM358", 2)])

    def test_minicart_duplicate_rows_counted_once(self):
        row = ('<div class="r"><a href="/p/1">Tụ 100nF</a>'
               '<input name="quantity" value="5"></div>')
        self.assertEqual(len(C.read_cart_items(row + row)), 1)

    def test_empty_cart(self):
        self.assertEqual(C.read_cart_items(EMPTY_PAGE), [])
        self.assertTrue(C.cart_page_says_empty(EMPTY_PAGE))
        self.assertTrue(C.cart_page_says_empty("<p>Your cart is empty</p>"))

    def test_state_numbers(self):
        self.assertEqual(C.CartState(count=3).n, 3)
        self.assertEqual(C.CartState(items=[C.CartItem("a")]).n, 1)
        self.assertEqual(C.CartState(count=0, items=[C.CartItem("a")]).n, 1)
        self.assertEqual(C.CartState(empty_confirmed=True).n, 0)
        self.assertTrue(C.CartState(empty_confirmed=True).is_empty)
        unknown = C.CartState()
        self.assertIsNone(unknown.n)
        self.assertFalse(unknown.known)
        self.assertFalse(unknown.is_empty)          # không biết != trống
        self.assertIn("Không xác định", unknown.message())
        self.assertEqual(C.CartState(count=4).message(), "Giỏ đang có 4 món")

    def test_items_removed(self):
        a, b = C.CartItem("A", 1), C.CartItem("B", 2)
        self.assertEqual(C.items_removed([a, b], [b]), [a])
        self.assertEqual(C.items_removed([a, b], [a, b]), [])


class ControlKindTest(unittest.TestCase):
    def test_remove_and_clear_buttons_recognised(self):
        ctl = C.cart_controls(SAMPLE)
        self.assertEqual(ctl["remove"], ["Xoá", "×"])
        self.assertEqual(ctl["clear"], ["Xoá tất cả"])

    def test_checkout_buttons_never_recognised(self):
        flat = " ".join(ctl_label for kinds in C.cart_controls(SAMPLE).values()
                        for ctl_label in kinds)
        for bad in ("Thanh toán", "Mua ngay", "Cập nhật", "Tiếp tục"):
            self.assertNotIn(bad, flat)
        for text, attrs, href in [
                ("Thanh toán", "", ""), ("Mua ngay", "btn-remove", ""),
                ("Đặt hàng", "", ""), ("Checkout", "", ""),
                ("Xoá và thanh toán", "", ""), ("", "remove", "/checkout"),
                ("", "btn-checkout remove", ""), ("Tiếp tục mua hàng", "", "/collections"),
                ("Cập nhật giỏ hàng", "", "")]:
            self.assertEqual(C.control_kind(text, attrs, href), "", (text, attrs))

    def test_kinds(self):
        self.assertEqual(C.control_kind("Xóa"), "remove")
        self.assertEqual(C.control_kind("Remove"), "remove")
        self.assertEqual(C.control_kind("×"), "remove")
        self.assertEqual(C.control_kind("", "cart__remove"), "remove")
        self.assertEqual(C.control_kind("", "", "/cart/change?line=1&quantity=0"),
                         "remove")
        self.assertEqual(C.control_kind("Xóa tất cả"), "clear")
        self.assertEqual(C.control_kind("Clear cart"), "clear")

    def test_selector_lists_have_no_checkout_words(self):
        sel = C.CartSelectors()
        for s in sel.remove_button + sel.clear_button:
            self.assertFalse(C.is_checkout_like(s), s)


# ------------------------------------------------------------ trang giỏ giả


class Handle:
    def __init__(self, page, text, attrs=None, action=None):
        self.page, self.text, self.attrs, self.action = page, text, attrs or {}, action

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def is_visible(self):
        return True

    def click(self):
        self.page.clicked.append(self.text)
        if self.action:
            self.action()


class CartPage:
    """Shop giả: trang giỏ /cart có các món, nút xoá, và nút Thanh toán.

    `has_clear`: có nút 'Xoá tất cả'. `stuck`: bấm xoá không có tác dụng.
    `evil`: MỌI bộ chọn đều trả về nút Thanh toán (kiểm tra chốt chặn).
    """

    def __init__(self, items, *, has_clear=True, stuck=False, evil=False,
                 cart_path="/cart"):
        self.items = list(items)
        self.has_clear, self.stuck, self.evil = has_clear, stuck, evil
        self.cart_url = BASE + cart_path
        self.url, self.html = BASE + "/", "<html><body></body></html>"
        self.visited, self.clicked, self.listeners = [], [], []

    def render(self):
        if not self.items:
            return EMPTY_PAGE
        rows = "".join(
            f'<tr><td><a href="/p/{i}">{n}</a></td>'
            f'<td><input name="updates[]" class="qty" value="{q}"></td>'
            f'<td><a class="cart__remove" href="#">Xoá</a></td></tr>'
            for i, (n, q) in enumerate(self.items))
        return (f'<html><body><span class="cart-count">{len(self.items)}</span>'
                f'<table>{rows}</table><button>Xoá tất cả</button>'
                f'<button name="checkout">Thanh toán</button></body></html>')

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url
        self.html = self.render() if url == self.cart_url \
            else "<html><body>404</body></html>"

    def content(self):
        return self.html

    def on(self, event, handler):
        self.listeners.append((event, handler))

    def remove_listener(self, event, handler):
        self.listeners.remove((event, handler))

    def _remove_first(self):
        if not self.stuck and self.items:
            self.items.pop(0)

    def _clear_all(self):
        if not self.stuck:
            self.items.clear()

    def query_selector(self, sel):
        if self.url != self.cart_url:
            return None
        if self.evil:
            return Handle(self, "Thanh toán", {"class": "btn-checkout"})
        if sel == "button:has-text('Xoá tất cả')" and self.has_clear and self.items:
            return Handle(self, "Xoá tất cả", {"id": "clear-cart"}, self._clear_all)
        if sel == "a.cart__remove" and self.items:
            return Handle(self, "Xoá", {"class": "cart__remove"}, self._remove_first)
        if sel == "button[name='clear']" and self.items:
            return None
        return None


def make_filler(page, **kw):
    clock = [0.0]

    def sleep(s):
        clock[0] += s

    return C.CartFiller(page, BASE, sleep=sleep, rand=lambda a, b: a,
                        clock=lambda: clock[0], confirm_timeout=1.0, poll=0.5, **kw)


class InspectCartTest(unittest.TestCase):
    def test_reads_count_and_items(self):
        page = CartPage([("NE555", 10), ("LM358", 3)])
        st = make_filler(page).inspect_cart()
        self.assertEqual(st.n, 2)
        self.assertEqual([(i.name, i.qty) for i in st.items],
                         [("NE555", 10), ("LM358", 3)])
        self.assertEqual(page.visited[0], BASE + "/cart")
        self.assertEqual(page.clicked, [])             # chỉ đọc

    def test_empty_cart(self):
        st = make_filler(CartPage([])).inspect_cart()
        self.assertTrue(st.is_empty)

    def test_unreadable_cart_is_unknown_not_empty(self):
        page = CartPage([], cart_path="/gio-hang-khac")      # /cart và /gio-hang 404
        st = make_filler(page).inspect_cart()
        self.assertIsNone(st.n)
        self.assertFalse(st.is_empty)
        self.assertEqual(page.visited, [BASE + "/cart", BASE + "/gio-hang"])

    def test_tries_gio_hang_when_cart_missing(self):
        page = CartPage([("NE555", 1)], cart_path="/gio-hang")
        st = make_filler(page).inspect_cart()
        self.assertEqual(st.n, 1)

    def test_cxt_reads_gio_hang_html(self):
        page = CartPage([("Tụ 100nF", 5)], cart_path="/gio-hang.html")
        page.cart_url = "https://cxtvn.com/gio-hang.html"
        f = X.CxtCartFiller(page, "https://linhkien.cxtvn.com",
                            sleep=lambda s: None, no_image_out_of_stock=False)
        self.assertEqual(f.cart_urls, ("https://cxtvn.com/gio-hang.html",))
        st = f.inspect_cart()
        self.assertEqual(page.visited, ["https://cxtvn.com/gio-hang.html"])
        self.assertEqual(st.n, 1)

    def test_cxt_count_from_slcart_without_item_list(self):
        class P(CartPage):
            def render(self):
                return '<html><span class="slcart">4</span><p>giỏ</p></html>'
        page = P([("x", 1)])
        page.cart_url = "https://cxtvn.com/gio-hang.html"
        f = X.CxtCartFiller(page, "https://cxtvn.com", sleep=lambda s: None,
                            no_image_out_of_stock=False)
        st = f.inspect_cart()
        self.assertEqual(st.count, 4)
        self.assertEqual(st.n, 4)
        self.assertEqual(st.items, [])


class ClearCartTest(unittest.TestCase):
    def test_clear_all_button_logs_each_item(self):
        page = CartPage([("NE555", 10), ("LM358", 3)])
        logs = []
        after = make_filler(page).clear_cart(logs.append)
        self.assertTrue(after.is_empty)
        self.assertEqual(page.clicked, ["Xoá tất cả"])
        self.assertEqual(logs, ["Đã xoá khỏi giỏ: NE555 × 10",
                                "Đã xoá khỏi giỏ: LM358 × 3"])
        self.assertEqual(page.listeners, [])           # gỡ trình xử lý hộp thoại

    def test_remove_buttons_one_by_one(self):
        page = CartPage([("NE555", 10), ("LM358", 3), ("TL431", 1)], has_clear=False)
        logs = []
        after = make_filler(page).clear_cart(logs.append)
        self.assertTrue(after.is_empty)
        self.assertEqual(page.clicked, ["Xoá"] * 3)
        self.assertEqual([l.split(": ")[1] for l in logs],
                         ["NE555 × 10", "LM358 × 3", "TL431 × 1"])

    def test_empty_cart_clicks_nothing(self):
        page = CartPage([])
        after = make_filler(page).clear_cart(lambda m: None)
        self.assertTrue(after.is_empty)
        self.assertEqual(page.clicked, [])

    def test_never_clicks_checkout_even_when_page_has_it(self):
        page = CartPage([("NE555", 10), ("LM358", 3)], has_clear=False)
        self.assertIn("Thanh toán", page.render())     # trang giỏ có nút Thanh toán
        make_filler(page).clear_cart(lambda m: None)
        self.assertNotIn("Thanh toán", page.clicked)
        self.assertEqual(set(page.clicked), {"Xoá"})

    def test_checkout_button_behind_every_selector_is_refused(self):
        page = CartPage([("NE555", 10)], evil=True)
        f = make_filler(page)
        with self.assertRaises(C.CheckoutRefused):
            f.clear_cart(lambda m: None)
        self.assertEqual(page.clicked, [])

    def test_unknown_button_is_not_clicked(self):
        page = CartPage([("NE555", 10)])
        page.query_selector = lambda sel: (
            Handle(page, "Cập nhật giỏ hàng") if page.url == page.cart_url else None)
        with self.assertRaises(C.CartClearError):
            make_filler(page).clear_cart(lambda m: None)
        self.assertEqual(page.clicked, [])

    def test_stuck_cart_stops_and_reports_not_empty(self):
        page = CartPage([("NE555", 10), ("LM358", 3)], has_clear=False, stuck=True)
        after = make_filler(page).clear_cart(lambda m: None)
        self.assertFalse(after.is_empty)
        self.assertEqual(after.n, 2)
        self.assertLessEqual(len(page.clicked), 4)     # có giới hạn, không lặp mãi

    def test_no_remove_button_found(self):
        page = CartPage([("NE555", 10)])
        page.query_selector = lambda sel: None
        after = make_filler(page).clear_cart(lambda m: None)
        self.assertEqual(after.n, 1)
        self.assertEqual(page.clicked, [])


# ------------------------------------------------------------ xác nhận 'đã thêm'


class WaitPage:
    def __init__(self, html, url="https://shop.example/products/x"):
        self.html, self.url = html, url

    def content(self):
        return self.html


def count_html(n, extra=""):
    return f'<html><body><span class="cart-count">{n}</span>{extra}</body></html>'


PRODUCT = BASE + "/products/x"


class WaitAddedTest(unittest.TestCase):
    def test_before_none_and_existing_items_is_not_added(self):
        # Giỏ đã có 5 món từ trước, không đọc được số trước khi bấm.
        f = make_filler(WaitPage(count_html(5)))
        ok, why = f._wait_added(None, PRODUCT)
        self.assertFalse(ok, why)

    def test_before_none_uses_precheck_count_as_baseline(self):
        f = make_filler(WaitPage(count_html(5)))
        f.cart_floor = 5
        self.assertFalse(f._wait_added(None, PRODUCT)[0])      # không tăng
        f2 = make_filler(WaitPage(count_html(6)))
        f2.cart_floor = 5
        ok, why = f2._wait_added(None, PRODUCT)
        self.assertTrue(ok, why)
        self.assertIn("5 -> 6", why)
        self.assertEqual(f2.cart_floor, 6)             # mốc được nâng

    def test_floor_not_reused_for_next_line(self):
        page = WaitPage(count_html(6))
        f = make_filler(page)
        f.cart_floor = 5
        self.assertTrue(f._wait_added(None, PRODUCT)[0])
        # dòng sau: bấm mà giỏ vẫn 6 -> không được báo thêm nhầm
        self.assertFalse(f._wait_added(None, PRODUCT)[0])

    def test_failed_add_never_lowers_floor(self):
        f = make_filler(WaitPage(count_html(7)))
        f.cart_floor = 5
        self.assertTrue(f._wait_added(None, PRODUCT)[0])
        self.assertEqual(f.cart_floor, 7)

    def test_message_still_counts_without_baseline(self):
        f = make_filler(WaitPage(count_html(5, "<div>Đã thêm vào giỏ hàng</div>")))
        ok, why = f._wait_added(None, PRODUCT)
        self.assertTrue(ok)
        self.assertIn("báo đã thêm", why)

    def test_redirect_to_cart_counts_without_baseline(self):
        f = make_filler(WaitPage(count_html(5), url=BASE + "/cart"))
        ok, why = f._wait_added(None, PRODUCT)
        self.assertTrue(ok)
        self.assertIn("trang giỏ", why)

    def test_with_before_behaviour_unchanged(self):
        f = make_filler(WaitPage(count_html(3)))
        ok, why = f._wait_added(2, PRODUCT)
        self.assertTrue(ok)
        self.assertEqual(why, "giỏ tăng 2 -> 3")
        self.assertFalse(make_filler(WaitPage(count_html(2)))._wait_added(2, PRODUCT)[0])


CXT_PAGE = ('<html><body><span class="slcart">{n}</span>'
            '<div class="sale-button">{btn}</div></body></html>')


class Btn:
    def __init__(self, text):
        self.text = text

    def inner_text(self):
        return self.text


class WaitAddedCxtTest(unittest.TestCase):
    def filler(self, n, btn="CHO VÀO GIỎ"):
        page = WaitPage(CXT_PAGE.format(n=n, btn=f'<a href="#">{btn}</a>'),
                        url="https://linhkien.cxtvn.com/1-x.html")
        clock = [0.0]

        def sleep(s):
            clock[0] += s

        return X.CxtCartFiller(page, "https://linhkien.cxtvn.com", sleep=sleep,
                               rand=lambda a, b: a, clock=lambda: clock[0],
                               confirm_timeout=1.0, poll=0.5,
                               no_image_out_of_stock=False)

    def test_before_none_and_existing_items_is_not_added(self):
        f = self.filler(5)
        ok, why = f._wait_added_cxt(None, Btn("CHO VÀO GIỎ"))
        self.assertFalse(ok, why)

    def test_before_none_with_baseline(self):
        f = self.filler(6)
        f.cart_floor = 5
        ok, why = f._wait_added_cxt(None, Btn("CHO VÀO GIỎ"))
        self.assertTrue(ok, why)
        self.assertIn("5 -> 6", why)
        f = self.filler(5)
        f.cart_floor = 5
        self.assertFalse(f._wait_added_cxt(None, Btn("CHO VÀO GIỎ"))[0])

    def test_added_button_counts_without_baseline(self):
        f = self.filler(5, btn="ĐÃ THÊM")
        ok, why = f._wait_added_cxt(None, Btn("ĐÃ THÊM"))
        self.assertTrue(ok)
        self.assertIn("ĐÃ THÊM", why)

    def test_with_before(self):
        self.assertTrue(self.filler(3)._wait_added_cxt(2, Btn("CHO VÀO GIỎ"))[0])
        self.assertFalse(self.filler(2)._wait_added_cxt(2, Btn("CHO VÀO GIỎ"))[0])


if __name__ == "__main__":
    unittest.main()
