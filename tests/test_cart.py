"""Kiểm thử tìm linh kiện trên shop và bỏ vào giỏ.

Phần bóc tách/chấm điểm test bằng HTML mẫu; phần thao tác test bằng trang giả
(không cần Playwright, không cần mạng).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder.bom import parse_line                                  # noqa: E402
from lkorder.browser import cart as C                               # noqa: E402
from lkorder.browser import matcher as M                            # noqa: E402
from lkorder.models import BomLine                                  # noqa: E402

BASE = "https://shop.example"

# ------------------------------------------------------------ HTML mẫu

HARAVAN_SEARCH = """
<html><body>
<header><a href="/cart" class="header-cart"><span class="cart-count">0</span></a></header>
<div class="search-list">
  <div class="product-item">
    <a href="/products/dien-tro-100k-1-4w"><img alt="Điện trở 100K"></a>
    <h3 class="product-name"><a href="/products/dien-tro-100k-1-4w">Điện trở 100K 1/4W (gói 100 con)</a></h3>
    <div class="product-price"><span class="price">15.000₫</span></div>
  </div>
  <div class="product-item">
    <a href="/products/dien-tro-10k-1-4w"><img alt="Điện trở 10K"></a>
    <h3 class="product-name"><a href="/products/dien-tro-10k-1-4w">Điện trở 10K 1/4W 5% (gói 100 con)</a></h3>
    <div class="product-price"><span class="price">12.500₫</span>
      <del class="compare-price">20.000₫</del></div>
  </div>
  <div class="product-item sold-out">
    <h3 class="product-name"><a href="/products/dien-tro-1k">Điện trở 1K 1/4W</a></h3>
    <span class="price">10.000₫</span>
  </div>
</div>
</body></html>
"""

WOO_SEARCH = """
<ul class="products columns-4">
  <li class="product type-product instock">
    <a href="https://shop.example/san-pham/ne555p/" class="woocommerce-LoopProduct-link">
      <h2 class="woocommerce-loop-product__title">IC NE555P DIP-8</h2>
      <span class="price"><del><span>5.000&nbsp;₫</span></del>
        <ins><span>3.500&nbsp;₫</span></ins></span>
    </a>
    <a href="?add-to-cart=12" class="button add_to_cart_button">Thêm vào giỏ</a>
  </li>
  <li class="product type-product outofstock">
    <a href="https://shop.example/san-pham/lm358/">
      <h2 class="woocommerce-loop-product__title">IC LM358 DIP-8</h2>
      <span class="price">4.000 ₫</span></a>
  </li>
</ul>
"""

JSONLD_SEARCH = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":"ListItem","position":1,"item":{"@type":"Product",
   "name":"Vi điều khiển STM32F103C8T6 LQFP-48","url":"/p/stm32f103c8t6",
   "offers":{"@type":"Offer","price":"45000","availability":"https://schema.org/InStock"}}},
 {"@type":"ListItem","position":2,"item":{"@type":"Product",
   "name":"Kit STM32F103C8T6 Blue Pill","url":"/p/bluepill",
   "offers":{"@type":"Offer","price":"85000"}}}
]}
</script></head><body><div>Kết quả</div></body></html>
"""

PRODUCT_PAGE = """
<html><body>
<header><a href="/cart"><span class="cart-count">{count}</span></a></header>
<h1>Điện trở 10K 1/4W 5% (gói 100 con)</h1>
<p>Còn hàng. Số lượng tối thiểu: 2</p>
<form action="/cart/add">
  <input type="number" name="quantity" value="1" min="1" step="1">
  <button name="add" class="add-to-cart">Thêm vào giỏ</button>
  <button class="buy-now">Mua ngay</button>
</form>
</body></html>
"""


# ------------------------------------------------------------ bóc tách


