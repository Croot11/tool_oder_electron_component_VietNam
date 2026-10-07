"""Tìm từng dòng BOM trên shop và bỏ vào giỏ hàng — KHÔNG BAO GIỜ thanh toán.

Luồng cho mỗi dòng BOM:

1. Mở URL tìm kiếm của shop (hoặc gõ vào ô tìm kiếm nếu URL không ra gì).
2. Đọc danh sách kết quả, chấm điểm bằng `matcher` (dựa trên normalize.py).
3. Không đủ chắc -> trả `uncertain` kèm ứng viên, KHÔNG bỏ giỏ.
4. Đủ chắc -> vào trang sản phẩm, tính số lượng theo gói/MOQ/bước nhảy,
   điền số lượng, bấm "Thêm vào giỏ".
5. Xác nhận giỏ đã tăng (số trên biểu tượng giỏ, thông báo "đã thêm", hoặc bị
   chuyển sang trang giỏ). Không xác nhận được -> `uncertain`.

Giữa hai dòng nghỉ một khoảng ngẫu nhiên để không dồn dập lên shop.

An toàn: mọi nút được bấm đều qua `is_checkout_like` — nút nào có chữ
"thanh toán", "mua ngay", "đặt hàng", "checkout"... đều bị từ chối.

Phần đọc HTML (đếm giỏ, đọc trang sản phẩm) là hàm thuần trên chuỗi để kiểm thử
được bằng HTML mẫu; phần thao tác dùng API đồng bộ của Playwright (`page.goto`,
`page.content`, `page.query_selector`, `handle.fill/click`), nên test bằng
trang giả là đủ.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote_plus, urljoin

from ..models import BomLine
from .matcher import (
    ACCEPT_SCORE, CONSIDER_SCORE, MATCH, NONE, Candidate, MatchDecision, Node,
    _fold, detect_pack, parse_html, parse_search_results, pick_best,
    search_queries,
)

# Trạng thái kết quả từng dòng
ADDED = "added"
NOT_FOUND = "not_found"
UNCERTAIN = "uncertain"
ERROR = "error"
OUT_OF_STOCK = "out_of_stock"   # khớp đúng món nhưng shop hết hàng: không bỏ giỏ

DEFAULT_DELAY = (2.0, 5.0)      # giây nghỉ giữa hai dòng BOM (ngẫu nhiên)
CONFIRM_TIMEOUT = 8.0           # giây chờ giỏ hàng tăng sau khi bấm
CONFIRM_POLL = 0.5

# URL tìm kiếm mặc định theo nền tảng. {base} = gốc shop, {q} = từ khoá.
SEARCH_URLS: dict[str, str] = {
    "haravan": "{base}/search?q={q}",
    "shopify": "{base}/search?q={q}&type=product",
    "sapo": "{base}/search?query={q}",
    "woo": "{base}/?s={q}&post_type=product",
}
DEFAULT_SEARCH_URL = "{base}/search?q={q}"

# ------------------------------------------------------------ an toàn

_FORBIDDEN = re.compile(
    r"thanh toan|dat hang|mua ngay|checkout|check out|buy now|place order|"
    r"pay now|thanh toan ngay|tien hanh|xac nhan don|order now"
)


def is_checkout_like(text: str = "", href: str = "") -> bool:
    """Nút/link này có dẫn tới thanh toán/đặt hàng không. Có -> cấm bấm."""
    t = _fold(f"{text} {href}")
    return bool(_FORBIDDEN.search(t)) or "/checkout" in t or "/thanh-toan" in t


class CheckoutRefused(RuntimeError):
    """Tool đã từ chối bấm một nút trông như nút thanh toán."""


# ------------------------------------------------------------ bộ chọn CSS


@dataclass
class CartSelectors:
    """Bộ chọn CSS cho các thao tác trên trang. Ghi đè được theo từng shop."""

    qty_input: tuple[str, ...] = (
        "form[action*='cart'] input[name='quantity']",
        "input[name='quantity']",
        "input[name='qty']",
        "input.qty",
        "input#quantity",
        "input.quantity",
        "input.input-text.qty",
        "input[type='number']",
    )
    add_button: tuple[str, ...] = (
        "button[name='add-to-cart']",
        "button.single_add_to_cart_button",
        "#add-to-cart",
        "#AddToCart",
        "button[name='add']",
        "button.add-to-cart",
        "button.btn-add-to-cart",
        ".btn-addtocart",
        "[data-action='add-to-cart']",
        "button:has-text('Thêm vào giỏ')",
        "a:has-text('Thêm vào giỏ')",
        "button:has-text('Cho vào giỏ')",
        "button:has-text('Add to cart')",
        "input[type='submit'][value*='giỏ' i]",
    )
    search_input: tuple[str, ...] = (
        "input[type='search']",
        "input[name='q']",
        "input[name='s']",
        "input[name='query']",
        "input[name='keyword']",
    )


# ------------------------------------------------------------ đọc HTML

_CART_COUNT_CLASS = re.compile(
    r"cart[-_]?(?:count|number|num|qty|quantity|total[-_]?items?|items?[-_]?count|badge)"
    r"|count[-_]?cart|number[-_]?cart|cart-contents-count|minicart[-_]?(?:qty|count)"
    r"|header[-_]?cart[-_]?count|cartcount|soluonggio|so-luong-gio"
)


def _int_in(text: str) -> Optional[int]:
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def read_cart_count(html: str) -> Optional[int]:
    """Số trên biểu tượng giỏ hàng. Không thấy thì None.

    Dò theo thứ tự: thuộc tính data-cart-count; phần tử có class/id kiểu
    cart-count; phần tử con có số bên trong link tới /cart hoặc /gio-hang.
    """
    root = parse_html(html)
    for n in root.iter():
        v = n.get("data-cart-count") or n.get("data-cart-items")
        if v and _int_in(v) is not None:
            return _int_in(v)
    for n in root.iter():
        names = n.classes + ([n.id] if n.id else [])
        if any(_CART_COUNT_CLASS.search(c) for c in names):
            v = _int_in(n.text())
            if v is not None:
                return v
    for a in root.find_all(lambda x: x.tag == "a"):
        href = a.get("href").lower()
        if not re.search(r"/cart\b|/gio-hang|/giohang|cart\.php", href):
            continue
        for c in a.iter():
            if c.find(lambda _: True) is None:     # phần tử lá
                v = _int_in(c.text())
                if v is not None and c.text().strip().isdigit():
                    return v
    return None


_ADDED_TEXTS = (
    "da them vao gio", "them vao gio hang thanh cong", "da duoc them vao gio",
    "them thanh cong", "added to cart", "added to your cart",
    "has been added to your cart", "been added to the cart",
)


def has_added_message(html: str) -> bool:
    """Trang có thông báo kiểu 'Đã thêm vào giỏ hàng' không."""
    t = _fold(parse_html(html).text())
    return any(k in t for k in _ADDED_TEXTS)


@dataclass
class ProductPage:
    """Những gì cần biết trên trang sản phẩm để đặt đúng số lượng."""

    title: str = ""
    moq: int = 1                  # số đơn vị bán tối thiểu
    step: int = 1                 # bước nhảy số lượng
    max_qty: Optional[int] = None
    pack: int = 1                 # số linh kiện trong một đơn vị bán
    in_stock: Optional[bool] = None
    has_qty_input: bool = False


_MOQ_TEXT = re.compile(
    r"(?:toi thieu|it nhat|moq|minimum(?: order)?(?: quantity)?|min\.? order)"
    r"\D{0,25}?(\d+)"
)
_PACK_TEXT = re.compile(
    r"(?:quy cach|dong goi|so luong(?: moi| tren)? (?:goi|tui|bich)|"
    r"(?:moi|1) (?:goi|tui|bich|cuon|vi|hop)(?: gom| co)?)\D{0,15}?(\d+)\s*"
    r"(?:con|cai|chiec|pcs|c)\b"
)


def _int_attr(n: Node, name: str) -> Optional[int]:
    v = n.get(name).strip()
    try:
        return int(float(v)) if v else None
    except ValueError:
        return None


def parse_product_page(html: str) -> ProductPage:
    root = parse_html(html)
    info = ProductPage()
    h1 = root.find(lambda n: n.tag == "h1")
    if h1:
        info.title = h1.text()

    qty = root.find(lambda n: n.tag == "input" and (
        n.get("name").lower() in ("quantity", "qty")
        or any(c in ("qty", "quantity", "input-qty", "quantity-input")
               for c in n.classes)))
    if qty is not None:
        info.has_qty_input = True
        mn, st, mx = (_int_attr(qty, "min"), _int_attr(qty, "step"),
                      _int_attr(qty, "max"))
        if mn and mn > 1:
            info.moq = mn
        if st and st > 1:
            info.step = st
        if mx and mx > 0:
            info.max_qty = mx

    body = _fold(root.text())
    m = _MOQ_TEXT.search(body)
    if m:
        info.moq = max(info.moq, int(m.group(1)))
    m = _PACK_TEXT.search(body)
    pack = int(m.group(1)) if m else 1
    info.pack = pack if pack > 1 else detect_pack(info.title)

    if any(k in body for k in ("het hang", "tam het hang", "out of stock",
                               "sold out", "ngung kinh doanh")):
        info.in_stock = False
    elif "con hang" in body or "in stock" in body:
        info.in_stock = True
    return info


def units_to_order(want: int, pack: int = 1, moq: int = 1, step: int = 1) -> int:
    """Số đơn vị bán cần đặt để có đủ `want` linh kiện.

    Làm tròn lên theo gói, nâng lên MOQ, rồi làm tròn lên theo bước nhảy.
    """
    pack = max(1, pack)
    units = max(1, -(-max(1, want) // pack))
    units = max(units, moq or 1)
    if step and step > 1:
        units = -(-units // step) * step
    return units


# ------------------------------------------------------------ kết quả


@dataclass
class LineResult:
    line: BomLine
    status: str          # added / not_found / uncertain / error / out_of_stock
    product: Optional[Candidate] = None
    units: int = 0                           # số đơn vị bán đã điền
    pieces: int = 0                          # số linh kiện tương ứng
    candidates: list[Candidate] = field(default_factory=list)
    query: str = ""
    message: str = ""

    def summary(self) -> str:
        name = self.line.raw or self.line.key
        if self.status == ADDED and self.product:
            return (f"[added] {name}: {self.units} x '{self.product.title}'"
                    f" ({self.pieces} con)")
        extra = f" — {self.message}" if self.message else ""
        return f"[{self.status}] {name}{extra}"


# ------------------------------------------------------------ thao tác trang


class CartFiller:
    """Bỏ các dòng BOM vào giỏ của MỘT shop, dùng một trang Playwright.

    Shop có bộ xử lý riêng (xem `browser/shops`, vd *.cxtvn.com) thì
    `CartFiller(page, base_url, ...)` tự trả về lớp riêng đó.
    """

    def __new__(cls, page: Any = None, base_url: str = "", *args: Any,
                **kwargs: Any) -> "CartFiller":
        if cls is CartFiller:
            from .shops import filler_class_for
            cls = filler_class_for(base_url) or cls
        return super().__new__(cls)

    def __init__(self, page: Any, base_url: str, *,
                 search_url: str | None = None,
                 platform: str = "",
                 selectors: CartSelectors | None = None,
                 accept: float = ACCEPT_SCORE,
                 consider: float = CONSIDER_SCORE,
                 delay: tuple[float, float] = DEFAULT_DELAY,
                 confirm_timeout: float = CONFIRM_TIMEOUT,
                 poll: float = CONFIRM_POLL,
                 sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[float, float], float] = random.uniform,
                 clock: Callable[[], float] = time.monotonic,
                 notify: Callable[[str], None] | None = None,
                 no_image_out_of_stock: bool = False) -> None:
        self.page = page
        # data/shops.json "no_image_means_out_of_stock": shop có bộ xử lý riêng
        # (vd CXT) coi sản phẩm không có ảnh là hết hàng.
        self.no_image_out_of_stock = bool(no_image_out_of_stock)
        self.base_url = base_url.rstrip("/")
        self.search_url = search_url or SEARCH_URLS.get(platform, DEFAULT_SEARCH_URL)
        self.sel = selectors or CartSelectors()
        self.accept, self.consider = accept, consider
        self.delay = delay
        self.confirm_timeout, self.poll = confirm_timeout, poll
        self.sleep, self.rand, self.clock = sleep, rand, clock
        self.notify = notify

    # -- tiện ích

    def _say(self, msg: str) -> None:
        if self.notify:
            self.notify(msg)

    def _goto(self, url: str) -> str:
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page.content()

    def _first(self, selectors: Iterable[str]) -> Any:
        for s in selectors:
            try:
                h = self.page.query_selector(s)
            except Exception:
                continue
            if h is None:
                continue
            try:
                if hasattr(h, "is_visible") and not h.is_visible():
                    continue
            except Exception:
                pass
            return h
        return None

    @staticmethod
    def _label(handle: Any) -> tuple[str, str]:
        text = ""
        for getter in (lambda: handle.inner_text(),
                       lambda: handle.get_attribute("value"),
                       lambda: handle.get_attribute("aria-label")):
            try:
                text = getter() or ""
            except Exception:
                text = ""
            if text.strip():
                break
        try:
            href = handle.get_attribute("href") or ""
        except Exception:
            href = ""
        return text, href

    def _safe_click(self, handle: Any) -> None:
        text, href = self._label(handle)
        if is_checkout_like(text, href):
            raise CheckoutRefused(f"Từ chối bấm nút có vẻ là thanh toán: {text!r}")
        handle.click()

    # -- tìm kiếm

    def search_page_url(self, query: str) -> str:
        return self.search_url.format(base=self.base_url, q=quote_plus(query))

    def _search_via_box(self, query: str) -> str:
        """Phương án dự phòng: gõ vào ô tìm kiếm ở trang chủ rồi Enter."""
        self._goto(self.base_url + "/")
        box = self._first(self.sel.search_input)
        if box is None:
            return ""
        box.fill(query)
        box.press("Enter")
        try:
            self.page.wait_for_load_state("domcontentloaded")
        except Exception:
            self.sleep(self.poll)
        return self.page.content()

    def search(self, query: str) -> list[Candidate]:
        html = self._goto(self.search_page_url(query))
        found = parse_search_results(html, getattr(self.page, "url", "") or self.base_url)
        if not found:
            html = self._search_via_box(query)
            if html:
                found = parse_search_results(
                    html, getattr(self.page, "url", "") or self.base_url)
        return found

    def find(self, line: BomLine) -> tuple[MatchDecision, str]:
        """Thử lần lượt các từ khoá, dừng ở từ khoá đầu tiên cho kết quả khớp."""
        best: tuple[MatchDecision, str] | None = None
        for q in search_queries(line):
            dec = pick_best(line, self.search(q), accept=self.accept,
                            consider=self.consider)
            if dec.status == MATCH:
                return dec, q
            if best is None or (best[0].status == NONE and dec.status != NONE) or (
                    dec.best and best[0].best
                    and dec.best.score > best[0].best.score):
                best = (dec, q)
        assert best is not None
        return best

    # -- thêm vào giỏ

    def _wait_added(self, before: Optional[int], product_url: str) -> tuple[bool, str]:
        deadline = self.clock() + self.confirm_timeout
        while True:
            html = self.page.content()
            after = read_cart_count(html)
            if before is not None and after is not None and after > before:
                return True, f"giỏ tăng {before} -> {after}"
            if before is None and after is not None and after > 0:
                return True, f"giỏ có {after} món"
            if has_added_message(html):
                return True, "shop báo đã thêm vào giỏ"
            url = (getattr(self.page, "url", "") or "").lower()
            if url != product_url.lower() and re.search(r"/cart\b|/gio-hang", url):
                return True, "đã chuyển sang trang giỏ hàng"
            if self.clock() >= deadline:
                return False, "bấm thêm vào giỏ nhưng không thấy giỏ tăng"
            self.sleep(self.poll)

    def add_to_cart(self, line: BomLine, cand: Candidate) -> LineResult:
        html = self._goto(cand.url)
        info = parse_product_page(html)
        if info.in_stock is False:
            return LineResult(line, NOT_FOUND, cand, message="sản phẩm đã hết hàng")
        pack = info.pack if info.pack > 1 else cand.pack
        units = units_to_order(line.qty, pack, info.moq, info.step)
        if info.max_qty and units > info.max_qty:
            return LineResult(line, UNCERTAIN, cand, units, units * pack, [cand],
                              message=f"cần {units} nhưng shop chỉ cho tối đa "
                                      f"{info.max_qty}")
        before = read_cart_count(html)

        qty_box = self._first(self.sel.qty_input)
        if qty_box is not None:
            qty_box.fill(str(units))
        elif units > 1:
            return LineResult(line, ERROR, cand, units, units * pack, [cand],
                              message=f"không thấy ô số lượng để điền {units}")

        button = self._first(self.sel.add_button)
        if button is None:
            return LineResult(line, ERROR, cand, units, units * pack, [cand],
                              message="không thấy nút Thêm vào giỏ")
        self._safe_click(button)

        ok, why = self._wait_added(before, cand.url)
        notes = [why]
        if pack > 1:
            notes.append(f"bán theo gói {pack} con")
        if info.moq > 1:
            notes.append(f"MOQ {info.moq}")
        return LineResult(line, ADDED if ok else UNCERTAIN, cand, units,
                          units * pack, [cand], message="; ".join(notes))

    def add_line(self, line: BomLine) -> LineResult:
        try:
            dec, q = self.find(line)
            if dec.status == OUT_OF_STOCK:
                # Không bỏ giỏ, không chuyển sang shop khác: báo riêng "Hết hàng".
                return LineResult(line, OUT_OF_STOCK, dec.best,
                                  candidates=dec.shortlist, query=q,
                                  message=dec.message)
            if dec.status == NONE:
                return LineResult(line, NOT_FOUND, dec.best, candidates=dec.shortlist,
                                  query=q, message=dec.message)
            if dec.status != MATCH:
                return LineResult(line, UNCERTAIN, dec.best, candidates=dec.shortlist,
                                  query=q, message=dec.message)
            res = self.add_to_cart(line, dec.best)
            res.query = q
            if res.status != ADDED:
                res.candidates = dec.shortlist or res.candidates
            return res
        except CheckoutRefused as e:
            return LineResult(line, ERROR, message=str(e))
        except Exception as e:      # một dòng hỏng không được làm hỏng cả lượt
            return LineResult(line, ERROR, message=f"{type(e).__name__}: {e}")

    def run(self, lines: Iterable[BomLine]) -> list[LineResult]:
        out: list[LineResult] = []
        lines = list(lines)
        for i, line in enumerate(lines):
            if i:
                lo, hi = self.delay
                self.sleep(self.rand(lo, hi) if hi > lo else lo)
            res = self.add_line(line)
            self._say(res.summary())
            out.append(res)
        return out


def add_bom_to_cart(page: Any, lines: Iterable[BomLine], base_url: str,
                    **kwargs: Any) -> list[LineResult]:
    """Bỏ cả BOM vào giỏ của một shop. KHÔNG thanh toán — người dùng tự làm."""
    return CartFiller(page, base_url, **kwargs).run(lines)


def summarize(results: Iterable[LineResult]) -> dict[str, int]:
    counts = {ADDED: 0, NOT_FOUND: 0, UNCERTAIN: 0, ERROR: 0, OUT_OF_STOCK: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def shop_option(base_url: str, key: str, shops: Any = None,
                default: Any = False) -> Any:
    """Tuỳ chọn `key` của shop có cùng tên miền với `base_url` trong
    data/shops.json (hoặc dict `shops` id -> Shop đã nạp). Không thấy -> default.
    """
    from urllib.parse import urlsplit

    def host(u: str) -> str:
        h = urlsplit(u if "://" in u else "https://" + u).netloc.lower()
        return h[4:] if h.startswith("www.") else h

    if shops is None:
        try:
            from ..config import load_shops
            shops = load_shops()
        except Exception:
            return default
    want = host(base_url or "")
    for s in (shops.values() if isinstance(shops, dict) else shops):
        if getattr(s, "url", "") and host(s.url) == want:
            return getattr(s, key, default)
    return default
