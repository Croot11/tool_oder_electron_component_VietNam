"""CXT: sản phẩm chung tên ("Tụ 0603 không phân cực") — đọc tên thật ở trang
chi tiết, bộ nhớ đệm theo URL, quy cách gói theo dây, bước selectProduct().

Dùng HTML mẫu rút gọn từ trang thật 6736 (tests/data/cxtvn_generic_product.html).
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from lkorder.bom import parse_line                                  # noqa: E402
from lkorder.browser import cart as C                               # noqa: E402
from lkorder.browser.matcher import MATCH, UNCERTAIN, Candidate, parse_html  # noqa: E402
from lkorder.browser.shops import cxtvn as X                        # noqa: E402

GENERIC_HTML = (HERE / "data" / "cxtvn_generic_product.html").read_text(
    encoding="utf-8")
SITE = "https://linhkien.cxtvn.com/"
SEARCH_PREFIX = SITE + "tim-kiem.html?q="
REAL_TITLE = "0603B104K500NT Tụ 100nF 50V 10% 0603 (20c)"
URL_100N = SITE + "6736-tu-0603-khong-phan-cuc.html"


def P(path: str) -> str:
    return SITE + path


def generic_page(real: str = REAL_TITLE, pid: str = "6736",
                 image: str = "0603b104k500nt-tu-100nf-50v-10-0603-10c-img-1658908857.jpg",
                 siblings: bool = True) -> str:
    """Trang chi tiết tên chung cho sản phẩm khác (đổi tên thật/ảnh/id)."""
    html = GENERIC_HTML.replace(REAL_TITLE, real)
    html = html.replace("0603b104k500nt-tu-100nf-50v-10-0603-10c-img-1658908857.jpg",
                        image)
    if not siblings:
        start = html.index('<div id="sanphamcungloai"')
        end = html.index("</div>", start) + len("</div>")
        html = html[:start] + html[end:]
    return html


def ajax_page(html: str, pid: str = "6736") -> str:
    """Trang sau khi bấm link "Thông số": nút giỏ thật (đã đăng nhập)."""
    return (html
            .replace('<a href="javascript:;" onclick="selectProduct(); return false;">'
                     'CHO VÀO GIỎ</a>',
                     f'<a href="javascript:;" onclick="addToCart({pid}, $(\'#sl\').val())">'
                     'CHO VÀO GIỎ</a>')
            .replace('<a href="javascript:;" class="btn-buy" onclick="selectProduct(); '
                     'return false;">ĐẶT HÀNG</a>',
                     f'<a href="javascript:;" class="btn-buy" onclick="addToCart({pid}, '
                     f'$(\'#sl\').val(), \'checkout\')">ĐẶT HÀNG</a>')
            .replace(f'<a href="{pid}-tu-0603-khong-phan-cuc.html">',
                     f'<a href="{pid}-tu-0603-khong-phan-cuc.html" class="active">'))


def card(pid: str, title: str, price: str, thumb: str = "") -> str:
    img = (f'<a href="./{pid}-x.html"><img src="https://cxtvn.com/upload/products/'
           f'thumbs/{thumb}" alt=""></a>') if thumb else ""
    slug = "tu-0603-khong-phan-cuc" if "không phân cực" in title else "tu-khac"
    return f"""<div class="item_lkpa fl">
  <div class="imgitem_lkpa"><span>{img.replace('-x.html', '-' + slug + '.html')}</span></div>
  <div class="infoitem_lkpa"><a href="{pid}-{slug}.html"><h5>{title}</h5></a>
  <p><span style="color:#a0a0a0">Bán lẻ:</span> {price}₫</p></div>