class TestParseSearch(unittest.TestCase):
    def test_haravan_cards(self):
        c = M.parse_search_results(HARAVAN_SEARCH, BASE + "/search?q=x")
        self.assertEqual(len(c), 3)
        self.assertEqual(c[1].title, "Điện trở 10K 1/4W 5% (gói 100 con)")
        self.assertEqual(c[1].url, BASE + "/products/dien-tro-10k-1-4w")
        self.assertEqual(c[1].price, 12_500)          # không lấy giá cũ
        self.assertEqual(c[1].pack, 100)
        self.assertIs(c[2].in_stock, False)

    def test_woo_cards(self):
        c = M.parse_search_results(WOO_SEARCH, BASE)
        self.assertEqual([x.title for x in c], ["IC NE555P DIP-8", "IC LM358 DIP-8"])
        self.assertEqual(c[0].price, 3_500)           # giá khuyến mãi <ins>
        self.assertEqual(c[0].url, BASE + "/san-pham/ne555p/")
        self.assertIs(c[1].in_stock, False)

    def test_jsonld(self):
        c = M.parse_search_results(JSONLD_SEARCH, BASE + "/search?q=stm32")
        self.assertEqual(len(c), 2)
        self.assertEqual(c[0].url, BASE + "/p/stm32f103c8t6")
        self.assertEqual(c[0].price, 45_000)
        self.assertIs(c[0].in_stock, True)

    def test_link_fallback(self):
        html = """<nav><a href="/products/menu">Menu sản phẩm</a></nav>
                  <div><a href="/products/ams1117-3-3">AMS1117-3.3 SOT-223</a></div>"""
        c = M.parse_search_results(html, BASE)
        self.assertEqual([x.url for x in c], [BASE + "/products/ams1117-3-3"])

    def test_empty(self):
        self.assertEqual(M.parse_search_results("<p>Không tìm thấy</p>", BASE), [])

    def test_price_and_pack(self):
        self.assertEqual(M.parse_price("1,250,000 đ"), 1_250_000)
        self.assertEqual(M.parse_price("Liên hệ"), 0)
        self.assertEqual(M.detect_pack("Tụ 100nF túi 50c"), 50)
        self.assertEqual(M.detect_pack("Điện trở 10K (100 cái)"), 100)
        self.assertEqual(M.detect_pack("NE555 DIP-8"), 1)


# ------------------------------------------------------------ chấm điểm


class TestScore(unittest.TestCase):
    def s(self, q, t):
        return M.score_title(q, t)[0]

    def test_sai_gia_tri_bi_loai(self):
        self.assertEqual(self.s("Điện trở 10K", "Điện trở 100K 1/4W"), 0.0)
        self.assertEqual(self.s("Điện trở 100K", "Điện trở 10K 1/4W"), 0.0)
        self.assertEqual(self.s("Tụ gốm 100nF", "Tụ gốm 10nF 50V"), 0.0)

    def test_dung_gia_tri_khac_cach_viet(self):
        self.assertGreaterEqual(self.s("Tụ gốm 100nF", "Tụ 0.1uF 50V"), M.ACCEPT_SCORE)
        self.assertGreaterEqual(self.s("Điện trở 4K7", "Trở 4.7K 1/4W"), M.ACCEPT_SCORE)

    def test_lech_cong_suat_bi_tru(self):
        self.assertLess(self.s("Điện trở 10K 1/4W", "Điện trở 10K 1W"), M.ACCEPT_SCORE)

    def test_bo_kit_nhieu_gia_tri(self):
        self.assertLess(self.s("Điện trở 10K", "Bộ điện trở 1K 10K 100K"),
                        M.ACCEPT_SCORE)

    def test_khac_loai(self):
        self.assertEqual(self.s("Điện trở 10K", "Biến trở 10K"), 0.0)

    def test_mpn(self):
        self.assertEqual(self.s("STM32F103C8T6", "Vi điều khiển STM32F103C8T6 LQFP-48"), 1.0)
        self.assertGreaterEqual(self.s("NE555", "IC NE555P DIP-8"), M.ACCEPT_SCORE)
        self.assertEqual(self.s("LM35", "IC LM358 DIP-8"), 0.0)   # khác con
        self.assertGreaterEqual(self.s("ESP32-WROOM-32", "Module ESP32 WROOM-32"),
                                M.ACCEPT_SCORE)
        self.assertLess(self.s("STM32F103C8T6", "STM32F103 các loại"), M.ACCEPT_SCORE)

    def test_lech_chan(self):
        self.assertLess(self.s("NE555 DIP-8", "NE555 SOP-8"), M.ACCEPT_SCORE)

    def test_mo_ta_chung(self):
        self.assertGreaterEqual(self.s("LED 5mm đỏ", "LED 5mm đỏ siêu sáng"),
                                M.ACCEPT_SCORE)
        self.assertLess(self.s("LED 5mm đỏ", "LED 5mm xanh lá"), M.ACCEPT_SCORE)


