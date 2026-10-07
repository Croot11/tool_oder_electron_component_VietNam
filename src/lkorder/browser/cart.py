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

# Đường dẫn trang giỏ theo nền tảng (thử lần lượt tới khi gặp trang giỏ thật).
CART_PATHS: dict[str, tuple[str, ...]] = {
    "haravan": ("/cart",),
    "shopify": ("/cart",),
    "sapo": ("/cart",),
    "woo": ("/cart/", "/gio-hang/"),
}
DEFAULT_CART_PATHS = ("/cart", "/gio-hang")

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


class CartClearError(RuntimeError):
    """Làm trống giỏ không thành công (còn món, hoặc không thấy nút xoá)."""


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
    # Chỉ dùng trên TRANG GIỎ khi người dùng chọn "Làm trống giỏ". Danh sách
    # cho phép rõ ràng: nút xoá một món / xoá tất cả. Không có nút thanh toán.
    clear_button: tuple[str, ...] = (
        "button:has-text('Xoá tất cả')",
        "button:has-text('Xóa tất cả')",
        "a:has-text('Xoá tất cả')",
        "a:has-text('Xóa tất cả')",
        "button:has-text('Xóa giỏ hàng')",
        "a:has-text('Xóa giỏ hàng')",
        "button:has-text('Xoá giỏ hàng')",
        "a:has-text('Xoá giỏ hàng')",
        "button:has-text('Làm trống giỏ')",
        "a:has-text('Làm trống giỏ')",
        "button:has-text('Clear cart')",
        "a:has-text('Clear cart')",
        "button:has-text('Empty cart')",
        "a:has-text('Empty cart')",
        "button[name='clear']",
        "a.clear-cart",
        "button.clear-cart",
        "#clear-cart",
    )
    remove_button: tuple[str, ...] = (
        "a.remove",
        "button.remove",
        "a.remove_from_cart_button",
        "td.product-remove a",
        "a.product-remove",
        "a.cart__remove",
        "button.cart__remove",
        ".cart-item__remove",
        "a.cart-remove",
        "button.cart-remove",
        "a.btn-remove",
        "button.btn-remove",
        "a.remove-item",
        "button.remove-item",
        "[data-action='remove']",
        "[data-cart-remove]",
        "a:has-text('Xoá')",
        "a:has-text('Xóa')",
        "button:has-text('Xoá')",
        "button:has-text('Xóa')",
        "a:has-text('Remove')",
        "button:has-text('Remove')",
        "a[aria-label*='xóa' i]",
        "button[aria-label*='xóa' i]",
        "a[aria-label*='remove' i]",
        "button[aria-label*='remove' i]",
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


# ------------------------------------------------------------ đọc trang giỏ


@dataclass
class CartItem:
    """Một dòng trong trang giỏ: tên + số lượng (None nếu không đọc được)."""

    name: str
    qty: Optional[int] = None

    def label(self) -> str:
        return f"{self.name} × {self.qty}" if self.qty else self.name


@dataclass
class CartState:
    """Giỏ hàng hiện tại, đọc từ biểu tượng giỏ và trang giỏ."""

    count: Optional[int] = None          # số trên biểu tượng giỏ
    items: list[CartItem] = field(default_factory=list)
    empty_confirmed: bool = False        # trang giỏ ghi rõ "giỏ trống"
    source: str = ""                     # trang giỏ đã đọc
    error: str = ""

    @property
    def n(self) -> Optional[int]:
        """Số món trong giỏ; None = không xác định được."""
        vals = []
        if self.count is not None:
            vals.append(self.count)
        if self.items:
            vals.append(len(self.items))
        if vals:
            return max(vals)
        return 0 if self.empty_confirmed else None

    @property
    def known(self) -> bool:
        return self.n is not None

    @property
    def is_empty(self) -> bool:
        return self.n == 0

    def message(self) -> str:
        n = self.n
        if n is None:
            return "Không xác định được giỏ có đồ hay không"
        return f"Giỏ đang có {n} món"

    def to_json(self) -> dict:
        return {"known": self.known, "count": self.n, "message": self.message(),
                "error": self.error,
                "items": [{"name": i.name, "qty": i.qty} for i in self.items]}


_EMPTY_CART_TEXTS = (
    "gio hang trong", "gio hang cua ban dang trong", "gio hang cua ban trong",
    "khong co san pham nao trong gio", "chua co san pham nao trong gio",
    "gio hang cua ban chua co san pham", "gio hang chua co san pham",
    "your cart is empty", "cart is currently empty", "cart is empty",
    "no items in your cart", "no products in the cart",
)

_QTY_NAME = re.compile(r"qty|quantity|so[-_]?luong|^sl$|^updates")
_NOT_QTY_TYPES = ("hidden", "submit", "button", "checkbox", "radio", "image")


def cart_page_says_empty(html: str) -> bool:
    """Trang có dòng kiểu 'Giỏ hàng trống' / 'Your cart is empty' không."""
    t = _fold(parse_html(html).text())
    return any(k in t for k in _EMPTY_CART_TEXTS)


def _is_qty_input(n: Node) -> bool:
    if n.tag != "input" or n.get("type").lower() in _NOT_QTY_TYPES:
        return False
    names = [n.get("name").lower(), n.id] + n.classes
    return any(_QTY_NAME.search(x) for x in names if x)


# --- nhận diện nút xoá (không bao giờ là nút thanh toán)

_CLEAR_WORDS = re.compile(
    r"xoa (?:tat ca|het|toan bo|gio)|lam trong gio|clear (?:the )?cart|"
    r"empty (?:the |your )?cart|remove all|delete all")
_REMOVE_WORDS = re.compile(
    r"\b(?:xoa|remove|delete)\b|bo khoi gio|bo san pham")
_REMOVE_ATTR = re.compile(r"remove|delete|(?:^|[-_ ])del(?:$|[-_ ])|xoa|clear[-_]?cart")
_REMOVE_HREF = re.compile(
    r"quantity=0|[?&/](?:remove|delete)|remove[-_]?(?:item|from)|action=remove|"
    r"/xoa|delete[-_]?item")
_X_LABELS = ("×", "x", "✕", "✖", "╳", "✗")


def _attrs_text(attrs: Iterable[str]) -> str:
    return " ".join(a for a in attrs if a).lower()


def control_kind(text: str = "", attrs: str = "", href: str = "") -> str:
    """'remove' (xoá một món), 'clear' (xoá tất cả) hoặc '' (không phải nút xoá).

    Nút trông như thanh toán/đặt hàng luôn trả ''.
    """
    flat = re.sub(r"[-_]+", " ", attrs or "")
    if is_checkout_like(text, href) or is_checkout_like(flat)             or "checkout" in (attrs or "").lower():
        return ""
    label = _fold((text or "").strip())
    if _CLEAR_WORDS.search(label):
        return "clear"
    if (text or "").strip().lower() in _X_LABELS or _REMOVE_WORDS.search(label):
        return "remove"
    if _REMOVE_ATTR.search((attrs or "").lower()) or _REMOVE_HREF.search(
            (href or "").lower()):
        return "remove"
    return ""


def _node_label(n: Node) -> str:
    return n.text() or n.get("value") or n.get("aria-label") or n.get("title")


def _node_kind(n: Node) -> str:
    is_input = n.tag == "input" and n.get("type").lower() in ("submit", "button")
    if n.tag not in ("a", "button") and not is_input:
        return ""
    attrs = _attrs_text(n.classes + [n.id, n.get("name"), n.get("data-action"),
                                    n.get("onclick"), n.get("title"),
                                    n.get("aria-label")])
    return control_kind(_node_label(n), attrs, n.get("href"))


def cart_controls(html: str) -> dict[str, list[str]]:
    """Các nút xoá nhận diện được trên trang giỏ: {'remove': [...], 'clear': [...]}
    (giá trị là chữ trên nút). Nút thanh toán không bao giờ lọt vào đây."""
    out: dict[str, list[str]] = {"remove": [], "clear": []}
    for n in parse_html(html).iter():
        kind = _node_kind(n)
        if kind:
            out[kind].append(_node_label(n))
    return out


def _item_name(row: Node) -> str:
    for a in row.find_all(lambda n: n.tag == "a"):
        t = a.text()
        if len(t) >= 2 and not _node_kind(a) and not is_checkout_like(t, a.get("href")):
            return t
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        h = row.find(lambda n, tag=tag: n.tag == tag)
        if h is not None and h.text():
            return h.text()
    img = row.find(lambda n: n.tag == "img" and n.get("alt").strip())
    return img.get("alt").strip() if img is not None else ""


def _item_row(inp: Node) -> Optional[Node]:
    """Phần tử chứa đúng MỘT dòng giỏ (một ô số lượng) và có tên sản phẩm."""
    for a in inp.ancestors():
        if a.tag in ("body", "html", "#root", "form", "table", "tbody", "ul", "ol"):
            break
        if len(a.find_all(_is_qty_input)) > 1:
            break
        if _item_name(a):
            return a
    return None


def read_cart_items(html: str) -> list[CartItem]:
    """Danh sách món (tên + số lượng) trên trang giỏ. Mỗi dòng giỏ = một ô số
    lượng; tên lấy từ link sản phẩm cùng dòng. Không đọc được thì []."""
    root = parse_html(html)
    items: list[CartItem] = []
    seen: set[tuple[str, Optional[int]]] = set()
    for inp in root.find_all(_is_qty_input):
        row = _item_row(inp)
        name = _item_name(row) if row is not None else ""
        v = inp.get("value").strip()
        qty = int(float(v)) if re.fullmatch(r"\d+(?:\.\d+)?", v) else None
        key = (name, qty)
        if name and key in seen:        # mini-cart lặp lại cùng dòng
            continue
        seen.add(key)
        items.append(CartItem(name or "(không rõ tên)", qty))
    return items


def items_removed(before: list[CartItem], after: list[CartItem]) -> list[CartItem]:
    """Các món có trong `before` mà không còn trong `after` (so theo tên + SL)."""
    left = [(i.name, i.qty) for i in after]
    gone = []
    for it in before:
        key = (it.name, it.qty)
        if key in left:
            left.remove(key)
        else:
            gone.append(it)
    return gone


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
                 no_image_out_of_stock: bool = False,
                 cart_url: str | None = None) -> None:
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
        # Trang giỏ (thử lần lượt) để đọc/làm trống giỏ.
        self.cart_urls: tuple[str, ...] = (cart_url,) if cart_url else tuple(
            self.base_url + p for p in CART_PATHS.get(platform, DEFAULT_CART_PATHS))
        self._cart_url = ""
        # Mốc số món trong giỏ: đọc ở bước kiểm tra giỏ rồi cập nhật theo từng
        # lần thêm. None = chưa có mốc, khi đó không được tin vào số trên giỏ.
        self.cart_floor: Optional[int] = None

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

    # -- đọc / làm trống giỏ

    def _count_of(self, html: str) -> Optional[int]:
        """Số trên biểu tượng giỏ của trang `html` (shop có bộ riêng thì ghi đè)."""
        return read_cart_count(html)

    def _state_of(self, html: str, url: str = "") -> CartState:
        return CartState(count=self._count_of(html), items=read_cart_items(html),
                         empty_confirmed=cart_page_says_empty(html), source=url)

    def inspect_cart(self) -> CartState:
        """Giỏ hiện tại: số trên biểu tượng giỏ ở trang đang mở + danh sách món
        đọc từ trang giỏ. Chỉ đọc, không bấm gì. Mở trang giỏ nên trang hiện tại
        sẽ đổi."""
        state = CartState()
        try:
            state.count = self._count_of(self.page.content())
        except Exception:
            pass
        errors: list[str] = []
        for url in self.cart_urls:
            try:
                html = self._goto(url)
            except Exception as e:
                errors.append(f"{url}: {type(e).__name__}: {e}")
                continue
            page = self._state_of(html, url)
            if page.count is not None:
                state.count = max(state.count or 0, page.count)
            if page.items or page.empty_confirmed:        # đúng là trang giỏ
                state.items, state.empty_confirmed = page.items, page.empty_confirmed
                state.source = self._cart_url = url
                break
        if not state.source and errors:
            state.error = "; ".join(errors)
        return state

    def _reload_cart(self) -> CartState:
        return self._state_of(self._goto(self._cart_url), self._cart_url)

    @staticmethod
    def _attr(handle: Any, name: str) -> str:
        try:
            return handle.get_attribute(name) or ""
        except Exception:
            return ""

    def _control_attrs(self, handle: Any) -> str:
        return " ".join(self._attr(handle, a) for a in (
            "class", "id", "name", "onclick", "data-action", "title", "aria-label"))

    def _click_cart_control(self, handle: Any) -> str:
        """Bấm một nút xoá trên trang giỏ. CHỈ bấm nút nhận diện là xoá; nút
        có vẻ thanh toán thì ném CheckoutRefused, nút lạ thì CartClearError.
        Trả về 'remove' hoặc 'clear'."""
        text, href = self._label(handle)
        attrs = self._control_attrs(handle)
        if is_checkout_like(text, href) or is_checkout_like(
                re.sub(r"[-_]+", " ", attrs)) or "checkout" in attrs.lower():
            raise CheckoutRefused(
                f"Từ chối bấm nút có vẻ là thanh toán khi làm trống giỏ: {text!r}")
        kind = control_kind(text, attrs, href)
        if not kind:
            raise CartClearError(f"Không bấm nút lạ trên trang giỏ: {text!r}")
        self._safe_click(handle)
        return kind

    def clear_cart(self, log: Callable[[str], None] | None = None) -> CartState:
        """Xoá hết món trong giỏ, trả về giỏ đọc lại sau khi xoá (người gọi phải
        kiểm tra `is_empty`). Chỉ bấm nút xoá theo `selectors.clear_button` /
        `remove_button`; mọi lần bấm đi qua `_click_cart_control` -> `_safe_click`
        nên không bao giờ bấm Thanh toán/Đặt hàng/Mua ngay. Ghi log từng món đã xoá.
        """
        log = log or self._say
        cur = self.inspect_cart()
        if cur.is_empty:
            return cur
        if not self._cart_url:
            raise CartClearError("không mở được trang giỏ hàng để xoá")
        handler = self._accept_dialogs()
        try:
            btn = self._first(self.sel.clear_button)
            if btn is not None:
                self._click_cart_control(btn)
                self.sleep(self.poll)
                new = self._reload_cart()
                self._log_removed(log, cur, new)
                cur = new
            budget = max(cur.n or 0, len(cur.items)) + 3
            stuck = 0
            while not cur.is_empty and budget > 0:
                budget -= 1
                btn = self._first(self.sel.remove_button)
                if btn is None:
                    break
                self._click_cart_control(btn)
                self.sleep(self.poll)
                new = self._reload_cart()
                gone = self._log_removed(log, cur, new)
                stuck = 0 if gone else stuck + 1
                cur = new
                if stuck >= 2:
                    break
        finally:
            self._drop_dialogs(handler)
        return self.inspect_cart()

    @staticmethod
    def _log_removed(log: Callable[[str], None], before: CartState,
                     after: CartState) -> int:
        gone = items_removed(before.items, after.items)
        for it in gone:
            log(f"Đã xoá khỏi giỏ: {it.label()}")
        if not gone and (before.n or 0) > (after.n or 0):
            log(f"Đã xoá {(before.n or 0) - (after.n or 0)} món khỏi giỏ "
                f"(không đọc được tên)")
            return 1
        return len(gone)

    def _accept_dialogs(self) -> Any:
        """Trang giỏ hay hỏi 'Bạn có chắc muốn xoá?' bằng hộp thoại: đồng ý, trừ
        hộp thoại nhắc tới thanh toán."""
        def handler(d: Any) -> None:
            try:
                if is_checkout_like(getattr(d, "message", "") or ""):
                    d.dismiss()
                else:
                    d.accept()
            except Exception:
                pass
        on = getattr(self.page, "on", None)
        if on is None:
            return None
        try:
            on("dialog", handler)
        except Exception:
            return None
        return handler

    def _drop_dialogs(self, handler: Any) -> None:
        off = getattr(self.page, "remove_listener", None)
        if handler is not None and off is not None:
            try:
                off("dialog", handler)
            except Exception:
                pass

    # -- thêm vào giỏ

    def _count_confirms(self, before: Optional[int], after: Optional[int]) -> str:
        """Số trên giỏ có chứng tỏ đã thêm không. Trả lý do, '' nếu không.

        Mốc: số đọc trước khi bấm; không có thì số đọc ở bước kiểm tra giỏ
        (`cart_floor`). Không có mốc nào thì KHÔNG tin vào số trên giỏ, vì giỏ có
        thể đã có đồ từ trước.
        """
        ref = before if before is not None else self.cart_floor
        if ref is None or after is None or after <= ref:
            return ""
        if self.cart_floor is not None:
            self.cart_floor = after
        if before is not None:
            return f"giỏ tăng {before} -> {after}"
        return f"giỏ tăng {ref} -> {after} (so với lúc kiểm tra giỏ)"

    def _sync_floor(self, before: Optional[int]) -> None:
        """Số đọc được trước khi bấm cũng là mốc mới (chỉ nâng, không hạ)."""
        if before is not None and self.cart_floor is not None:
            self.cart_floor = max(self.cart_floor, before)

    def _note_cart(self, after: Optional[int], added: bool) -> None:
        """Cập nhật mốc số món sau một lần thêm không xác nhận được bằng số.
        `added`: đã xác nhận bằng cách khác. Mốc chỉ được nâng, không hạ, để
        không báo thêm nhầm ở lần sau."""
        if self.cart_floor is None:
            return
        floor = self.cart_floor + (1 if added else 0)
        self.cart_floor = max(floor, after) if after is not None else floor

    def _wait_added(self, before: Optional[int], product_url: str) -> tuple[bool, str]:
        self._sync_floor(before)
        deadline = self.clock() + self.confirm_timeout
        while True:
            html = self.page.content()
            after = read_cart_count(html)
            why = self._count_confirms(before, after)
            if why:
                return True, why
            if has_added_message(html):
                self._note_cart(after, True)
                return True, "shop báo đã thêm vào giỏ"
            url = (getattr(self.page, "url", "") or "").lower()
            if url != product_url.lower() and re.search(r"/cart\b|/gio-hang", url):
                self._note_cart(after, True)
                return True, "đã chuyển sang trang giỏ hàng"
            if self.clock() >= deadline:
                self._note_cart(after, False)
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
