"""Dò đăng nhập riêng theo shop (login_check) — đặc biệt CXT dựa vào cookie."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lkorder.browser import session as S                            # noqa: E402
from lkorder.browser.shops import cxtvn as X                        # noqa: E402

LOGGED_IN = (HERE / "data" / "cxtvn_loggedin.html").read_text(
    encoding="utf-8").replace("{count}", "3")
LOGGED_OUT = LOGGED_IN.replace(
    """<a href="javascript:;" onclick="addToCart(4173, $('#sl').val())">CHO VÀO GIỎ</a>""",
    """<a href="https://linhkien.cxtvn.com/dang-nhap.html">CHO VÀO GIỎ</a>""")
CXT_COOKIES = [
    {"name": "mem_logged", "value": "1", "domain": ".cxtvn.com", "path": "/"},
    {"name": "mem_token", "value": "abc123", "domain": ".cxtvn.com", "path": "/"},
]


class Ctx:
    def __init__(self, cookies):
        self._cookies = list(cookies)

    def cookies(self, *urls):
        return list(self._cookies)


class Page:
    def __init__(self, html, cookies=(), url="https://linhkien.cxtvn.com/"):
        self.html, self.url = html, url
        self.context = Ctx(cookies)

    def content(self):
        return self.html

    # cách dò chung: không có dấu hiệu nào
    def query_selector(self, sel):
        return None

    def inner_text(self, sel):
        return "CHO VÀO GIỎ 3 MỤC"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class CxtIsLoggedInTest(unittest.TestCase):
    def test_cookie_and_no_login_link(self):
        self.assertTrue(X.is_logged_in(Page(LOGGED_IN, CXT_COOKIES)))

    def test_one_cookie_is_enough(self):
        self.assertTrue(X.is_logged_in(Page(LOGGED_IN, CXT_COOKIES[1:])))

    def test_subdomain_cookie(self):
        c = [dict(CXT_COOKIES[0], domain="linhkien.cxtvn.com")]
        self.assertTrue(X.is_logged_in(Page(LOGGED_IN, c)))

    def test_no_cookie(self):
        self.assertFalse(X.is_logged_in(Page(LOGGED_IN, [])))

    def test_cookie_of_other_domain(self):
        c = [dict(CXT_COOKIES[0], domain=".notcxtvn.com")]
        self.assertFalse(X.is_logged_in(Page(LOGGED_IN, c)))

    def test_cookie_but_button_points_to_login(self):
        self.assertFalse(X.is_logged_in(Page(LOGGED_OUT, CXT_COOKIES)))

    def test_order_button_points_to_login(self):
        html = LOGGED_IN.replace(
            """<a href="javascript:;" class="btn-order" onclick="addToCart(4173, $('#sl').val(), 'checkout')">""",
            """<a href="dang-nhap.html" class="btn-order">""")
        self.assertFalse(X.is_logged_in(Page(html, CXT_COOKIES)))

    def test_errors_mean_not_logged_in(self):
        class Broken(Page):
            def content(self):
                raise RuntimeError("đang chuyển trang")
        self.assertFalse(X.is_logged_in(Broken(LOGGED_IN, CXT_COOKIES)))
        self.assertFalse(X.is_logged_in(object()))

    def test_generic_check_misses_it(self):
        # Đây là lỗi cũ: cách dò chung không thấy "Đăng xuất"/"Xin chào".
        self.assertFalse(S.is_logged_in(Page(LOGGED_IN, CXT_COOKIES)))


class WaitForLoginTest(unittest.TestCase):
    def test_uses_login_check(self):
        clock = FakeClock()
        msgs = []
        S.wait_for_login(Page(LOGGED_IN, CXT_COOKIES), timeout=10, poll=1,
                         sleep=clock.sleep, clock=clock, notify=msgs.append,
                         login_check=X.is_logged_in)
        self.assertEqual(msgs, [])

    def test_waits_until_cookie_appears(self):
        clock = FakeClock()
        page = Page(LOGGED_OUT, [])

        def sleep(s):
            clock.sleep(s)
            if clock.t >= 3:
                page.html = LOGGED_IN
                page.context._cookies = CXT_COOKIES
        msgs = []
        S.wait_for_login(page, timeout=10, poll=1, sleep=sleep, clock=clock,
                         notify=msgs.append, login_check=X.is_logged_in)
        self.assertEqual(msgs[-1], "Đã đăng nhập, tiếp tục.")

    def test_timeout_names_signs(self):
        clock = FakeClock()
        with self.assertRaises(S.LoginTimeout) as cm:
            S.wait_for_login(Page(LOGGED_OUT, []), timeout=3, poll=1,
                             sleep=clock.sleep, clock=clock, notify=None,
                             login_check=X.is_logged_in,
                             login_signs=X.LOGIN_SIGNS)
        msg = str(cm.exception)
        self.assertIn("mem_logged", msg)
        self.assertIn("dang-nhap", msg)

    def test_timeout_default_names_generic_signs(self):
        clock = FakeClock()
        with self.assertRaises(S.LoginTimeout) as cm:
            S.wait_for_login(Page(LOGGED_OUT, []), timeout=2, poll=1,
                             sleep=clock.sleep, clock=clock, notify=None)
        self.assertIn("đăng xuất", str(cm.exception))

    def test_login_check_exception_is_not_logged_in(self):
        clock = FakeClock()

        def boom(page):
            raise RuntimeError("x")
        with self.assertRaises(S.LoginTimeout):
            S.wait_for_login(Page(LOGGED_IN), timeout=2, poll=1,
                             sleep=clock.sleep, clock=clock, notify=None,
                             login_check=boom)


class LoginOptionsTest(unittest.TestCase):
    def test_cxt_adapter_provides_check(self):
        opts = S.login_options_for("https://linhkien.cxtvn.com")
        self.assertIs(opts["login_check"], X.is_logged_in)
        self.assertEqual(opts["login_signs"], X.LOGIN_SIGNS)

    def test_other_shop_uses_generic(self):
        self.assertEqual(S.login_options_for("https://hshop.vn"), {})
        self.assertEqual(S.login_options_for(""), {})

    def test_open_shop_passes_login_check(self):
        page = Page(LOGGED_IN, CXT_COOKIES)
        page.goto = lambda url, wait_until=None: None

        class Sess(S.BrowserSession):
            def new_page(self):
                return page
        got = Sess().open_shop("https://linhkien.cxtvn.com/", timeout=1, poll=1,
                               notify=None, sleep=lambda s: None,
                               **S.login_options_for("https://linhkien.cxtvn.com"))
        self.assertIs(got, page)


if __name__ == "__main__":
    unittest.main()
