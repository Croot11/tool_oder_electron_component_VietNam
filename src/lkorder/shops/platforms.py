"""Cách lấy giá theo từng nền tảng web.

Không viết riêng cho từng shop, mà viết theo NỀN TẢNG mà shop đang chạy. Lý
do rất thực tế: shop VN hầu hết dùng lại vài nền tảng thương mại điện tử có
sẵn, và các nền tảng đó phơi ra dữ liệu theo cùng một khuôn. Viết theo nền
tảng thì thêm một shop mới chỉ là thêm một dòng trong `shops.json`, còn viết
theo từng shop thì mỗi lần shop đổi giao diện là hỏng.

Ba nền tảng được hỗ trợ:

    haravan   Haravan / Sapo / Shopify — cùng họ, đều có `/products.json`
    woo       WooCommerce — có Store API `/wp-json/wc/store/v1/products`
    jsonld    Bất kỳ site nào nhúng dữ liệu Schema.org Product trong HTML

⚠ Các endpoint dưới đây là khuôn chung của từng nền tảng. Từng shop cụ thể có
thể tắt bớt, đổi đường dẫn, hoặc chặn truy cập tự động. Hãy chạy
`lk fetch --dry-run` để xem shop nào thực sự trả về dữ liệu trước khi tin vào
kết quả, và luôn dò lại bằng `lk match`.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

from ..models import Offer
from .base import Fetcher

# ---------------------------------------------------------------- tiện ích


def _to_vnd(value: Any, minor_unit: int = 0) -> int:
    """Đưa giá về số nguyên VND.

    Các nền tảng trả giá theo nhiều kiểu khác nhau:
        45000        số nguyên đồng
        "45000.00"   chuỗi có phần thập phân (Shopify/Haravan)
        4500000 + minor_unit=2   đơn vị nhỏ nhất (Woo Store API)
    """
    if value is None:
        return 0
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return 0
        try:
            value = float(s)
        except ValueError:
            digits = re.sub(r"[^\d]", "", s)
            return int(digits) if digits else 0
    if minor_unit:
        value = float(value) / (10 ** minor_unit)
    return int(round(float(value)))


def _clean_html(text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


# ------------------------------------------------------- Haravan / Sapo


class HaravanFetcher(Fetcher):
    """Haravan, Sapo, Shopify — cùng một khuôn dữ liệu.

    Mỗi `product` có nhiều `variant`; mỗi variant mới là thứ đặt mua được, có
    giá và tồn kho riêng. Tên hiển thị ghép tên sản phẩm với tên variant, trừ
    khi variant chỉ là "Default Title" (nghĩa là sản phẩm không có biến thể).
    """

    platform = "haravan"

    def search_urls(self, query: str) -> list[str]:
        from urllib.parse import quote_plus

        q = quote_plus(query)
        return [
            self.abs_url(f"/search?q={q}&view=json"),
            self.abs_url(f"/search?type=product&q={q}&view=json"),
        ]

    def parse(self, body: str, query: str) -> list[Offer]:
        data = json.loads(body)
        products = _extract_products(data)
        out: list[Offer] = []
        for p in products:
            handle = p.get("handle") or ""
            base_title = _clean_html(p.get("title") or "")
            if not base_title:
                continue
            variants = p.get("variants") or [{}]
            for v in variants:
                price = _to_vnd(v.get("price", p.get("price")))
                if price <= 0:
                    continue
                vt = (v.get("title") or "").strip()
                title = base_title
                if vt and vt.lower() not in ("default title", "default"):
                    title = f"{base_title} - {vt}"

                stock = v.get("inventory_quantity")
                # Nhiều shop không quản tồn kho: khi đó trường này bằng 0 nhưng
                # `available` vẫn true. Hiểu 0 là hết hàng sẽ loại nhầm hàng
                # đang bán, nên chỉ tin con số khi nó dương.
                if not isinstance(stock, int) or stock <= 0:
                    stock = None if v.get("available", True) else 0

                out.append(self.make_offer(
                    title=title,
                    price=price,
                    sku=str(v.get("sku") or "").strip(),
                    url=self.abs_url(f"/products/{handle}") if handle else "",
                    stock=stock,
                ))
        return out


def _extract_products(data: Any) -> list[dict]:
    """Lấy danh sách sản phẩm ra khỏi vài kiểu bọc khác nhau."""
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("products", "results", "items", "data"):
        v = data.get(key)
        if isinstance(v, list):
            return [p for p in v if isinstance(p, dict)]
        if isinstance(v, dict):
            inner = v.get("products") or v.get("items")
            if isinstance(inner, list):
                return [p for p in inner if isinstance(p, dict)]
    return []


# --------------------------------------------------------- WooCommerce


class WooFetcher(Fetcher):
    """WooCommerce Store API — công khai, không cần khoá.

    Giá trả về theo đơn vị nhỏ nhất kèm `currency_minor_unit`, ví dụ
    `{"price": "4500000", "currency_minor_unit": 2}` nghĩa là 45.000đ.
    """

    platform = "woo"

    def search_urls(self, query: str) -> list[str]:
        from urllib.parse import quote_plus

        q = quote_plus(query)
        return [
            self.abs_url(f"/wp-json/wc/store/v1/products?search={q}&per_page=20"),
            self.abs_url(f"/wp-json/wc/store/products?search={q}&per_page=20"),
        ]

    def parse(self, body: str, query: str) -> list[Offer]:
        data = json.loads(body)
        items = data if isinstance(data, list) else _extract_products(data)
        out: list[Offer] = []
        for p in items:
            title = _clean_html(p.get("name") or "")
            if not title:
                continue
            prices = p.get("prices") or {}
            minor = prices.get("currency_minor_unit", 0) or 0
            price = _to_vnd(prices.get("price"), minor)
            if price <= 0:
                price = _to_vnd(p.get("price"))
            if price <= 0:
                continue
            stock = p.get("stock_quantity")
            if not isinstance(stock, int):
                stock = None if p.get("is_in_stock", True) else 0
            out.append(self.make_offer(
                title=title,
                price=price,
                sku=str(p.get("sku") or "").strip(),
                url=p.get("permalink") or "",
                stock=stock,
            ))
        return out


# --------------------------------------------------------------- JSON-LD


_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


class JsonLdFetcher(Fetcher):
    """Đọc dữ liệu Schema.org Product nhúng trong HTML.

    Phương án chống đỡ cuối cùng: hầu hết nền tảng đều nhúng khối này để lên
    Google Shopping, kể cả khi không có API nào công khai.
    """

    platform = "jsonld"

    def search_urls(self, query: str) -> list[str]:
        from urllib.parse import quote_plus

        q = quote_plus(query)
        return [
            self.abs_url(f"/search?q={q}"),
            self.abs_url(f"/?s={q}&post_type=product"),
        ]

    def parse(self, body: str, query: str) -> list[Offer]:
        out: list[Offer] = []
        for block in _LD_RE.findall(body):
            try:
                data = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            for node in _walk_ld(data):
                offer = self._offer_from_node(node)
                if offer is not None:
                    out.append(offer)
        return out

    def _offer_from_node(self, node: dict) -> Offer | None:
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        if not any(str(t).lower() == "product" for t in types):
            return None
        title = _clean_html(str(node.get("name") or ""))
        if not title:
            return None

        offers = node.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            return None
        price = _to_vnd(offers.get("price") or offers.get("lowPrice"))
        if price <= 0:
            return None

        avail = str(offers.get("availability") or "").lower()
        stock = 0 if "outofstock" in avail.replace("_", "") else None

        return self.make_offer(
            title=title,
            price=price,
            sku=str(node.get("sku") or node.get("mpn") or "").strip(),
            url=str(offers.get("url") or node.get("url") or ""),
            stock=stock,
        )


def _walk_ld(data: Any):
    """Duyệt cây JSON-LD, nhả ra mọi node là dict.

    Cần duyệt sâu vì Product hay nằm lồng trong @graph hoặc trong ItemList.
    """
    if isinstance(data, dict):
        yield data
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            v = data.get(key)
            if v is not None:
                yield from _walk_ld(v)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_ld(item)


FETCHERS: dict[str, type[Fetcher]] = {
    f.platform: f for f in (HaravanFetcher, WooFetcher, JsonLdFetcher)
}