class TestPickBest(unittest.TestCase):
    def test_chon_dung_10k_khong_nham_100k(self):
        line = parse_line("Điện trở 10K 1/4W x100")
        cands = M.parse_search_results(HARAVAN_SEARCH, BASE)
        d = M.pick_best(line, cands)
        self.assertEqual(d.status, M.MATCH)
        self.assertIn("10k-1-4w", d.best.url)

    def test_khong_co_gi(self):
        line = parse_line("Điện trở 47K")
        d = M.pick_best(line, M.parse_search_results(HARAVAN_SEARCH, BASE))
        self.assertEqual(d.status, M.NONE)

    def test_het_hang(self):
        line = parse_line("Điện trở 1K 1/4W")
        d = M.pick_best(line, M.parse_search_results(HARAVAN_SEARCH, BASE))
        self.assertEqual(d.status, M.NONE)
        self.assertIn("hết hàng", d.message)

    def test_chua_chac_kem_ung_vien(self):
        line = parse_line("STM32F103C8T6")
        cands = [M.Candidate("STM32F103 các loại", BASE + "/a"),
                 M.Candidate("Module STM32 mini", BASE + "/b")]
        d = M.pick_best(line, cands)
        self.assertEqual(d.status, M.UNCERTAIN)
        self.assertTrue(d.shortlist)

    def test_hai_ung_vien_ngang_nhau(self):
        line = parse_line("NE555")
        cands = [M.Candidate("IC NE555P", BASE + "/a"),
                 M.Candidate("IC NE555N", BASE + "/b")]
        self.assertEqual(M.pick_best(line, cands).status, M.UNCERTAIN)

    def test_alt_keys(self):
        line = BomLine(key="ams1117-3.3", qty=1, raw="AMS1117-3.3",
                       alt_keys=("lm1117",))
        d = M.pick_best(line, [M.Candidate("IC ổn áp LM1117 SOT-223", BASE + "/x")])
        self.assertEqual(d.status, M.MATCH)

    def test_search_queries(self):
        self.assertEqual(M.search_queries(parse_line("Tụ gốm 100nF x10"))[0], "tụ 100nF")
        self.assertEqual(M.search_queries(parse_line("stm32f103c8t6 x2")),
                         ["STM32F103C8T6"])


# ------------------------------------------------------------ trang sản phẩm


class TestProductPage(unittest.TestCase):
    def test_parse(self):
        info = C.parse_product_page(PRODUCT_PAGE.format(count=0))
        self.assertTrue(info.has_qty_input)
        self.assertEqual(info.moq, 2)
        self.assertEqual(info.pack, 100)
        self.assertIs(info.in_stock, True)

    def test_min_step_attr(self):
        html = '<h1>Tụ 100nF</h1><input name="qty" min="10" step="10">'
        info = C.parse_product_page(html)
        self.assertEqual((info.moq, info.step), (10, 10))

    def test_units(self):
        self.assertEqual(C.units_to_order(100, pack=100), 1)
        self.assertEqual(C.units_to_order(150, pack=100), 2)
        self.assertEqual(C.units_to_order(3, moq=5), 5)
        self.assertEqual(C.units_to_order(12, step=10), 20)
        self.assertEqual(C.units_to_order(1, pack=100, moq=2), 2)

    def test_cart_count(self):
        self.assertEqual(C.read_cart_count('<span class="cart-count">3</span>'), 3)
        self.assertEqual(C.read_cart_count('<div data-cart-count="7"></div>'), 7)
        self.assertEqual(C.read_cart_count(
            '<a href="/gio-hang"><i></i><span>4</span></a>'), 4)
        self.assertIsNone(C.read_cart_count("<p>hello</p>"))

    def test_checkout_guard(self):
        self.assertTrue(C.is_checkout_like("Thanh toán"))
        self.assertTrue(C.is_checkout_like("MUA NGAY"))
        self.assertTrue(C.is_checkout_like("", "https://shop/checkout"))
        self.assertFalse(C.is_checkout_like("Thêm vào giỏ hàng"))


# ------------------------------------------------------------ trang giả


class FakeHandle:
    def __init__(self, page, text="", on_click=None, attrs=None):
        self.page, self.text, self.on_click = page, text, on_click
        self.attrs = attrs or {}
        self.value = None
        self.clicks = 0

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def is_visible(self):
        return True

    def fill(self, v):
        self.value = v
        self.page.filled.append(v)

    def press(self, key):
        pass

    def click(self):
        self.clicks += 1
        self.page.clicked.append(self.text)
        if self.on_click:
            self.on_click()


class FakePage:
    """`sites`: url -> html. `handles`: url -> {selector: FakeHandle}."""

    def __init__(self, sites, cart_grows=True, show_qty=True):
        self.sites = dict(sites)
        self.url = "about:blank"
        self.html = ""
        self.visited, self.filled, self.clicked = [], [], []
        self.cart = 0
        self.cart_grows = cart_grows
        self.show_qty = show_qty

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url
        self.html = self.sites.get(url, "<html><body>404</body></html>")
        if "{count}" in self.html:
            self.html = self.html.replace("{count}", str(self.cart))

    def content(self):
        return self.html

    def _add(self):
        if self.cart_grows:
            self.cart += 1
            self.html = PRODUCT_PAGE.format(count=self.cart)

    def query_selector(self, sel):
        if "product" not in self.url and "/p/" not in self.url:
            return None
        if sel == "input[name='quantity']" and self.show_qty:
            return FakeHandle(self)
        if sel == "button[name='add']":
            return FakeHandle(self, "Thêm vào giỏ", self._add)
        return None


