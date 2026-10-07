"""Kiểm thử bộ xử lý riêng cho CXT (cxtvn.com) bằng HTML mẫu, không cần mạng."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lkorder.bom import parse_line                                  # noqa: E402
from lkorder.browser import cart as C                               # noqa: E402
from lkorder.browser.matcher import MATCH, NONE, UNCERTAIN, parse_html  # noqa: E402
from lkorder.browser.shops import cxtvn as X                        # noqa: E402
from lkorder.browser.shops import filler_class_for                  # noqa: E402

SEARCH_HTML = (HERE / "data" / "cxtvn_search.html").read_text(encoding="utf-8")
PRODUCT_HTML = (HERE / "data" / "cxtvn_product.html").read_text(encoding="utf-8")
SITE = "https://linhkien.cxtvn.com/"
SEARCH_PREFIX = "https://linhkien.cxtvn.com/tim-kiem.html?q="


def product_page(title: str, body: str = "", extra: str = "",
                 add_href: str = "javascript:void(0)") -> str:
    return f"""<html><body>
<div class="header"><span class="slcart">{{count}}</span>
<a href="dang-xuat.html">Đăng xuất</a></div>
<h1>{title}</h1><div class="mota">{body}</div>
<table><tr class="pricelist_head"><td>1+</td><td>10+</td></tr>
<tr><td>9.000₫</td><td>8.000₫</td></tr></table>
<input type="hidden" id="min_buy" value="1">
{extra}
<input id="sl" value="1">
<a href="{add_href}" class="addCart" onclick="addToCart(1)">CHO VÀO GIỎ</a>
<a href="javascript:void(0)" class="addCart" onclick="addToCart(1,'checkout')">ĐẶT HÀNG</a>
</body></html>"""


RANGE_PAGE = product_page(
    "Điện trở 1/4W thông số từ 1K đến 99K (10c)",
    extra="""<select name="kthuoc" id="kthuoc">
  <option value="">-- Chọn thông số --</option>
  <option value="11">1K</option><option value="12">10K</option>
  <option value="13">47K</option><option value="14">4K7</option>
