"""Kiểm thử module trình duyệt bằng đối tượng giả, không cần cài Playwright."""

from __future__ import annotations

import builtins
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder.browser import session as S                           # noqa: E402


class FakePage:
    def __init__(self, states, url="about:blank"):
        # states: danh sách (selectors có mặt, chữ trên trang) theo từng lần dò
        self.states = list(states)
        self.url = url
        self.visited = []

    def _state(self):
        return self.states[0] if len(self.states) == 1 else self.states.pop(0)

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url

    def query_selector(self, sel):
        return object() if sel in self.states[0][0] else None

    def inner_text(self, sel):
        return self._state()[1]


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    def new_page(self):
        p = FakePage([((), "")])
        self.pages.append(p)
        return p

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, context):
        self.context = context
        self.launch_kwargs = None
        self.exited = False
        self.chromium = self

    def launch_persistent_context(self, **kw):
        self.launch_kwargs = kw
        return self.context

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.exited = True


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestDetect(unittest.TestCase):
    def test_nhan_ra_nut_dang_xuat_bang_selector(self):
        page = FakePage([(("a[href*='logout']",), "")])
        self.assertTrue(S.is_logged_in(page))

    def test_nhan_ra_chu_tren_trang(self):
        page = FakePage([((), "Xin chào, Nguyễn Văn A | ĐĂNG XUẤT")])
        self.assertTrue(S.is_logged_in(page))

    def test_chua_dang_nhap(self):
        page = FakePage([((), "Đăng nhập | Đăng ký")])
        self.assertFalse(S.is_logged_in(page))

    def test_loi_khi_do_coi_nhu_chua(self):
        page = mock.Mock()
        page.query_selector.side_effect = RuntimeError("navigating")
        page.inner_text.side_effect = RuntimeError("closed")
        self.assertFalse(S.is_logged_in(page))


class TestWait(unittest.TestCase):
    def test_cho_toi_khi_dang_nhap(self):
        clock = FakeClock()
        page = FakePage([((), "Đăng nhập")] * 3 + [((), "Đăng xuất")])
        S.wait_for_login(page, timeout=60, poll=1, sleep=clock.sleep,
                         clock=clock, notify=None)
        self.assertLess(clock.t, 60)

    def test_het_gio(self):
        clock = FakeClock()
        page = FakePage([((), "Đăng nhập")])
        with self.assertRaises(S.LoginTimeout):
            S.wait_for_login(page, timeout=10, poll=2, sleep=clock.sleep,
                             clock=clock, notify=None)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = Path(self.tmp.name) / "profile"

    def test_mo_chrome_persistent_va_hien_cua_so(self):
        page = FakePage([((), "Đăng xuất")])
        ctx = FakeContext(page)
        pw = FakePlaywright(ctx)
        with S.BrowserSession(self.profile, playwright_factory=lambda: pw) as sess:
            got = sess.open_shop("https://shop.example", notify=None)
        self.assertIs(got, page)
        self.assertEqual(page.visited, ["https://shop.example"])
        self.assertEqual(pw.launch_kwargs["user_data_dir"], str(self.profile))
        self.assertFalse(pw.launch_kwargs["headless"])
        self.assertEqual(pw.launch_kwargs["channel"], "chrome")
        self.assertTrue(self.profile.is_dir())
        self.assertTrue(ctx.closed)
        self.assertTrue(pw.exited)

    def test_open_shop_cho_dang_nhap(self):
        clock = FakeClock()
        page = FakePage([((), "Đăng nhập")] * 2 + [((), "Tài khoản của tôi")])
        pw = FakePlaywright(FakeContext(page))
        sess = S.BrowserSession(self.profile, playwright_factory=lambda: pw)
        got = S.open_shop("https://shop.example", session=sess, timeout=30,
                          poll=1, sleep=clock.sleep, clock=clock, notify=None)
        self.assertIs(got, page)
        sess.close()

    def test_profile_mac_dinh(self):
        p = S.default_profile_dir()
        self.assertEqual(p.parts[-2:], ("data", "browser_profile"))

    def test_bao_loi_ro_khi_chua_cai_playwright(self):
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name.startswith("playwright"):
                raise ImportError("No module named 'playwright'")
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", fake_import):
            with self.assertRaises(S.BrowserUnavailable) as cm:
                S.BrowserSession(self.profile).start()
        self.assertIn("pip install playwright", str(cm.exception))

    def test_loi_mo_trinh_duyet_duoc_boc_lai(self):
        pw = FakePlaywright(None)

        def boom(**kw):
            raise RuntimeError("Chromium distribution 'chrome' is not found")

        pw.launch_persistent_context = boom
        with self.assertRaises(S.BrowserUnavailable):
            S.BrowserSession(self.profile, playwright_factory=lambda: pw).start()
        self.assertTrue(pw.exited)


if __name__ == "__main__":
    unittest.main()