def search_url(q):
    from urllib.parse import quote_plus
    return f"{BASE}/search?q={quote_plus(q)}"


def make_filler(page, **kw):
    sleeps = []
    clock = [0.0]

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    f = C.CartFiller(page, BASE, sleep=sleep, rand=lambda a, b: a,
                     clock=lambda: clock[0], delay=(2.0, 5.0),
                     confirm_timeout=1.0, poll=0.5, **kw)
    return f, sleeps


class TestCartFlow(unittest.TestCase):
    def sites(self):
        return {
            search_url("điện trở 10k"): HARAVAN_SEARCH,
            BASE + "/products/dien-tro-10k-1-4w": PRODUCT_PAGE,
        }

    def test_added(self):
        page = FakePage(self.sites())
        f, _ = make_filler(page)
        res = f.add_line(parse_line("Điện trở 10K 1/4W x150"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.units, 2)                # 150 con, gói 100 -> 2 gói
        self.assertEqual(res.pieces, 200)
        self.assertEqual(page.filled, ["2"])
        self.assertEqual(page.cart, 1)
        self.assertIn("giỏ tăng 0 -> 1", res.message)
        # Không bao giờ bấm mua ngay / thanh toán
        self.assertEqual(page.clicked, ["Thêm vào giỏ"])

    def test_moq_ap_dung(self):
        page = FakePage(self.sites())
        f, _ = make_filler(page)
        res = f.add_line(parse_line("Điện trở 10K 1/4W x10"))
        self.assertEqual(res.units, 2)                # cần 1 gói nhưng MOQ 2
        self.assertEqual(page.filled, ["2"])

    def test_gio_khong_tang(self):
        page = FakePage(self.sites(), cart_grows=False)
        f, _ = make_filler(page)
        res = f.add_line(parse_line("Điện trở 10K 1/4W x100"))
        self.assertEqual(res.status, C.UNCERTAIN)
        self.assertIn("không thấy giỏ tăng", res.message)

    def test_not_found(self):
        page = FakePage({})
        f, _ = make_filler(page)
        res = f.add_line(parse_line("Điện trở 47K"))
        self.assertEqual(res.status, C.NOT_FOUND)
        self.assertEqual(page.clicked, [])

    def test_uncertain_khong_bo_gio(self):
        sites = {search_url("NE555"): """
            <div class="product-item"><h3 class="product-name">
              <a href="/products/a">IC NE555P</a></h3></div>
            <div class="product-item"><h3 class="product-name">
              <a href="/products/b">IC NE555N</a></h3></div>"""}
        page = FakePage(sites)
        f, _ = make_filler(page)
        res = f.add_line(parse_line("NE555"))
        self.assertEqual(res.status, C.UNCERTAIN)
        self.assertEqual(len(res.candidates), 2)
        self.assertEqual(page.clicked, [])

    def test_error_khong_co_o_so_luong(self):
        page = FakePage(self.sites(), show_qty=False)
        f, _ = make_filler(page)
        res = f.add_line(parse_line("Điện trở 10K 1/4W x150"))
        self.assertEqual(res.status, C.ERROR)

    def test_error_ngoai_le(self):
        class Boom(FakePage):
            def goto(self, url, wait_until=None):
                raise RuntimeError("mất mạng")
        f, _ = make_filler(Boom({}))
        res = f.add_line(parse_line("NE555"))
        self.assertEqual(res.status, C.ERROR)
        self.assertIn("mất mạng", res.message)

    def test_tu_choi_nut_thanh_toan(self):
        class Evil(FakePage):
            def query_selector(self, sel):
                if sel == "button[name='add']" and "product" in self.url:
                    return FakeHandle(self, "Thanh toán ngay")
                return super().query_selector(sel) if sel != "button[name='add']" else None
        page = Evil(self.sites())
        f, _ = make_filler(page)
        res = f.add_line(parse_line("Điện trở 10K 1/4W x100"))
        self.assertEqual(res.status, C.ERROR)
        self.assertEqual(page.clicked, [])

    def test_run_nghi_giua_cac_dong(self):
        page = FakePage(self.sites())
        f, sleeps = make_filler(page)
        lines = [parse_line("Điện trở 10K 1/4W x100"), parse_line("Điện trở 47K"),
                 parse_line("Điện trở 10K x100")]
        res = f.run(lines)
        self.assertEqual([r.status for r in res], [C.ADDED, C.NOT_FOUND, C.ADDED])
        self.assertEqual(sleeps.count(2.0), 2)        # nghỉ 2 lần cho 3 dòng
        self.assertEqual(C.summarize(res)[C.ADDED], 2)
        self.assertFalse(any("checkout" in u or "thanh-toan" in u
                             for u in page.visited))


if __name__ == "__main__":
    unittest.main()