</div>"""


GENERIC = "Tụ 0603 không phân cực"


def search_html(thumbs: bool = True) -> str:
    t = (lambda s: s) if thumbs else (lambda s: "")
    cards = [
        card("12064", "Tụ 180pF 0603 50V (20c)", "4,000",
             "tu-180pf-0603-50v-10c-img-1754387130.jpg"),
        card("6588", GENERIC, "4,000", t("tu-0-5pf-0603-50v-20c-img-1600000001.jpg")),
        card("7174", GENERIC, "4,000", t("tu-150nf-0603-50v-10-20c-img-1600000002.jpg")),
        card("6231", GENERIC, "9,000", t("tu-220nf-0603-25v-10-20c-img-1600000003.jpg")),
        card("6736", GENERIC, "4,000",
             t("0603b104k500nt-tu-100nf-50v-10-0603-10c-img-1658908857.jpg")),
        card("4173", "Tụ 100nF 0603x4 50V (5c)", "5,000",
             "tu-100nf-50v-0603x4-img-1605696191.jpg"),
    ]
    return ("<html><head><title>Tìm kiếm</title></head><body>"
            '<div class="header"><span class="slcart">0</span></div>'
            '<div class="list_lkpa">' + "".join(cards) + "</div></body></html>")


def detail_sites(siblings: bool = True) -> dict[str, str]:
    return {
        P("6736-tu-0603-khong-phan-cuc.html"): generic_page(siblings=siblings),
        P("6588-tu-0603-khong-phan-cuc.html"): generic_page(
            "Tụ 0.5pF 0603 50V (20c)", "6588", "tu-0-5pf-img-1.jpg", siblings),
        P("7174-tu-0603-khong-phan-cuc.html"): generic_page(
            "CL10B154KB8NNNC Tụ 150nF 0603 50V 10% (20c)", "7174",
            "tu-150nf-img-2.jpg", siblings),
        P("6231-tu-0603-khong-phan-cuc.html"): generic_page(
            "Tụ 220nF 0603 25V 10% (20c)", "6231", "tu-220nf-img-3.jpg", siblings),
    }


# ------------------------------------------------------------ trang giả


class FakeHandle:
    def __init__(self, page, node):
        self.page, self.node = page, node
        self.text = node.text() if node is not None else ""

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.node.attrs.get(name) if self.node is not None else None

    def is_visible(self):
        return True

    def fill(self, v):
        self.page.filled.append(v)

    def click(self):
        self.page.click(self)


class FakePage:
    """Trang giả: tìm kiếm trả `search`; bấm link Thông số -> nạp `ajax`;
    bấm nút addToCart (không checkout) -> giỏ tăng."""

    def __init__(self, sites, search, ajax=None):
        self.sites, self.search, self.ajax = dict(sites), search, dict(ajax or {})
        self.url, self.tpl, self.cart = "about:blank", "", 0
        self.visited, self.filled, self.clicked = [], [], []

    @property
    def html(self):
        return self.tpl.replace("{count}", str(self.cart))

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        self.url = url
        self.tpl = self.search if url.startswith(SEARCH_PREFIX) else \
            self.sites.get(url, "<html><body>404</body></html>")

    def content(self):
        return self.html

    def click(self, h):
        onclick = (h.get_attribute("onclick") or "").lower()
        self.clicked.append((h.text, onclick, h.get_attribute("href") or ""))
        if h.node.parent is not None and h.node.parent.id == "sanphamcungloai":
            href = h.get_attribute("href")
            self.tpl = self.ajax.get(href, self.tpl)
        elif "addtocart" in onclick and "checkout" not in onclick:
            self.cart += 1

    def _nodes(self, sel):
        root = parse_html(self.html)
        if sel == "#sanphamcungloai a":
            return [n for n in root.iter() if n.tag == "a" and n.parent is not None
                    and n.parent.id == "sanphamcungloai"]
        if sel == ".sale-button a":
            return [n for n in root.iter() if n.tag == "a" and any(
                "sale-button" in a.classes for a in n.ancestors())]
        if sel == "a[onclick*='addToCart']":
            return [n for n in root.iter() if n.tag == "a"
                    and "addToCart" in n.attrs.get("onclick", "")]
        if sel == ".addCart":
            return [n for n in root.iter() if "addcart" in n.classes]
        if sel in ("#sl", "input#sl"):
            return [n for n in root.iter() if n.id == "sl"]
        return []

    def query_selector_all(self, sel):
        return [FakeHandle(self, n) for n in self._nodes(sel)]

    def query_selector(self, sel):
        found = self.query_selector_all(sel)
        return found[0] if found else None


def make(page, **kw):
    sleeps, clock = [], [0.0]

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    f = C.CartFiller(page, "https://linhkien.cxtvn.com", sleep=sleep,
                     rand=lambda a, b: a, clock=lambda: clock[0],
                     confirm_timeout=1.0, poll=0.5, no_image_out_of_stock=True, **kw)
    return f, sleeps


def detail_visits(page):
    return [u for u in page.visited if not u.startswith(SEARCH_PREFIX)]


# ------------------------------------------------------------ nhận diện


class TestNhanDien(unittest.TestCase):
    def test_ten_chung(self):
        self.assertTrue(X.is_generic_title("Tụ 0603 không phân cực"))
        self.assertTrue(X.is_generic_title("Điện trở dán 0805 1%"))
        self.assertFalse(X.is_generic_title("Tụ 180pF 0603 50V (20c)"))
        self.assertFalse(X.is_generic_title(REAL_TITLE))
        self.assertFalse(X.is_generic_title("Điện trở 1/4W thông số từ 1K đến 99K"))
        self.assertFalse(X.is_generic_title("IC AMS1117-3.3 SOT-223"))
        self.assertFalse(X.is_generic_title("Tụ hóa 100uF 25V"))

    def test_dong_thu_dong(self):
        self.assertTrue(X.is_passive_line(parse_line("Tụ 100nF 0603")))
        self.assertTrue(X.is_passive_line(parse_line("Điện trở 10K 0603")))
        self.assertFalse(X.is_passive_line(parse_line("IC AMS1117-3.3 SOT-223")))

    def test_bo_tien_to_title(self):
        self.assertEqual(
            X.strip_cxt_title("CXT – Từ ý tưởng đến sản phẩm - " + REAL_TITLE),
            REAL_TITLE)
        self.assertEqual(X.strip_cxt_title("Tụ 100nF"), "Tụ 100nF")

    def test_goi_y_anh(self):
        self.assertEqual(
            X.image_hint("https://cxtvn.com/upload/products/thumbs/"
                         "tu-100nf-50v-10-0603-10c-img-1658908857.jpg"),
            "tu 100nf 50v 10 0603 10c")
        self.assertEqual(X.image_hint("https://cxtvn.com/images/no_image.jpg"), "")
        self.assertEqual(X.image_hint(""), "")

    def test_ten_chung_cham_thap_ten_that_cham_cao(self):
        line = parse_line("Tụ 100nF 0603")
        low = X.cxt_score(line, Candidate(title=GENERIC, url="u")).score
        high = X.cxt_score(line, Candidate(title=REAL_TITLE, url="u")).score
        self.assertLess(low, 0.5)
        self.assertGreaterEqual(high, 0.95)


class TestTrangChiTiet(unittest.TestCase):
    def setUp(self):
        self.info = X.parse_cxt_product(GENERIC_HTML, no_image_out_of_stock=True)

    def test_ten_that(self):
        self.assertEqual(self.info.title, GENERIC)
        self.assertEqual(self.info.page_title, REAL_TITLE)
        self.assertEqual(self.info.real_name, REAL_TITLE)
        self.assertEqual(self.info.storage_code, "G030617")
        self.assertEqual(X.detect_cxt_pack(self.info.real_name), 20)

    def test_bang_gia_theo_day(self):
        self.assertEqual(self.info.unit, "Dây")
        self.assertEqual(self.info.tiers, [(1, 4000), (20, 3400), (50, 3200),
                                           (100, 3000)])
        self.assertEqual(self.info.min_buy, 1)
        self.assertEqual(self.info.price_for(2), 4000)
        self.assertEqual(self.info.price_for(25), 3400)

    def test_select_product(self):
        self.assertTrue(self.info.needs_select)
        # Nút giả không cho biết đăng nhập; ở đây nhờ link "Đăng xuất".
        self.assertTrue(self.info.logged_in)
        no_logout = X.parse_cxt_product(GENERIC_HTML.replace("dang-xuat", "x"))
        self.assertIsNone(no_logout.logged_in)
        self.assertIn(("6736-tu-0603-khong-phan-cuc.html", "100nF", False),
                      self.info.siblings)
        after = X.parse_cxt_product(ajax_page(GENERIC_HTML))
        self.assertFalse(after.needs_select)
        self.assertTrue(after.logged_in)

    def test_anh_that_la_con_hang(self):
        self.assertIsNone(self.info.in_stock)
        self.assertIn("0603b104k500nt", self.info.image)

    def test_khong_anh_het_hang(self):
        html = GENERIC_HTML.replace(
            "https://cxtvn.com/upload/products/0603b104k500nt-tu-100nf-50v-10-0603-10c-"
            "img-1658908857.jpg", "https://cxtvn.com/images/no_image.jpg")
        info = X.parse_cxt_product(html, no_image_out_of_stock=True)
        self.assertFalse(info.in_stock)
        self.assertEqual(info.stock_reason, X.REASON_NO_IMAGE)

    def test_gia_0_het_hang(self):
        html = GENERIC_HTML
        for p in ("4,000", "3,400", "3,200", "3,000"):
            html = html.replace(f'<td class="pricelist_td">{p}</td>',
                                '<td class="pricelist_td">0</td>')
        info = X.parse_cxt_product(html, no_image_out_of_stock=True)
        self.assertFalse(info.in_stock)

    def test_du_phong_ten_file_anh_va_ma_luu_tru(self):
        html = GENERIC_HTML.replace(REAL_TITLE, GENERIC)
        info = X.parse_cxt_product(html)
        self.assertIn("100nf", info.real_name.lower())
        html = html.replace("0603b104k500nt-tu-100nf-50v-10-0603-10c-img-1658908857",
                            "anh-1")
        info = X.parse_cxt_product(html)
        self.assertIn("G030617", info.real_name)

    def test_khong_bam_select_product(self):
        page = FakePage({URL_100N: GENERIC_HTML}, search_html())
        f, _ = make(page)
        page.goto(URL_100N)
        self.assertIsNone(f._add_button())


# ------------------------------------------------------------ luồng chọn


class TestDocTenThat(unittest.TestCase):
    def test_uu_tien_goi_y_anh(self):
        page = FakePage(detail_sites(), search_html())
        f, sleeps = make(page)
        dec, _ = f.find(parse_line("Tụ 100nF 0603"))
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.url, URL_100N)
        self.assertEqual(dec.best.title, REAL_TITLE)
        self.assertEqual(dec.best.generic_title, GENERIC)
        self.assertEqual(dec.best.pack, 20)
        self.assertIn("tên thật", dec.message)
        # Gợi ý từ ảnh thumbnail: mở đúng trang 6736 ngay lần đầu.
        self.assertEqual(detail_visits(page), [URL_100N])
        self.assertTrue(sleeps and all(s >= 2.0 for s in sleeps))

    def test_khong_goi_y_dung_nhan_thong_so(self):
        page = FakePage(detail_sites(), search_html(thumbs=False))
        f, _ = make(page)
        dec, _ = f.find(parse_line("Tụ 100nF 0603"))
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.url, URL_100N)
        # Trang đầu tiên cho biết nhãn "Thông số" -> trang kế là 6736.
        self.assertEqual(len(detail_visits(page)), 2)
        self.assertEqual(detail_visits(page)[-1], URL_100N)

    def test_gioi_han_so_trang(self):
        page = FakePage(detail_sites(siblings=False), search_html(thumbs=False))
        f, _ = make(page, max_detail_pages=2)
        dec, _ = f.find(parse_line("Tụ 47nF 0603"))
        self.assertNotEqual(dec.status, MATCH)
        self.assertLessEqual(len(set(detail_visits(page))), 2)

    def test_bo_nho_dem_theo_url(self):
        page = FakePage(detail_sites(), search_html())
        f, _ = make(page)
        f.find(parse_line("Tụ 100nF 0603"))
        n = len(detail_visits(page))
        for _ in range(3):                      # nhiều dòng cùng 'tụ 0603'
            dec, _ = f.find(parse_line("Tụ 100nF 0603"))
            self.assertEqual(dec.best.title, REAL_TITLE)
        self.assertEqual(len(detail_visits(page)), n)
        dec, _ = f.find(parse_line("Tụ 150nF 0603"))
        self.assertEqual(dec.status, MATCH)
        self.assertIn("150nF", dec.best.title)
        visits = detail_visits(page)
        self.assertEqual(len(visits), len(set(visits)))   # mỗi trang mở một lần

    def test_dong_khong_thu_dong_khong_mo_trang(self):
        page = FakePage(detail_sites(), search_html())
        f, _ = make(page)
        f.find(parse_line("IC AMS1117-3.3 SOT-223"))
        self.assertEqual(detail_visits(page), [])

    def test_co_ung_vien_chac_khong_mo_trang(self):
        page = FakePage(detail_sites(), search_html())
        f, _ = make(page)
        dec, _ = f.find(parse_line("Tụ 180pF 0603"))
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(detail_visits(page), [])


# ------------------------------------------------------------ thêm giỏ


class TestThemGio(unittest.TestCase):
    def sites(self):
        return detail_sites(), {"6736-tu-0603-khong-phan-cuc.html":
                                ajax_page(generic_page())}

    def test_chon_thong_so_roi_cho_vao_gio(self):
        sites, ajax = self.sites()
        page = FakePage(sites, search_html(), ajax)
        f, _ = make(page)
        line = dataclasses.replace(parse_line("Tụ 100nF 0603"), qty=30)
        res = f.add_line(line)
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.units, 2)                  # 30 con / 20 con mỗi dây
        self.assertEqual(page.filled, ["2"])
        self.assertIn("đã chọn thông số '100nF'", res.message)
        self.assertIn("gói 20 con/dây", res.message)
        self.assertIn("mua 2 dây", res.message)
        # Thứ tự: link Thông số trước, rồi CHO VÀO GIỎ; không bấm selectProduct
        # hay ĐẶT HÀNG.
        self.assertEqual(page.clicked[0][0], "100nF")
        self.assertEqual(page.clicked[1][0], "CHO VÀO GIỎ")
        self.assertIn("addtocart(6736", page.clicked[1][1])
        self.assertFalse(any("selectproduct" in c[1] for c in page.clicked))
        self.assertFalse(any("checkout" in c[1] for c in page.clicked))

    def test_khong_thay_link_thong_so(self):
        sites = {URL_100N: generic_page(siblings=False)}
        page = FakePage(sites, search_html())
        f, _ = make(page)
        cand = X.CxtCandidate(title=REAL_TITLE, url=URL_100N, price=4000,
                              pack=20, pid="6736")
        res = f.add_to_cart(parse_line("Tụ 100nF 0603"), cand)
        self.assertEqual(res.status, C.UNCERTAIN)
        self.assertIn("selectProduct", res.message)
        self.assertEqual(page.clicked, [])

    def test_ajax_khong_doi_nut(self):
        sites, _ = self.sites()
        page = FakePage(sites, search_html(), {})      # bấm link mà không nạp lại
        f, _ = make(page)
        cand = X.CxtCandidate(title=REAL_TITLE, url=URL_100N, price=4000,
                              pack=20, pid="6736")
        res = f.add_to_cart(parse_line("Tụ 100nF 0603"), cand)
        self.assertEqual(res.status, C.UNCERTAIN)
        self.assertEqual([c[0] for c in page.clicked], ["100nF"])

    def test_tim_kiem_nhieu_ten_chung(self):
        priced, zeros = X.parse_cxt_search(search_html(), SEARCH_PREFIX + "tu")
        generic = [c for c in priced if X.is_generic_title(c.title)]
        self.assertEqual(len(generic), 4)
        self.assertEqual(zeros, [])
        self.assertTrue(all(c.in_stock is None for c in generic))


if __name__ == "__main__":
    unittest.main()
