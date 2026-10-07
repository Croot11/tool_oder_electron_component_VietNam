"""CXT: món không có ảnh (images/no_image.jpg) hoặc giá 0₫ -> "Hết hàng".

HTML mẫu theo các trang thật của linhkien.cxtvn.com:
  9757 — không ảnh, vẫn có nút CHO VÀO GIỎ
  9271 — không ảnh, có giá
  9236 — có ảnh, giá 0₫
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from lkorder import config, report                                  # noqa: E402
from lkorder.bom import parse_line                                  # noqa: E402
from lkorder.browser import cart as C                               # noqa: E402
from lkorder.browser.matcher import MATCH                           # noqa: E402
from lkorder.browser.shops import cxtvn as X                        # noqa: E402
from lkorder.models import Shop                                     # noqa: E402
from test_shop_cxtvn import FakePage, P, make                       # noqa: E402

NOIMG_HTML = (HERE / "data" / "cxtvn_noimage.html").read_text(encoding="utf-8")
NO_IMAGE = "https://linhkien.cxtvn.com/images/no_image.jpg"

# Kết quả tìm kiếm mà 9757 / 9271 có ảnh thu nhỏ — chỉ trang sản phẩm mới lộ
# ra là không có ảnh.
THUMB_HTML = NOIMG_HTML.replace(
    '<a href="9757-tu-dan-10uf-0805.html"><img src="https://linhkien.cxtvn.com/'
    'images/no_image.jpg"',
    '<a href="9757-tu-dan-10uf-0805.html"><img src="/upload/sanpham/9757.jpg"'
).replace('<img src="images/no_image.jpg" alt="IC LM358 SOP-8">',
          '<img src="/upload/sanpham/9271.jpg" alt="IC LM358 SOP-8">')


def cxt_page(title: str, image: str, retail: str = "", tiers: str = "",
             related_image: str = "/upload/sanpham/khac.jpg") -> str:
    """Trang sản phẩm CXT: logo, ảnh chính, giá, nút CHO VÀO GIỎ, SP liên quan."""
    head = (f'<table><tr class="pricelist_head"><td>1+</td><td>10+</td></tr>'
            f'<tr>{tiers}</tr></table>') if tiers else ""
    return f"""<html><head><meta property="og:image" content="{image}"></head><body>
<div class="header"><img src="https://linhkien.cxtvn.com/images/logo.png">
<span class="slcart">{{count}}</span><a href="dang-xuat.html">Đăng xuất</a></div>
<div class="detail_sp">
  <div class="img_detail"><img src="{image}" alt="{title}"></div>
  <h1>{title}</h1>
  <div class="price">Bán lẻ: {retail}</div>
  {head}
  <input type="hidden" id="min_buy" value="1">
  <input id="sl" value="1">
  <a href="javascript:void(0)" class="addCart" onclick="addToCart(1)">CHO VÀO GIỎ</a>
</div>
<div class="splq"><h3>Sản phẩm liên quan</h3>
  <div class="item_lkpa"><div class="imgitem_lkpa"><img src="{related_image}"></div>
  <h5><a href="1-khac.html">Món khác</a></h5><div>Bán lẻ: 0₫</div></div>