</select>""")
HK_5V = product_page("Relay HK4100F", "Relay HK4100F-DC5V, cuộn hút 5VDC, tiếp điểm 3A 250VAC")
HK_12V = product_page("Relay HK4100F", "Relay HK4100F-DC12V, cuộn hút 12VDC, tiếp điểm 3A 250VAC")


# ------------------------------------------------------------ trang giả


class FakeHandle:
    def __init__(self, page, text="", attrs=None, on_click=None):
        self.page, self.text, self.attrs = page, text, attrs or {}
        self.on_click = on_click

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def is_visible(self):
        return True

    def fill(self, v):
        self.page.filled.append(v)

    def click(self):
        self.page.clicked.append(self.text)
        if self.on_click:
            self.on_click(self)


class FakePage:
    """Trang giả: `sites` url -> html; tìm kiếm CXT luôn trả `search`."""

    def __init__(self, sites, search=SEARCH_HTML, confirm="count"):
        self.sites, self.search = dict(sites), search
        self.url, self.html = "about:blank", ""
        self.visited, self.filled, self.clicked, self.selected = [], [], [], []
        self.cart = 0
        self.confirm = confirm          # count / button / none

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url
        if url.startswith(SEARCH_PREFIX):
            self.tpl = self.search
        else:
            self.tpl = self.sites.get(url, "<html><body>404</body></html>")
        self.html = self.tpl.replace("{count}", str(self.cart))

    def content(self):
        return self.html

    def _added(self, handle):
        if self.confirm == "count":
            self.cart += 1
            self.html = self.tpl.replace("{count}", str(self.cart))
        elif self.confirm == "button":
            self.html = self.html.replace(">CHO VÀO GIỎ<", ">ĐÃ THÊM<")
            handle.text = "ĐÃ THÊM"

    def query_selector_all(self, sel):
        if sel != ".addCart":
            return []
        out = []
        for n in parse_html(self.html).iter():
            if "addcart" in n.classes:
                out.append(FakeHandle(self, n.text(), dict(n.attrs),
                                      self._added))
        return out

    def query_selector(self, sel):
        if sel in ("#sl", "input#sl") and 'id="sl"' in self.html:
            return FakeHandle(self)
        return None

    def select_option(self, sel, value):
        self.selected.append((sel, value))


def P(path):
    return SITE + path


def make(page, base="https://cxtvn.com", **kw):
    sleeps, clock = [], [0.0]

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    f = C.CartFiller(page, base, sleep=sleep, rand=lambda a, b: a,
                     clock=lambda: clock[0], confirm_timeout=1.0, poll=0.5, **kw)
    return f, sleeps


# ------------------------------------------------------------ nhận diện


class TestChonAdapter(unittest.TestCase):
    def test_is_cxt(self):
        for u in ("https://cxtvn.com", "linhkien.cxtvn.com",
                  "https://www.cxtvn.com/abc", "http://linhkien.cxtvn.com/x.html"):
            self.assertTrue(X.is_cxt_url(u), u)
        for u in ("https://cxtvn.com.vn", "https://notcxtvn.com", "https://hshop.vn"):
            self.assertFalse(X.is_cxt_url(u), u)

    def test_cartfiller_tu_chon(self):
        page = FakePage({})
        self.assertIsInstance(C.CartFiller(page, "https://linhkien.cxtvn.com"),
                              X.CxtCartFiller)
        f = C.CartFiller(page, "https://hshop.vn", platform="haravan")
        self.assertIs(type(f), C.CartFiller)
        self.assertIs(filler_class_for("https://cxtvn.com"), X.CxtCartFiller)
        self.assertIsNone(filler_class_for("https://hshop.vn"))

    def test_search_url(self):
        f, _ = make(FakePage({}))
        self.assertEqual(f.search_page_url("điện trở 10k"),
                         SEARCH_PREFIX + "%C4%91i%E1%BB%87n+tr%E1%BB%9F+10k")

    def test_shops_json_co_cxt(self):
        from lkorder import config
        shops = config.load_shops(HERE.parent / "data" / "shops.json")
        self.assertIn("cxt", shops)
        self.assertTrue(X.is_cxt_url(shops["cxt"].url))


# ------------------------------------------------------------ tìm kiếm


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.priced, self.zero = X.parse_cxt_search(SEARCH_HTML, X.SEARCH_URL)

    def test_doc_item(self):
        self.assertEqual(len(self.priced), 9)
        first = self.priced[0]
        self.assertEqual(first.title, "Điện trở dán 10K 0603 (50c)")
        self.assertEqual(first.pid, "10231")
        self.assertEqual(first.url, P("10231-dien-tro-dan-10k-0603.html"))
        self.assertEqual(first.price, 2500)
        self.assertEqual(first.pack, 50)

    def test_quy_cach_goi(self):
        packs = {c.pid: c.pack for c in self.priced}
        self.assertEqual(packs["10240"], 10)
        self.assertEqual(packs["20001"], 1)
        self.assertEqual(X.detect_cxt_pack("Tụ 100nF 0603 (100c)"), 100)

    def test_bo_gia_0(self):
        self.assertEqual([c.title for c in self.zero], ["IC LM2596S-ADJ TO-263"])
        self.assertNotIn("50001", {c.pid for c in self.priced})

    def test_gom_nhieu_gia_tri(self):
        multi = [c.pid for c in self.priced if c.multi]
        self.assertEqual(multi, ["10240"])


# ------------------------------------------------------------ khớp


class TestMatch(unittest.TestCase):
    def setUp(self):
        self.priced, self.zero = X.parse_cxt_search(SEARCH_HTML, X.SEARCH_URL)

    def pick(self, raw):
        priced, zero = X.parse_cxt_search(SEARCH_HTML, X.SEARCH_URL)
        return X.cxt_pick(parse_line(raw), priced, zero)

    def score_of(self, raw, title):
        c = X.CxtCandidate(title=title, url="u", multi=X.is_multi_value(title))
        return X.cxt_score(parse_line(raw), c).score

    def test_dung_kieu_chan_0603(self):
        dec, group = self.pick("Điện trở 10K 0603 x100")
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.pid, "10231")
        self.assertEqual(len(group), 1)

    def test_sai_kieu_chan_phat_nang(self):
        self.assertLess(self.score_of("Điện trở 10K 0603", "Điện trở dán 10K 0805 (50c)"),
                        0.4)
        self.assertLess(self.score_of("1N4148 SOD-323", "Diode 1N4148 SOD-123 (10c)"),
                        0.4)
        self.assertLess(self.score_of("SS34 SMB", "Diode SS34 SMA"), 0.4)
        self.assertGreaterEqual(self.score_of("SS34 SMA", "Diode SS34 SMA"), 0.95)
        dec, _ = self.pick("1N4148 SOD-323 x10")
        self.assertNotEqual(dec.status, MATCH)

    def test_ic_khong_lay_module(self):
        self.assertLess(self.score_of("AMS1117-3.3", "Module AMS1117-3.3V"), 0.4)
        self.assertLess(self.score_of("IC NE555", "Kit mạch NE555 tạo xung"), 0.4)
        self.assertLess(self.score_of("NE555", "Mạch NE555"), 0.4)
        dec, _ = self.pick("AMS1117-3.3 x3")
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.pid, "30002")
        # BOM ghi rõ là module thì không phạt
        self.assertGreaterEqual(self.score_of("Module AMS1117-3.3V",
                                              "Module AMS1117-3.3V"), 0.8)

    def test_lech_dien_ap(self):
        self.assertLess(self.score_of("HK4100F 5V", "Relay HK4100F-12VDC"), 0.6)
        self.assertGreaterEqual(self.score_of("HK4100F 5V", "Relay HK4100F 5VDC"), 0.8)

    def test_khoang_gia_tri(self):
        dec, group = self.pick("Điện trở 47K 1/4W x20")
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.pid, "10240")
        self.assertTrue(dec.best.multi)
        # ngoài khoảng 1K..99K
        self.assertEqual(self.score_of("Điện trở 220K",
                                       "Điện trở 1/4W thông số từ 1K đến 99K"), 0.0)

    def test_trung_ten_gom_nhom(self):
        dec, group = self.pick("Relay HK4100F 5V x2")
        self.assertEqual(dec.status, MATCH)
        self.assertEqual({c.pid for c in group}, {"40001", "40002"})

    def test_chi_co_gia_0(self):
        dec, _ = self.pick("LM2596S-ADJ x1")
        self.assertEqual(dec.status, UNCERTAIN)
        self.assertIn("0₫", dec.message)
        self.assertIn("kiểm tra", dec.message)

    def test_khong_co(self):
        dec, _ = self.pick("STM32F103C8T6 x1")
        self.assertEqual(dec.status, NONE)

    def test_spec_values(self):
        v = X.spec_values("Relay HK4100F-DC5V 1/4W 4k7 100nF 16MHz 250VAC 1N4148")
        self.assertEqual(v["v"], [5.0, 250.0])
        self.assertEqual(v["ohm"], [4700.0])
        self.assertNotIn("w", v)
        self.assertAlmostEqual(v["f"][0], 1e-7)


# ------------------------------------------------------------ trang sản phẩm


class TestProductPage(unittest.TestCase):
    def test_parse(self):
        info = X.parse_cxt_product(PRODUCT_HTML.replace("{count}", "3"))
        self.assertEqual(info.title, "Điện trở dán 10K 0603 (50c)")
        self.assertEqual(info.tiers, [(1, 2500), (10, 2200), (50, 1900)])
        self.assertEqual(info.min_buy, 2)
        self.assertTrue(info.has_sl)
        self.assertTrue(info.logged_in)
        self.assertEqual(info.cart_count, 3)
        self.assertEqual(info.price_for(1), 2500)
        self.assertEqual(info.price_for(12), 2200)
        self.assertEqual(info.price_for(60), 1900)

    def test_bang_gia_ngang(self):
        info = X.parse_cxt_product(HK_5V.replace("{count}", "0"))
        self.assertEqual(info.tiers, [(1, 9000), (10, 8000)])

    def test_chua_dang_nhap(self):
        html = PRODUCT_HTML.replace('href="javascript:void(0)" class="addCart" '
                                    'onclick="addToCart(10231)"',
                                    'href="https://linhkien.cxtvn.com/dang-nhap.html" '
                                    'class="addCart"')
        self.assertFalse(X.parse_cxt_product(html).logged_in)

    def test_bien_the_select(self):
        info = X.parse_cxt_product(RANGE_PAGE)
        self.assertEqual(info.variant_kind, {"kthuoc": "select"})
        self.assertEqual([v for v, _ in info.variants["kthuoc"]], ["11", "12", "13", "14"])
        v, _ = X.choose_variant(parse_line("Điện trở 4.7K"), info.variants)
        self.assertEqual(v, ("kthuoc", "14", "4K7"))
        v, why = X.choose_variant(parse_line("Điện trở 22K"), info.variants)
        self.assertIsNone(v)

    def test_bien_the_radio_mau(self):
        html = """<h1>LED 5mm</h1>
          <label><input type="radio" name="color" value="1"> Đỏ</label>
          <label><input type="radio" name="color" value="2"> Xanh lá</label>"""
        info = X.parse_cxt_product(html)
        self.assertEqual(info.variant_kind, {"color": "radio"})
        v, _ = X.choose_variant(parse_line("LED 5mm đỏ"), info.variants)
        self.assertEqual(v, ("color", "1", "Đỏ"))
        v, why = X.choose_variant(parse_line("LED 5mm"), info.variants)
        self.assertIsNone(v)


# ------------------------------------------------------------ luồng thêm giỏ


class TestFlow(unittest.TestCase):
    def sites(self):
        return {
            P("10231-dien-tro-dan-10k-0603.html"): PRODUCT_HTML,
            P("10240-dien-tro-1-4w-thong-so-tu-1k-den-99k.html"): RANGE_PAGE,
            P("40001-relay-hk4100f.html"): HK_12V,
            P("40002-relay-hk4100f.html"): HK_5V,
        }

    def test_them_gio_xac_nhan_slcart(self):
        page = FakePage(self.sites())
        f, _ = make(page)
        res = f.add_line(parse_line("Điện trở 10K 0603 x30"))
        self.assertEqual(res.status, C.ADDED, res.message)
        # 30 con / gói 50 = 1 gói, nhưng #min_buy = 2
        self.assertEqual(res.units, 2)
        self.assertEqual(res.pieces, 100)
        self.assertEqual(page.filled, ["2"])
        self.assertEqual(page.clicked, ["CHO VÀO GIỎ"])
        self.assertIn("giỏ tăng 0 -> 1", res.message)
        self.assertEqual(res.product.price, 2500)

    def test_xac_nhan_nut_da_them(self):
        page = FakePage(self.sites(), confirm="button")
        f, _ = make(page)
        res = f.add_line(parse_line("Điện trở 10K 0603 x500"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.units, 10)
        self.assertIn("ĐÃ THÊM", res.message)
        self.assertEqual(res.product.price, 2200)

    def test_khong_xac_nhan_duoc(self):
        page = FakePage(self.sites(), confirm="none")
        f, _ = make(page)
        res = f.add_line(parse_line("Điện trở 10K 0603 x30"))
        self.assertEqual(res.status, C.UNCERTAIN)

    def test_khong_bam_dat_hang(self):
        # Nút ĐẶT HÀNG đứng trước, và nút "CHO VÀO GIỎ" giả gọi addToCart checkout.
        html = PRODUCT_HTML.replace(
            '<a href="javascript:void(0)" class="addCart" onclick="addToCart(10231)">'
            'CHO VÀO GIỎ</a>',
            '<a href="javascript:void(0)" class="addCart" '
            'onclick="addToCart(10231,\'checkout\')">CHO VÀO GIỎ</a>')
        sites = self.sites()
        sites[P("10231-dien-tro-dan-10k-0603.html")] = html
        page = FakePage(sites)
        f, _ = make(page)
        res = f.add_line(parse_line("Điện trở 10K 0603 x30"))
        self.assertEqual(res.status, C.ERROR)
        self.assertEqual(page.clicked, [])

    def test_click_guard(self):
        f, _ = make(FakePage({}))
        for text, attrs in (("ĐẶT HÀNG", {}), ("MUA NGAY", {}),
                            ("CHO VÀO GIỎ", {"onclick": "addToCart(1,'checkout')"})):
            with self.assertRaises(C.CheckoutRefused):
                f._click_add(FakeHandle(f.page, text, attrs))

    def test_chua_dang_nhap_dung_ca_luot(self):
        html = PRODUCT_HTML.replace(
            'href="javascript:void(0)" class="addCart" onclick="addToCart(10231)"',
            'href="https://linhkien.cxtvn.com/dang-nhap.html" class="addCart"')
        sites = self.sites()
        sites[P("10231-dien-tro-dan-10k-0603.html")] = html
        page = FakePage(sites)
        f, _ = make(page)
        out = f.run([parse_line("Điện trở 10K 0603 x30"), parse_line("SS34 SMA x5")])
        self.assertEqual([r.status for r in out], [C.ERROR, C.ERROR])
        self.assertIn("đăng nhập", out[0].message)
        self.assertEqual(page.clicked, [])
        self.assertFalse(any("20001" in u for u in page.visited))

    def test_chon_bien_the_kthuoc(self):
        page = FakePage(self.sites())
        f, _ = make(page)
        res = f.add_line(parse_line("Điện trở 47K 1/4W x25"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(page.selected[-1][1], "13")
        self.assertEqual(res.units, 3)              # gói 10 con
        self.assertIn("kthuoc=47K", res.message)

    def test_khoang_khong_co_bien_the_khop(self):
        page = FakePage(self.sites())
        f, _ = make(page)
        res = f.add_line(parse_line("Điện trở 33K 1/4W x10"))
        self.assertEqual(res.status, C.UNCERTAIN)
        self.assertEqual(page.clicked, [])

    def test_trung_ten_phan_biet_dien_ap(self):
        page = FakePage(self.sites())
        f, sleeps = make(page)
        res = f.add_line(parse_line("Relay HK4100F 5V x2"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.product.pid, "40002")
        self.assertIn(2.0, sleeps)                   # nghỉ giữa hai trang sản phẩm

    def test_trung_ten_khong_chac(self):
        page = FakePage(self.sites())
        f, _ = make(page)
        res = f.add_line(parse_line("Relay HK4100F x2"))
        self.assertEqual(res.status, C.UNCERTAIN)
        self.assertIn("trùng tên", res.message)
        self.assertEqual(page.clicked, [])

    def test_nghi_2_den_5_giay(self):
        page = FakePage(self.sites())
        f, sleeps = make(page, delay=(0.0, 0.5))
        self.assertEqual(f.delay, (2.0, 2.0))
        f2, _ = make(page)
        self.assertEqual(f2.delay, (2.0, 5.0))
        page = FakePage(self.sites())
        f, sleeps = make(page)
        f.run([parse_line("Điện trở 10K 0603 x30"), parse_line("SS34 SMA x5")])
        self.assertGreaterEqual(min(s for s in sleeps if s >= 1), 2.0)
        self.assertIn(2.0, sleeps)

    def test_dry_run_cli(self):
        from lkorder import cli
        page = FakePage(self.sites())
        out = cli.run_cart(page, [parse_line("Điện trở 10K 0603 x30")],
                           "https://cxtvn.com", dry_run=True, notify=None,
                           sleep=lambda s: None, rand=lambda a, b: a)
        self.assertEqual(out[0].product.pid, "10231")
        self.assertEqual(page.clicked, [])


if __name__ == "__main__":
    unittest.main()
