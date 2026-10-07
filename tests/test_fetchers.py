"""Kiểm thử phần bóc tách dữ liệu web, bằng dữ liệu mẫu, không cần mạng.

Phần tải mạng không kiểm thử ở đây được (và cũng không nên giả lập cho có).
Cái kiểm thử được — và cũng là chỗ hay sai — là việc hiểu đúng khuôn dữ liệu
của từng nền tảng, nhất là chuyện đơn vị tiền và cách hiểu tồn kho.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder.models import Shop                                    # noqa: E402
from lkorder.shops.base import HttpClient, dedupe                  # noqa: E402
from lkorder.shops.platforms import (                              # noqa: E402
    HaravanFetcher, JsonLdFetcher, WooFetcher, _to_vnd,
)

SHOP = Shop("test", "Shop Test", url="https://shop.example")


def fetcher(cls):
    return cls(SHOP, HttpClient(respect_robots=False))


class TestPrice(unittest.TestCase):
    def test_cac_kieu_bieu_dien_gia(self):
        self.assertEqual(_to_vnd(45000), 45_000)
        self.assertEqual(_to_vnd("45000.00"), 45_000)
        self.assertEqual(_to_vnd("45,000"), 45_000)
        # Woo trả đơn vị nhỏ nhất: 4500000 với minor_unit=2 là 45.000đ
        self.assertEqual(_to_vnd("4500000", 2), 45_000)
        self.assertEqual(_to_vnd(None), 0)
        self.assertEqual(_to_vnd("45.000 đ"), 45_000)


class TestHaravan(unittest.TestCase):
    PAYLOAD = json.dumps({
        "products": [
            {
                "title": "Vi điều khiển STM32F103C8T6",
                "handle": "stm32f103c8t6",
                "variants": [
                    {"title": "Default Title", "price": "46000.00",
                     "sku": "MCU-01", "inventory_quantity": 25, "available": True},
                ],
            },
            {
                "title": "Điện trở 10K",
                "handle": "dien-tro-10k",
                "variants": [
                    {"title": "1/4W", "price": "120.00", "sku": "R-1",
                     "inventory_quantity": 0, "available": True},
                    {"title": "Gói 100 con", "price": "9000.00", "sku": "R-100",
                     "inventory_quantity": 0, "available": False},
                ],
            },
        ]
    })

    def test_bocs_duoc_san_pham_va_bien_the(self):
        offers = fetcher(HaravanFetcher).parse(self.PAYLOAD, "stm32")
        self.assertEqual(len(offers), 3)
        first = offers[0]
        self.assertEqual(first.title, "Vi điều khiển STM32F103C8T6")
        self.assertEqual(first.unit_price, 46_000)
        self.assertEqual(first.sku, "MCU-01")
        self.assertEqual(first.url, "https://shop.example/products/stm32f103c8t6")

    def test_bo_default_title_ghep_ten_bien_the_that(self):
        offers = fetcher(HaravanFetcher).parse(self.PAYLOAD, "tro")
        titles = [o.title for o in offers]
        self.assertIn("Vi điều khiển STM32F103C8T6", titles)   # không có đuôi
        self.assertIn("Điện trở 10K - Gói 100 con", titles)

    def test_ton_kho_0_nhung_con_ban_thi_coi_nhu_khong_ro(self):
        """Nhiều shop không quản tồn kho, để 0 nhưng vẫn bán bình thường."""
        offers = {o.sku: o for o in fetcher(HaravanFetcher).parse(self.PAYLOAD, "x")}
        self.assertIsNone(offers["R-1"].stock)      # available=True  -> không rõ
        self.assertEqual(offers["R-100"].stock, 0)  # available=False -> hết hàng
        self.assertEqual(offers["MCU-01"].stock, 25)

    def test_bo_qua_san_pham_khong_gia(self):
        payload = json.dumps({"products": [
            {"title": "Hàng liên hệ", "handle": "x", "variants": [{"price": "0"}]}
        ]})
        self.assertEqual(fetcher(HaravanFetcher).parse(payload, "x"), [])

    def test_chiu_duoc_json_boc_kieu_khac(self):
        payload = json.dumps({"results": {"products": [
            {"title": "NE555", "handle": "ne555",
             "variants": [{"price": "4000", "sku": "N1"}]}
        ]}})
        offers = fetcher(HaravanFetcher).parse(payload, "ne555")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].unit_price, 4_000)


class TestWoo(unittest.TestCase):
    PAYLOAD = json.dumps([
        {
            "name": "IC NE555P DIP-8",
            "sku": "NE555P",
            "permalink": "https://shop.example/san-pham/ne555p",
            "prices": {"price": "450000", "currency_minor_unit": 2},
            "stock_quantity": 120,
            "is_in_stock": True,
        },
        {
            "name": "Module LM2596",
            "sku": "LM2596",
            "permalink": "https://shop.example/san-pham/lm2596",
            "prices": {"price": "22000", "currency_minor_unit": 0},
            "is_in_stock": False,
        },
    ])

    def test_doi_dung_don_vi_nho_nhat(self):
        offers = fetcher(WooFetcher).parse(self.PAYLOAD, "ne555")
        self.assertEqual(offers[0].unit_price, 4_500)     # 450000 / 100
        self.assertEqual(offers[1].unit_price, 22_000)    # minor_unit = 0

    def test_het_hang_khi_is_in_stock_false(self):
        offers = fetcher(WooFetcher).parse(self.PAYLOAD, "lm2596")
        self.assertEqual(offers[0].stock, 120)
        self.assertEqual(offers[1].stock, 0)


class TestJsonLd(unittest.TestCase):
    HTML = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[
      {"@type":"Product","name":"Thạch anh 16MHz","sku":"X16",
       "offers":{"@type":"Offer","price":"3000","priceCurrency":"VND",
                 "availability":"https://schema.org/InStock",
                 "url":"https://shop.example/thach-anh-16mhz"}},
      {"@type":"Product","name":"Tụ gốm 100nF","sku":"C104",
       "offers":{"price":"500","availability":"https://schema.org/OutOfStock"}}
    ]}
    </script>
    <script type="application/ld+json">{"@type":"Organization","name":"Shop"}</script>
    </head><body></body></html>
    """

    def test_boc_product_trong_graph(self):
        offers = fetcher(JsonLdFetcher).parse(self.HTML, "thach anh")
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].title, "Thạch anh 16MHz")
        self.assertEqual(offers[0].unit_price, 3_000)
        self.assertEqual(offers[0].url, "https://shop.example/thach-anh-16mhz")

    def test_hieu_dung_con_hang_het_hang(self):
        offers = fetcher(JsonLdFetcher).parse(self.HTML, "x")
        self.assertIsNone(offers[0].stock)     # InStock -> không giới hạn
        self.assertEqual(offers[1].stock, 0)   # OutOfStock

    def test_bo_qua_node_khong_phai_product(self):
        html = '<script type="application/ld+json">{"@type":"WebSite"}</script>'
        self.assertEqual(fetcher(JsonLdFetcher).parse(html, "x"), [])

    def test_chiu_duoc_json_hong(self):
        html = '<script type="application/ld+json">{hỏng,,,}</script>'
        self.assertEqual(fetcher(JsonLdFetcher).parse(html, "x"), [])


class TestSearchUrls(unittest.TestCase):
    def test_ma_hoa_query_co_dau_cach(self):
        urls = fetcher(HaravanFetcher).search_urls("điện trở 10K")
        self.assertTrue(all(" " not in u for u in urls))
        self.assertTrue(urls[0].startswith("https://shop.example/search?q="))

    def test_woo_dung_store_api(self):
        urls = fetcher(WooFetcher).search_urls("ne555")
        self.assertIn("/wp-json/wc/store/v1/products", urls[0])


class TestDedupe(unittest.TestCase):
    def test_giu_ban_re_nhat(self):
        from lkorder.models import Offer
        offers = [
            Offer("a", "NE555", 5_000, sku="N1"),
            Offer("a", "NE555", 4_000, sku="N1"),
            Offer("b", "NE555", 4_500, sku="N1"),
        ]
        out = {(o.shop_id, o.sku): o.unit_price for o in dedupe(offers)}
        self.assertEqual(out[("a", "N1")], 4_000)
        self.assertEqual(out[("b", "N1")], 4_500)


class TestRobots(unittest.TestCase):
    def test_tat_kiem_tra_robots_thi_cho_phep_het(self):
        self.assertTrue(HttpClient(respect_robots=False).allowed("https://x.test/a"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