</div>
</body></html>"""


PAGE_9757 = cxt_page("Tụ dán 10uF 0805 (50c)", NO_IMAGE, "1.500₫",
                     "<td>1.500₫</td><td>1.300₫</td>")
PAGE_9271 = cxt_page("IC LM358 SOP-8", "images/no_image.jpg", "3.000₫")
PAGE_9236 = cxt_page("IC NE555 SOP-8", "/upload/sanpham/9236.jpg", "0₫")
PAGE_9758 = cxt_page("Tụ dán 10uF 0805 25V (50c)", "/upload/sanpham/9758.jpg",
                     "1.800₫", "<td>1.800₫</td><td>1.600₫</td>",
                     related_image=NO_IMAGE)
PAGE_9272 = cxt_page("IC LM358 DIP-8", "/upload/sanpham/9272.jpg", "2.500₫")


def sites() -> dict[str, str]:
    return {
        P("9757-tu-dan-10uf-0805.html"): PAGE_9757,
        P("9758-tu-dan-10uf-0805-25v.html"): PAGE_9758,
        P("9271-ic-lm358-sop-8.html"): PAGE_9271,
        P("9272-ic-lm358-dip-8.html"): PAGE_9272,
        P("9236-ic-ne555-sop-8.html"): PAGE_9236,
    }


def by_pid(items):
    return {c.pid: c for c in items}


# ------------------------------------------------------------ đọc trang


class TestSearch(unittest.TestCase):
    def parse(self, flag=True, html=NOIMG_HTML):
        priced, zero = X.parse_cxt_search(html, X.SEARCH_URL,
                                          no_image_out_of_stock=flag)
        return by_pid(priced), by_pid(zero)

    def test_khong_anh_la_het_hang(self):
        priced, _ = self.parse()
        for pid in ("9757", "9271"):
            self.assertIs(priced[pid].in_stock, False, pid)
            self.assertEqual(priced[pid].stock_reason, "không có ảnh")
        self.assertEqual(priced["9271"].image, NO_IMAGE)      # link tương đối
        for pid in ("9758", "9759", "9272"):
            self.assertIsNone(priced[pid].in_stock, pid)
            self.assertEqual(priced[pid].stock_reason, "")
        self.assertTrue(priced["9758"].image.endswith("/upload/sanpham/9758.jpg"))

    def test_gia_0_la_het_hang(self):
        _, zero = self.parse()
        self.assertEqual(list(zero), ["9236"])
        self.assertIs(zero["9236"].in_stock, False)
        self.assertEqual(zero["9236"].stock_reason, "giá 0₫")

    def test_tat_tuy_chon_thi_khong_xet_anh(self):
        priced, zero = self.parse(flag=False)
        self.assertIsNone(priced["9757"].in_stock)
        self.assertIsNone(priced["9271"].in_stock)
        self.assertIs(zero["9236"].in_stock, False)           # giá 0₫ vẫn hết

    def test_khong_co_the_anh_thi_khong_doan(self):
        # Mẫu cũ không có .imgitem_lkpa: không coi là hết hàng.
        old = (HERE / "data" / "cxtvn_search.html").read_text(encoding="utf-8")
        priced, _ = X.parse_cxt_search(old, X.SEARCH_URL, no_image_out_of_stock=True)
        self.assertTrue(all(c.in_stock is None for c in priced))

    def test_is_no_image(self):
        for u in (NO_IMAGE, "images/no_image.jpg", "/images/no_image.jpg?v=2",
                  "https://linhkien.cxtvn.com/IMAGES/No_Image.jpg"):
            self.assertTrue(X.is_no_image(u), u)
        for u in ("", "/upload/a.jpg", "images/no_image.jpg.png",
                  "images/no_image_2.jpg"):
            self.assertFalse(X.is_no_image(u), u)


class TestProductPage(unittest.TestCase):
    def info(self, html, flag=True):
        return X.parse_cxt_product(html.replace("{count}", "0"),
                                   no_image_out_of_stock=flag)

    def test_9757_khong_anh_van_co_nut(self):
        info = self.info(PAGE_9757)
        self.assertTrue(info.logged_in)                       # có nút CHO VÀO GIỎ
        self.assertTrue(X.is_no_image(info.image))
        self.assertIs(info.in_stock, False)
        self.assertEqual(info.stock_reason, "không có ảnh")
        self.assertIsNone(self.info(PAGE_9757, flag=False).in_stock)

    def test_9271_khong_anh_co_gia(self):
        info = self.info(PAGE_9271)
        self.assertEqual(info.retail, 3000)
        self.assertIs(info.in_stock, False)
        self.assertEqual(info.stock_reason, "không có ảnh")

    def test_9236_co_anh_gia_0(self):
        info = self.info(PAGE_9236)
        self.assertFalse(X.is_no_image(info.image))
        self.assertEqual(info.retail, 0)
        self.assertIs(info.in_stock, False)
        self.assertEqual(info.stock_reason, "giá 0₫")

    def test_anh_sp_lien_quan_khong_tinh(self):
        info = self.info(PAGE_9758)
        self.assertTrue(info.image.endswith("9758.jpg"))
        self.assertIsNone(info.in_stock)
        self.assertEqual(info.tiers, [(1, 1800), (10, 1600)])

    def test_og_image_khi_khong_co_img(self):
        html = (f'<html><head><meta property="og:image" content="{NO_IMAGE}">'
                f'</head><body><h1>X</h1><div>Bán lẻ: 5.000₫</div></body></html>')
        self.assertIs(self.info(html).in_stock, False)


# ------------------------------------------------------------ chọn ứng viên


class TestPick(unittest.TestCase):
    def pick(self, raw, flag=True, html=NOIMG_HTML):
        priced, zero = X.parse_cxt_search(html, X.SEARCH_URL,
                                          no_image_out_of_stock=flag)
        return X.cxt_pick(parse_line(raw), priced, zero)

    def test_bo_qua_mon_het_hang_lay_mon_khac_dung_chan(self):
        dec, group = self.pick("Tụ 10uF 0805 x20")
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.pid, "9758")             # không phải 9757/9759
        self.assertEqual([c.pid for c in group], ["9758"])

    def test_khong_con_mon_nao_thi_het_hang(self):
        dec, _ = self.pick("LM358 SOP-8 x5")
        self.assertEqual(dec.status, C.OUT_OF_STOCK)
        self.assertEqual(dec.best.pid, "9271")             # không lấy DIP-8
        self.assertEqual(dec.message, "Hết hàng (không có ảnh)")

    def test_gia_0(self):
        dec, _ = self.pick("NE555 SOP-8 x2")
        self.assertEqual(dec.status, C.OUT_OF_STOCK)
        self.assertEqual(dec.message, "Hết hàng (giá 0₫)")

    def test_tat_tuy_chon(self):
        dec, _ = self.pick("LM358 SOP-8 x5", flag=False)
        self.assertEqual(dec.status, MATCH)
        self.assertEqual(dec.best.pid, "9271")


# ------------------------------------------------------------ luồng thêm giỏ


class TestFlow(unittest.TestCase):
    def filler(self, html=NOIMG_HTML, flag=True, confirm="count"):
        page = FakePage(sites(), search=html, confirm=confirm)
        f, _ = make(page, no_image_out_of_stock=flag)
        return f, page

    def test_khong_anh_khong_them_gio(self):
        f, page = self.filler()
        res = f.add_line(parse_line("LM358 SOP-8 x5"))
        self.assertEqual(res.status, C.OUT_OF_STOCK)
        self.assertEqual(res.message, "Hết hàng (không có ảnh)")
        self.assertEqual(res.product.pid, "9271")
        self.assertEqual(res.units, 0)
        self.assertEqual(page.clicked, [])
        # không vào trang sản phẩm nào, không sang shop khác
        self.assertTrue(all(u.startswith("https://linhkien.cxtvn.com/tim-kiem.html")
                            for u in page.visited), page.visited)

    def test_gia_0_khong_them_gio(self):
        f, page = self.filler()
        res = f.add_line(parse_line("NE555 SOP-8 x2"))
        self.assertEqual(res.status, C.OUT_OF_STOCK)
        self.assertEqual(res.message, "Hết hàng (giá 0₫)")
        self.assertEqual(page.clicked, [])

    def test_xet_tiep_ung_vien_khac(self):
        f, page = self.filler()
        res = f.add_line(parse_line("Tụ 10uF 0805 x20"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.product.pid, "9758")
        self.assertNotIn(P("9757-tu-dan-10uf-0805.html"), page.visited)
        self.assertEqual(page.clicked, ["CHO VÀO GIỎ"])

    def test_trang_sp_khong_anh_thi_chon_lai(self):
        # Ảnh thu nhỏ có, nhưng trang 9757 không có ảnh dù vẫn có nút CHO VÀO GIỎ.
        f, page = self.filler(html=THUMB_HTML)
        res = f.add_line(parse_line("Tụ 10uF 0805 x20"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.product.pid, "9758")
        self.assertIn(P("9757-tu-dan-10uf-0805.html"), page.visited)
        self.assertEqual(page.clicked, ["CHO VÀO GIỎ"])     # chỉ bấm ở 9758
        self.assertEqual(f.known_oos, {P("9757-tu-dan-10uf-0805.html"): "không có ảnh"})

    def test_trang_sp_khong_anh_het_ung_vien(self):
        f, page = self.filler(html=THUMB_HTML)
        res = f.add_line(parse_line("LM358 SOP-8 x5"))
        self.assertEqual(res.status, C.OUT_OF_STOCK)
        self.assertEqual(res.message, "Hết hàng (không có ảnh)")
        self.assertEqual(page.clicked, [])
        self.assertNotIn(P("9272-ic-lm358-dip-8.html"), page.visited)

    def test_tat_tuy_chon_thi_them_gio_nhu_cu(self):
        f, page = self.filler(flag=False)
        res = f.add_line(parse_line("LM358 SOP-8 x5"))
        self.assertEqual(res.status, C.ADDED, res.message)
        self.assertEqual(res.product.pid, "9271")

    def test_run_nhieu_dong(self):
        f, page = self.filler()
        out = f.run([parse_line("Tụ 10uF 0805 x20"), parse_line("LM358 SOP-8 x5"),
                     parse_line("NE555 SOP-8 x2")])
        self.assertEqual([r.status for r in out],
                         [C.ADDED, C.OUT_OF_STOCK, C.OUT_OF_STOCK])
        self.assertEqual(C.summarize(out)[C.OUT_OF_STOCK], 2)

    def test_dry_run_cli(self):
        from lkorder import cli
        page = FakePage(sites(), search=NOIMG_HTML)
        out = cli.run_cart(page, [parse_line("LM358 SOP-8 x5")],
                           "https://cxtvn.com", dry_run=True, notify=None,
                           sleep=lambda s: None, rand=lambda a, b: a,
                           no_image_out_of_stock=True)
        self.assertEqual(out[0].status, C.OUT_OF_STOCK)
        self.assertEqual(out[0].message, "Hết hàng (không có ảnh)")


# ------------------------------------------------------------ cấu hình


class TestConfig(unittest.TestCase):
    def test_shops_json(self):
        shops = config.load_shops(HERE.parent / "data" / "shops.json")
        self.assertTrue(shops["cxt"].no_image_means_out_of_stock)
        for sid, s in shops.items():
            if sid != "cxt":
                self.assertFalse(s.no_image_means_out_of_stock, sid)
        self.assertTrue(X.cxt_no_image_flag(shops))

    def test_mac_dinh_false(self):
        self.assertFalse(Shop("a", "A").no_image_means_out_of_stock)
        self.assertFalse(X.cxt_no_image_flag({"x": Shop("x", "X", url="https://hshop.vn")}))
        self.assertFalse(X.cxt_no_image_flag({"cxt": Shop("cxt", "CXT",
                                                          url="https://cxtvn.com")}))

    def test_filler_doc_shops_json(self):
        page = FakePage({})
        self.assertTrue(C.CartFiller(page, "https://cxtvn.com").no_image_out_of_stock)
        self.assertFalse(C.CartFiller(page, "https://cxtvn.com",
                                      no_image_out_of_stock=False).no_image_out_of_stock)
        f = C.CartFiller(page, "https://hshop.vn", platform="haravan")
        self.assertFalse(f.no_image_out_of_stock)

    def test_luu_lai_khong_mat_co(self):
        shops = config.load_shops(HERE.parent / "data" / "shops.json")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "shops.json"
            config.save_shops(shops, p)
            raw = {s["id"]: s for s in json.loads(p.read_text(encoding="utf-8"))["shops"]}
            self.assertIs(raw["cxt"]["no_image_means_out_of_stock"], True)
            self.assertNotIn("no_image_means_out_of_stock", raw["hshop"])
            self.assertTrue(config.load_shops(p)["cxt"].no_image_means_out_of_stock)

    def test_web_luu_shop(self):
        from lkorder import web
        shops = config.load_shops(HERE.parent / "data" / "shops.json")
        js = [web.shop_json(s) for s in shops.values()]
        with tempfile.TemporaryDirectory() as d:
            app = web.App(Path(d) / "shops.json", Path(d) / "catalog.csv")
            self.assertTrue(app.save_shops({"shops": js}).get("ok"))
            self.assertTrue(config.load_shops(Path(d) / "shops.json")["cxt"]
                            .no_image_means_out_of_stock)


# ------------------------------------------------------------ báo cáo


def results():
    priced, zero = X.parse_cxt_search(NOIMG_HTML, X.SEARCH_URL,
                                      no_image_out_of_stock=True)
    c = by_pid(priced + zero)
    line = parse_line
    return [
        C.LineResult(line("Tụ 10uF 0805 x20"), C.ADDED, c["9758"], 1, 50, [c["9758"]]),
        C.LineResult(line("LM358 SOP-8 x5"), C.OUT_OF_STOCK, c["9271"],
                     message="Hết hàng (không có ảnh)"),
        C.LineResult(line("NE555 SOP-8 x2"), C.OUT_OF_STOCK, c["9236"],
                     message="Hết hàng (giá 0₫)"),
        C.LineResult(line("STM32F103C8T6 x1"), C.NOT_FOUND,
                     message="không có sản phẩm nào khớp"),
        C.LineResult(line("Relay HK4100F x2"), C.UNCERTAIN, message="trùng tên"),
    ]


class TestReport(unittest.TestCase):
    def test_nhom_rieng(self):
        g = report.group_cart_results(results())
        self.assertEqual([len(g[k]) for k in ("added", "not_found", "out_of_stock",
                                              "check")], [1, 1, 2, 1])
        self.assertEqual(report.cart_counts(results())["out_of_stock"], 2)

    def test_terminal(self):
        text = report.render_cart(results(), "https://linhkien.cxtvn.com")
        self.assertIn("HẾT HÀNG", text)
        oos = text.split("HẾT HÀNG", 1)[1].split("CẦN KIỂM TRA", 1)[0]
        self.assertIn("LM358 SOP-8 × 5 — Hết hàng (không có ảnh)", oos)
        self.assertIn("NE555 SOP-8 × 2 — Hết hàng (giá 0₫)", oos)
        self.assertNotIn("STM32", oos)
        self.assertIn("Hết hàng 2", text)
        nf = text.split("KHÔNG THẤY TRÊN SHOP", 1)[1].split("HẾT HÀNG", 1)[0]
        self.assertNotIn("LM358", nf)

    def test_markdown(self):
        md = report.cart_to_markdown(results(), "https://linhkien.cxtvn.com")
        self.assertIn("## HẾT HÀNG", md)
        sec = md.split("## HẾT HÀNG", 1)[1].split("## ", 1)[0]
        self.assertIn("| LM358 SOP-8 | 5 |", sec)
        self.assertIn("| NE555 SOP-8 | 2 |", sec)
        self.assertIn("Hết hàng (giá 0₫)", sec)
        self.assertNotIn("STM32", sec)
        self.assertNotIn("HK4100F", sec)
        self.assertIn("Hết hàng 2", md)


class TestWeb(unittest.TestCase):
    def test_nhom_web(self):
        from lkorder import web
        rs = results()
        self.assertEqual(web.line_result_json(rs[1])["group"], "out_of_stock")
        self.assertEqual(web.line_result_json(rs[1])["message"],
                         "Hết hàng (không có ảnh)")
        job = web.CartJob([r.line for r in rs], "https://linhkien.cxtvn.com")
        for i, r in enumerate(rs):
            job.finish_line(i, r)
        job.finish()
        self.assertEqual(job.to_json()["counts"],
                         {"added": 1, "not_found": 1, "out_of_stock": 2, "check": 1})

    def test_giao_dien_co_nhom_het_hang(self):
        html = (HERE.parent / "src" / "lkorder" / "web" / "index.html").read_text(
            encoding="utf-8")
        self.assertIn("['out_of_stock','Hết hàng']", html)


if __name__ == "__main__":
    unittest.main()
