"""Bóc tách trang kết quả tìm kiếm của shop và chấm điểm khớp với dòng BOM.

Toàn bộ module này là hàm thuần trên chuỗi HTML — không đụng tới trình duyệt —
nên kiểm thử được bằng HTML mẫu (xem tests/test_cart.py).

Hai việc:

1. `parse_search_results(html, base_url)`  HTML trang tìm kiếm -> danh sách
   `Candidate` (tên, link, giá, còn hàng, số con mỗi gói).
2. `score_title(query, title)` / `pick_best(line, candidates)`  chấm điểm từng
   ứng viên bằng `normalize.py` và quyết định: khớp / chưa chắc / không có.

Nguyên tắc chấm điểm: thà báo "chưa chắc" còn hơn bỏ nhầm hàng vào giỏ.
Với linh kiện thụ động, sai giá trị (10K vs 100K, 100nF vs 10nF) là loại
thẳng — điểm 0 — chứ không chỉ trừ điểm.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable, Iterator, Optional
from urllib.parse import urljoin

from ..models import BomLine
from ..normalize import (
    KIND_MAIN_UNIT, KIND_PHRASES, MPN_RE, NOISE_WORDS, KIND_TOKENS, format_eng,
    parse, parse_value, similarity, strip_accents, tokenize,
)

# Ngưỡng mặc định
ACCEPT_SCORE = 0.8      # >= ngưỡng này mới tự bỏ vào giỏ
CONSIDER_SCORE = 0.4    # >= ngưỡng này thì đưa ra làm ứng viên "chưa chắc"
MARGIN = 0.05           # ứng viên nhất phải hơn ứng viên nhì ít nhất chừng này
SURE_SCORE = 0.95       # điểm gần tuyệt đối: hoà điểm cũng chấp nhận

# ------------------------------------------------------------ DOM tối giản

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_SKIP_TEXT = {"script", "style", "noscript", "template"}


class Node:
    """Một phần tử HTML. Đủ dùng để duyệt cây, không cố làm trình duyệt."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None,
                 parent: "Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list["Node | str"] = []
        self.parent = parent

    # -- thuộc tính
    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name) or default

    @property
    def classes(self) -> list[str]:
        return self.get("class").lower().split()

    @property
    def id(self) -> str:
        return self.get("id").lower()

    # -- duyệt
    def iter(self) -> Iterator["Node"]:
        """Duyệt mọi phần tử con cháu (không gồm chính nó), theo thứ tự tài liệu."""
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def find_all(self, pred) -> list["Node"]:
        return [n for n in self.iter() if pred(n)]

    def find(self, pred) -> "Node | None":
        for n in self.iter():
            if pred(n):
                return n
        return None

    def ancestors(self) -> Iterator["Node"]:
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    def text(self, skip: Iterable[str] = ()) -> str:
        """Chữ hiển thị, đã gộp khoảng trắng. `skip`: bỏ qua các thẻ này."""
        skip_tags = _SKIP_TEXT | set(skip)
        parts: list[str] = []

        def walk(n: Node) -> None:
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in skip_tags:
                    walk(c)

        walk(self)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def raw_text(self) -> str:
        """Chữ thô (kể cả trong <script>) — dùng cho JSON-LD."""
        return "".join(c if isinstance(c, str) else c.raw_text()
                       for c in self.children)

    def __repr__(self) -> str:   # pragma: no cover - chỉ để debug
        return f"<{self.tag} {self.attrs}>"


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        # Đóng tới thẻ khớp gần nhất; thẻ đóng lạc thì bỏ qua (HTML thật hay hỏng).
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    b = _Builder()
    try:
        b.feed(html or "")
        b.close()
    except Exception:       # HTML quá hỏng: dùng được tới đâu hay tới đó
        pass
    return b.root


# ------------------------------------------------------------ tiện ích chữ


def _fold(s: str) -> str:
    """Bỏ dấu + chữ thường, để dò cụm từ tiếng Việt."""
    return strip_accents(s or "").lower()


_PRICE_RE = re.compile(r"\d{1,3}(?:[.,\s]\d{3})+(?!\d)|\d+")


def parse_price(text: str) -> int:
    """'12.500₫' -> 12500 ; '1,250,000 đ' -> 1250000 ; 'Liên hệ' -> 0."""
    if not text:
        return 0
    m = _PRICE_RE.search(text.replace("\xa0", " "))
    if not m:
        return 0
    digits = re.sub(r"\D", "", m.group(0))
    return int(digits) if digits else 0


_PACK_PATTERNS = [
    # "gói 100 con", "túi 50c", "bịch 100 cái", "set 10 chiếc", "cuộn 5000"
    re.compile(r"\b(?:goi|tui|bich|set|cuon|hop|vi|bo|lot|pack)\s*(?:gom\s*)?"
               r"(\d+)\s*(?:con|cai|chiec|pcs|pc|c)?\b"),
    # "(100 con)", "100 cái/gói", "100pcs"
    re.compile(r"\b(\d+)\s*(?:con|cai|chiec|pcs)\b"),
    # "x100" ở cuối tên
    re.compile(r"\bx\s*(\d+)\s*$"),
]


def detect_pack(title: str) -> int:
    """Số linh kiện trong MỘT đơn vị bán, đoán từ tên. Không thấy thì 1."""
    t = _fold(title)
    for pat in _PACK_PATTERNS:
        m = pat.search(t)
        if m:
            n = int(m.group(1))
            if 1 < n <= 100_000:
                return n
    return 1


_OUT_OF_STOCK = ("het hang", "tam het", "out of stock", "sold out", "outofstock",
                 "ngung kinh doanh")
_OOS_CLASSES = re.compile(r"sold[-_]?out|out[-_]?of[-_]?stock|outofstock|het[-_]?hang")


# ------------------------------------------------------------ ứng viên


@dataclass
class Candidate:
    """Một sản phẩm đọc được trên trang kết quả tìm kiếm."""

    title: str
    url: str
    price: int = 0
    in_stock: Optional[bool] = None     # None = không rõ
    pack: int = 1
    score: float = 0.0
    reason: str = ""

    def __str__(self) -> str:
        return f"{self.title} ({self.score:.2f}) {self.url}"


_CARD_CLASS = re.compile(
    r"^(?:product|type-product|productitem|product[-_]?(?:item|card|block|box|"
    r"small|loop|grid[-_]?item|miniature|thumb[-_]?wrap|layout|wrapper|col|tile)"
    r"|item[-_]?product|pro[-_]?loop|product[-_]?inner|card[-_]?product)$"
)
_TITLE_CLASS = re.compile(r"(?:^|[-_])(?:title|name)(?:$|[-_])|^name$|^title$")
_PRICE_CLASS = re.compile(r"price|gia")
_OLD_PRICE_CLASS = re.compile(r"old|compare|regular|original|del|was|before|strike")
_PRODUCT_HREF = re.compile(r"/(?:products?|san-pham|sp|p|shop|item)/|\.html?(?:$|\?)"
                           r"|[?&](?:product|p)=")


def _is_card(n: Node) -> bool:
    if n.tag in ("html", "body", "#root"):
        return False
    if "data-product-id" in n.attrs or "data-product_id" in n.attrs:
        return n.tag != "a" and n.tag != "button"
    if "product" in n.get("itemtype").lower():
        return True
    return any(_CARD_CLASS.match(c) for c in n.classes)


def _good_href(href: str) -> bool:
    h = href.strip().lower()
    return bool(h) and not h.startswith(("#", "javascript:", "mailto:", "tel:"))


def _card_title(card: Node) -> tuple[str, Node | None]:
    """(tên, phần tử chứa tên) của một thẻ sản phẩm."""
    for n in card.iter():
        if n.tag in ("h1", "h2", "h3", "h4", "h5") or any(
                _TITLE_CLASS.search(c) for c in n.classes):
            if any(_PRICE_CLASS.search(c) for c in n.classes):
                continue
            t = n.text()
            if t:
                return t, n
    for n in card.iter():
        if n.tag == "a" and n.get("title").strip():
            return n.get("title").strip(), n
    for n in card.iter():
        if n.tag == "a" and _good_href(n.get("href")) and n.text():
            return n.text(), n
    img = card.find(lambda n: n.tag == "img" and n.get("alt").strip())
    return (img.get("alt").strip(), img) if img else ("", None)


def _card_url(card: Node, title_node: Node | None) -> str:
    if title_node is not None:
        if title_node.tag == "a" and _good_href(title_node.get("href")):
            return title_node.get("href")
        a = title_node.find(lambda n: n.tag == "a" and _good_href(n.get("href")))
        if a:
            return a.get("href")
        for anc in title_node.ancestors():
            if anc is card:
                break
            if anc.tag == "a" and _good_href(anc.get("href")):
                return anc.get("href")
    if card.tag == "a" and _good_href(card.get("href")):
        return card.get("href")
    a = card.find(lambda n: n.tag == "a" and _good_href(n.get("href"))
                  and not n.get("href").lower().count("add-to-cart"))
    return a.get("href") if a else ""


def _card_price(card: Node) -> int:
    nodes = card.find_all(lambda n: any(_PRICE_CLASS.search(c) for c in n.classes))
    # Ưu tiên giá đang bán: <ins>, rồi phần tử giá không mang dấu "giá cũ".
    for n in nodes:
        ins = n.find(lambda x: x.tag == "ins")
        if ins:
            p = parse_price(ins.text())
            if p:
                return p
    for n in nodes:
        if any(_OLD_PRICE_CLASS.search(c) for c in n.classes):
            continue
        p = parse_price(n.text(skip=("del", "s", "strike")))
        if p:
            return p
    for n in card.find_all(lambda x: x.get("itemprop") == "price"):
        p = parse_price(n.get("content") or n.text())
        if p:
            return p
    return 0


def _card_in_stock(card: Node) -> Optional[bool]:
    for n in [card, *card.iter()]:
        if any(_OOS_CLASSES.search(c) for c in n.classes):
            return False
    t = _fold(card.text())
    if any(k in t for k in _OUT_OF_STOCK):
        return False
    if "con hang" in t or "in stock" in t:
        return True
    return None


def _from_cards(root: Node, base_url: str) -> list[Candidate]:
    cards = root.find_all(_is_card)
    card_ids = {id(c) for c in cards}
    # Thẻ chứa thẻ khác là khung danh sách, không phải một sản phẩm.
    leaves = [c for c in cards
              if not any(id(d) in card_ids for d in c.iter())]
    out: list[Candidate] = []
    for card in leaves:
        title, tnode = _card_title(card)
        url = _card_url(card, tnode)
        if not title or not url:
            continue
        out.append(Candidate(
            title=title, url=urljoin(base_url, url), price=_card_price(card),
            in_stock=_card_in_stock(card), pack=detect_pack(title),
        ))
    return out


def _walk_ld(data) -> Iterator[dict]:
    if isinstance(data, dict):
        yield data
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            v = data.get(key)
            if v is not None:
                yield from _walk_ld(v)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_ld(item)


def _from_jsonld(root: Node, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for s in root.find_all(lambda n: n.tag == "script"
                           and "ld+json" in n.get("type").lower()):
        try:
            data = json.loads(s.raw_text().strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _walk_ld(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any(str(t).lower() == "product" for t in types):
                continue
            title = str(node.get("name") or "").strip()
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                offers = {}
            url = str(node.get("url") or offers.get("url") or "")
            if not title or not url:
                continue
            avail = str(offers.get("availability") or "").lower()
            stock = False if "outofstock" in avail.replace("_", "") else (
                True if "instock" in avail else None)
            out.append(Candidate(
                title=title, url=urljoin(base_url, url),
                price=parse_price(str(offers.get("price")
                                      or offers.get("lowPrice") or "")
                                  .split(".")[0]),
                in_stock=stock, pack=detect_pack(title),
            ))
    return out


def _from_links(root: Node, base_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    for a in root.find_all(lambda n: n.tag == "a"):
        href = a.get("href")
        if not _good_href(href) or not _PRODUCT_HREF.search(href.lower()):
            continue
        if any(anc.tag in ("nav", "header", "footer") for anc in a.ancestors()):
            continue
        title = a.get("title").strip() or a.text()
        if len(title) < 3:
            continue
        out.append(Candidate(title=title, url=urljoin(base_url, href),
                             pack=detect_pack(title)))
    return out


def parse_search_results(html: str, base_url: str = "") -> list[Candidate]:
    """Đọc danh sách sản phẩm trên trang kết quả tìm kiếm.

    Thử lần lượt: thẻ sản phẩm (class product-item/product-card/li.product...),
    rồi dữ liệu JSON-LD, cuối cùng là các link trông giống link sản phẩm.
    Trả về danh sách không trùng URL, giữ thứ tự của shop.
    """
    root = parse_html(html)
    found: list[Candidate] = []
    for strategy in (_from_cards, _from_jsonld, _from_links):
        found = strategy(root, base_url)
        if found:
            break
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in found:
        key = c.url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ------------------------------------------------------------ chấm điểm


def _flat(t: str) -> str:
    return t.replace("-", "").replace(".", "")


def _main_values(tokens: Iterable[str], kind: str) -> list[float]:
    """Mọi giá trị CHÍNH (ohm/F/H/Hz) có trong tên, theo đúng cách normalize hiểu."""
    main_unit = KIND_MAIN_UNIT.get(kind, "")
    if not main_unit:
        return []
    vals: list[float] = []
    for t in tokens:
        pv = parse_value(t)
        if pv is None:
            continue
        mag, unit = pv
        has_letter = any(c.isalpha() for c in t)
        if unit == main_unit or (not unit and has_letter):
            vals.append(mag)
    return vals


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-15)


def _score_mpn(mpn: str, query: str, title: str) -> tuple[float, str]:
    pt = parse(title)
    q = _flat(mpn)
    toks = [_flat(t) for t in pt.tokens]
    if q in toks:
        return 1.0, "trùng mã"
    # Mã bị tách bởi khoảng trắng: "ESP32 WROOM-32" vs "ESP32-WROOM-32"
    for i in range(len(toks)):
        joined = ""
        for j in range(i, min(i + 4, len(toks))):
            joined += toks[j]
            if joined == q:
                return 0.95, "trùng mã (viết tách)"
            if len(joined) > len(q):
                break
    # Hậu tố đóng gói/nhiệt độ: NE555 -> NE555P, STM32F103C8T6 -> ...TR.
    # Hậu tố có chữ số (LM35 -> LM358) là con khác, không tính.
    for t in toks:
        if t.startswith(q) and t != q:
            extra = t[len(q):]
            if extra.isalpha() and len(extra) <= 2:
                return 0.82, f"mã có hậu tố '{extra}'"
            if extra.isalpha() and len(extra) <= 4:
                return 0.6, f"mã có hậu tố dài '{extra}'"
    # Tên shop ghi thiếu đuôi: hỏi "STM32F103C8T6", shop ghi "STM32F103"
    for t in toks:
        if len(t) >= 4 and MPN_RE.match(t) and q.startswith(t):
            return 0.5, f"mã ngắn hơn ({t})"
    return min(similarity(query, title), 0.45), "không thấy mã"


def _score_passive(pq, query: str, title: str) -> tuple[float, str]:
    pt = parse(title)
    if pt.kind and pt.kind != "mpn" and pt.kind != pq.kind:
        return 0.0, f"khác loại ({pt.kind})"
    vals = _main_values(pt.tokens, pq.kind)
    if not vals:
        return min(similarity(query, title), 0.4), "không thấy giá trị"
    if not any(_same(v, pq.value) for v in vals):
        shown = ", ".join(sorted({format_eng(v) for v in vals}))
        return 0.0, f"sai giá trị ({shown} ≠ {format_eng(pq.value)})"

    reasons = ["đúng giá trị"]
    score = 0.9 if pt.kind == pq.kind else 0.8
    distinct = {round(v, 15) for v in vals}
    if len(distinct) > 1:
        score = 0.6
        reasons.append("tên có nhiều giá trị (bộ kit?)")

    for unit, want in pq.attrs.items():
        got = pt.attrs.get(unit)
        if got is None:
            continue
        if got == want:
            score += 0.03
        else:
            score -= 0.25
            reasons.append(f"lệch {unit}: {got} ≠ {want}")
    if pq.package and pt.package:
        if pq.package == pt.package:
            score += 0.05
        else:
            score -= 0.25
            reasons.append(f"lệch chân: {pt.package} ≠ {pq.package}")
    return max(0.0, min(1.0, score)), "; ".join(reasons)


def _score_generic(pq, query: str, title: str) -> tuple[float, str]:
    pt = parse(title)
    if pq.key and pq.key == pt.key:
        return 1.0, "trùng khoá"
    sim = similarity(query, title)
    q_sig = {t for t in pq.tokens if t not in NOISE_WORDS}
    title_toks = set(pt.tokens)
    if q_sig:
        cover = len(q_sig & title_toks) / len(q_sig)
        if cover >= 1.0 and len(q_sig - KIND_TOKENS) == 0 and len(q_sig) == 1:
            # chỉ có mỗi chữ loại ("LED"): đủ từ nhưng quá chung chung
            cover = 0.6
        sim = max(sim, 0.85 * cover)
    if pq.package and pt.package and pq.package != pt.package:
        sim -= 0.25
    return max(0.0, min(1.0, sim)), f"giống {sim:.0%}"


def score_title(query: str, title: str) -> tuple[float, str]:
    """Điểm 0..1 cho việc `title` (tên trên shop) đúng là `query` (tên trong BOM).

    Trả về (điểm, lý do ngắn gọn).
    """
    pq = parse(query)
    if not pq.key or not title.strip():
        return 0.0, "rỗng"
    if pq.kind == "mpn":
        score, why = _score_mpn(pq.mpn, query, title)
        pt = parse(title)
        if score > 0 and pq.package and pt.package and pq.package != pt.package:
            score -= 0.25
            why += f"; lệch chân: {pt.package} ≠ {pq.package}"
        return max(0.0, score), why
    if pq.kind and pq.value is not None:
        return _score_passive(pq, query, title)
    return _score_generic(pq, query, title)


# ------------------------------------------------ giá trị + kích thước chân
#
# Dòng BOM là linh kiện rời có GIÁ TRỊ và KÍCH THƯỚC CHÂN (thường đến từ
# ibom.html: "Tụ 0603 100nF", "Tụ tantal 3216 10uF") thì hai thứ này là bắt
# buộc: sai giá trị hoặc sai kích thước là loại hẳn (điểm 0). Tên loại ("tụ",
# "điện trở") không bắt buộc, chỉ bị trừ điểm khi kết quả rõ ràng là loại khác
# (NTC, biến trở, module, kit...).

VALUE_KINDS = ("resistor", "capacitor", "inductor", "crystal", "fuse")
_VALUE_UNIT = {**KIND_MAIN_UNIT, "fuse": "a"}

# Kích thước chân dạng mã 4 số. Mã hệ mét quy về mã hệ inch cho dễ so:
# 1608 = 0603, 3216 = 1206 (tụ tantal case A)...
_METRIC_TO_INCH = {"1005": "0402", "1608": "0603", "2012": "0805", "3216": "1206",
                   "3225": "1210", "4532": "1812", "5025": "2010", "6332": "2512",
                   "2520": "1008", "5750": "2220"}
_KNOWN_SIZES = {
    "0201", "0402", "0603", "0805", "1206", "1210", "1806", "1812", "2010", "2220",
    "2512", "1008", *_METRIC_TO_INCH,
    "3528", "6032", "7343",                         # tụ tantal case B/C/D
    "2016", "5032", "7050",                         # thạch anh SMD
    "3015", "3020", "4018", "4020", "4030", "5020", "5040", "6028", "6045",
    "8040", "1040",                                 # cuộn cảm công suất
}
_TANTAL_CASE = {"a": "3216", "b": "3528", "c": "6032", "d": "7343", "e": "7343"}
_SIZE_RE = re.compile(r"(?<![\d.,])(\d{4})(?![\d.,])")
_HC49_RE = re.compile(r"hc-?49(?:[\s/-]*[su](?![a-z]))?(?:[\s/-]*(smd|smt))?")
_CASE_RE = re.compile(r"\b(?:case|size|type|loai|kich thuoc)\s*([a-e])\b")
_THT_WORDS = re.compile(r"\b(?:dip|tht|through hole|xuyen lo|radial|axial|"
                        r"chan cam|loai cam)\b")
_THT_ACCENT = re.compile(r"cắm|xuyên lỗ")

# Loại xung đột rõ với linh kiện rời: trừ điểm (không loại hẳn).
_CONFLICT_WORDS = re.compile(
    r"\b(ntc|ptc|thermistor|dien tro nhiet|bien tro|chiet ap|potentiometer|"
    r"trimmer|trimpot|module|kit|mach|board)\b")
_CONFLICT_PENALTY = 0.45

_PACK_BEFORE = {"goi", "tui", "bich", "set", "cuon", "hop", "lot", "pack", "x"}
_COUNT_AFTER = {"con", "cai", "chiec", "pcs", "pc", "c", "vien"}


def canon_size(code: str) -> str:
    code = code.lower()
    return _METRIC_TO_INCH.get(code, code)


@dataclass
class ValueSpec:
    """Giá trị + kích thước chân bắt buộc của một dòng BOM."""

    kind: str           # resistor / capacitor / inductor / crystal / fuse
    value: float        # đơn vị gốc: ohm / F / H / Hz / A
    size: str           # đã quy đổi (canon_size), vd "0603", "1206", "hc49"
    size_label: str     # như ghi trong BOM, vd "3216"
    attrs: dict[str, str] = field(default_factory=dict)


def _strip_note(text: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", text or "").strip()


def _sizes_in(text: str) -> tuple[list[str], list[str], bool]:
    """(kích thước đã quy đổi, kích thước như viết, có chữ 'cắm lỗ')."""
    t = _fold(text)
    canon: list[str] = []
    labels: list[str] = []

    def add(label: str, c: str = "") -> None:
        c = c or canon_size(label)
        if c not in canon:
            canon.append(c)
            labels.append(label)

    for m in _SIZE_RE.finditer(t):
        if m.group(1) in _KNOWN_SIZES:
            add(m.group(1))
    for m in _HC49_RE.finditer(t):
        add("HC49 SMD", "hc49smd") if m.group(1) else add("HC49", "hc49")
    if "tantal" in t:
        for m in _CASE_RE.finditer(t):
            add(_TANTAL_CASE[m.group(1)])
    tht = bool(_THT_WORDS.search(t) or _THT_ACCENT.search((text or "").lower()))
    return canon, labels, tht


def _detect_kind(text: str) -> str:
    t = _fold(text)
    if re.search(r"\b(?:cau chi|fuse|polyfuse|polyswitch)\b", t):
        return "fuse"
    t = re.sub(r"[^a-z0-9]+", " ", t)
    for phrase, kind in KIND_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return kind
    return ""


def _plausible(kind: str, mag: float) -> bool:
    """Giá trị không ghi đơn vị ('100n', '10k', '8m') có hợp với loại không."""
    if kind == "capacitor":
        return mag < 0.01
    if kind == "inductor":
        return mag < 1
    if kind == "resistor":
        return mag >= 0.01
    if kind == "crystal":
        return mag >= 1000
    return False


def _eia(code: str) -> float:
    return int(code[:-1]) * 10 ** int(code[-1])


def _values_in(text: str, kind: str) -> list[tuple[float, bool]]:
    """Mọi giá trị chính có trong `text`, hiểu theo loại `kind`.

    Trả về [(giá trị, có_chữ)]. `có_chữ` = False với số trần ("104", "10000")
    — hiểu theo mã EIA hoặc số thẳng, kém chắc chắn hơn.
    """
    main = _VALUE_UNIT.get(kind, "")
    toks: list[str] = []
    for t in tokenize(text):
        if "/" in t and parse_value(t) is None:
            toks.extend(p for p in t.split("/") if p)
        else:
            toks.append(t)
    out: list[tuple[float, bool]] = []
    for i, t in enumerate(toks):
        if t in _KNOWN_SIZES:
            continue
        pv = parse_value(t)
        if pv is None:
            continue
        mag, unit = pv
        if unit:
            if unit == main:
                out.append((mag, True))
            continue
        if any(c.isalpha() for c in t):
            if _plausible(kind, mag):
                out.append((mag, True))
            continue
        if not t.isdigit() or len(t) < 2 or t.startswith("0"):
            continue
        prev = toks[i - 1] if i else ""
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if prev in _PACK_BEFORE or nxt in _COUNT_AFTER:
            continue                    # "gói 100 con": số lượng, không phải giá trị
        if kind == "capacitor" and len(t) == 3:
            out.append((_eia(t) * 1e-12, False))
        elif kind == "inductor" and len(t) == 3:
            out.append((_eia(t) * 1e-6, False))
        elif kind == "resistor":
            out.append((float(t), False))
            if len(t) in (3, 4):
                out.append((float(_eia(t)), False))
        elif kind == "crystal" and len(t) >= 4:
            out.append((float(t), False))
    return out


def _line_value(text: str, kind: str) -> float | None:
    if kind == "fuse":
        vals = [v for v, lettered in _values_in(text, kind) if lettered]
        return vals[0] if vals else None
    p = parse(text)
    if p.kind == kind and p.value is not None:
        return p.value
    vals = [v for v, lettered in _values_in(text, kind) if lettered]
    return vals[0] if vals else None


def line_spec(line: BomLine) -> ValueSpec | None:
    """Giá trị + kích thước chân của dòng BOM, nếu đủ cả hai; không thì None."""
    text = _strip_note(line.raw or line.key)
    if not text:
        return None
    p = parse(text)
    if p.kind == "mpn" and not _HC49_RE.fullmatch(p.mpn):
        return None                     # có mã linh kiện: tìm theo mã
    kind = _detect_kind(text)
    if kind not in VALUE_KINDS:
        return None
    value = _line_value(text, kind)
    if value is None:
        return None
    canon, labels, _ = _sizes_in(text)
    if not canon:
        return None
    attrs = {u: v for u, v in p.attrs.items() if u != _VALUE_UNIT[kind]}
    return ValueSpec(kind, value, canon[0], labels[0].upper(), attrs)


def _fmt_num(x: float) -> str:
    return f"{round(x, 6):g}"


def _eu(num: str, letter: str) -> str:
    """'4.7', 'K' -> '4K7'. Số nguyên thì không có dạng châu Âu."""
    if "." not in num:
        return ""
    a, b = num.split(".", 1)
    return f"{a}{letter}{b}"


def _eia_code(value: float, base: float) -> str:
    """Mã EIA 3 số: 100nF (base pF) -> '104'. Không biểu diễn được thì ''."""
    x = value / base
    for exp in range(0, 10):
        m = x / 10 ** exp
        if 10 - 1e-6 <= m < 100 - 1e-6 and abs(m - round(m)) < 1e-6:
            return f"{int(round(m))}{exp}"
    return ""


def value_forms(kind: str, value: float) -> list[str]:
    """Các cách viết của CÙNG một giá trị, theo thứ tự nên thử khi tìm.

    100nF -> 100nF, 0.1uF, 104, 100n ; 10k -> 10k, 10000, 103 ;
    4.7k -> 4.7k, 4K7, 4700, 472 ; 22pF -> 22pF, 22p, 220.
    """
    out: list[str] = []
    if kind == "capacitor":
        if value >= 1e-6 * (1 - 1e-9):
            pre, scale = "u", 1e-6
        elif value >= 1e-9 * (1 - 1e-9):
            pre, scale = "n", 1e-9
        else:
            pre, scale = "p", 1e-12
        n = _fmt_num(value / scale)
        out.append(f"{n}{pre}F")
        if pre == "n" and value >= 1e-8 * (1 - 1e-9):
            out.append(f"{_fmt_num(value / 1e-6)}uF")
        elif pre == "n":
            out.append(f"{_fmt_num(value / 1e-12)}pF")
        out.append(_eia_code(value, 1e-12))
        out.append(f"{n}{pre}")
        out.append(_eu(n, pre))
    elif kind == "resistor":
        if value >= 1e6 * (1 - 1e-9):
            n, pre = _fmt_num(value / 1e6), "M"
        elif value >= 1e3 * (1 - 1e-9):
            n, pre = _fmt_num(value / 1e3), "k"
        else:
            n, pre = _fmt_num(value), "R"
        out.append(f"{n}{pre}")
        out.append(_eu(n, pre.upper()))
        if pre == "R":
            out.append(n)
        elif abs(value - round(value)) < 1e-6:
            out.append(str(int(round(value))))
        if value >= 10:
            out.append(_eia_code(value, 1.0))
    elif kind == "inductor":
        if value >= 1e-3 * (1 - 1e-9):
            n, pre = _fmt_num(value / 1e-3), "m"
        elif value >= 1e-6 * (1 - 1e-9):
            n, pre = _fmt_num(value / 1e-6), "u"
        else:
            n, pre = _fmt_num(value / 1e-9), "n"
        out.append(f"{n}{pre}H")
        out.append(f"{n}{pre}")
        uh = value / 1e-6
        if 1 <= uh < 10 and "." in _fmt_num(uh):
            out.append(_fmt_num(uh).replace(".", "R"))
        else:
            out.append(_eia_code(value, 1e-6))
    elif kind == "crystal":
        if value >= 1e6 * (1 - 1e-9):
            n = _fmt_num(value / 1e6)
            out += [f"{n}MHz", f"{n}M", f"{value / 1e6:.3f}MHz"]
        elif value >= 1e3 * (1 - 1e-9):
            n = _fmt_num(value / 1e3)
            out += [f"{n}kHz", f"{n}K"]
            if abs(value - round(value)) < 1e-6:
                out.append(str(int(round(value))))
        else:
            out.append(f"{_fmt_num(value)}Hz")
    elif kind == "fuse":
        if value < 1:
            out += [f"{_fmt_num(value * 1e3)}mA", f"{_fmt_num(value)}A"]
        else:
            out.append(f"{_fmt_num(value)}A")
    seen: set[str] = set()
    return [f for f in out if f and not (f.lower() in seen or seen.add(f.lower()))]


def _same_value(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-4, abs_tol=1e-15)


def score_spec(spec: ValueSpec, title: str) -> tuple[float, str]:
    """Chấm điểm `title` cho dòng BOM có giá trị + kích thước bắt buộc."""
    if not title.strip():
        return 0.0, "rỗng"
    def show(v: float) -> str:
        return (value_forms(spec.kind, v) or [format_eng(v)])[0]

    want_v = show(spec.value)
    reasons: list[str] = []

    # -- loại: tên loại khác hẳn (tụ vs điện trở) làm đổi nghĩa con số -> loại
    tkind = _detect_kind(title)
    if tkind in VALUE_KINDS and tkind != spec.kind:
        return 0.0, f"khác loại ({tkind})"

    # -- giá trị (bắt buộc)
    vals = _values_in(title, spec.kind)
    if not vals:
        return CONSIDER_SCORE, f"không thấy giá trị {want_v}"
    if not any(_same_value(v, spec.value) for v, _ in vals):
        shown = ", ".join(sorted({show(v) for v, _ in vals}))
        return 0.0, f"sai giá trị ({shown} ≠ {want_v})"
    reasons.append("đúng giá trị")
    score = 1.0 if tkind == spec.kind else 0.95
    lettered = {round(math.log10(v), 4) if v > 0 else 0.0
                for v, lettered in vals if lettered}
    if len(lettered) > 1:
        score = min(score, 0.6)
        reasons.append("tên có nhiều giá trị (bộ kit?)")

    # -- kích thước chân (bắt buộc)
    canon, labels, tht = _sizes_in(title)
    raw_hit = bool(re.search(rf"(?<!\d){re.escape(spec.size_label.lower())}(?!\d)",
                             _fold(title)))
    if spec.size in canon or raw_hit:
        reasons.append(f"đúng chân {spec.size_label}")
        if len(canon) > 1:
            score = min(score, 0.6)
            reasons.append("tên có nhiều kích thước")
    elif canon:
        return 0.0, f"sai kích thước chân ({'/'.join(labels)} ≠ {spec.size_label})"
    elif tht and not spec.size.startswith("hc49"):
        return 0.0, f"sai kích thước chân (cắm lỗ ≠ {spec.size_label})"
    elif spec.size == "hc49smd" and tht:
        return 0.0, "sai kích thước chân (cắm lỗ ≠ HC49 SMD)"
    else:
        score = min(score, 0.6)
        reasons.append(f"không thấy kích thước chân {spec.size_label}")

    # -- thuộc tính phụ ghi trong BOM (điện áp, công suất...)
    if spec.attrs:
        pt = parse(title)
        for unit, want in spec.attrs.items():
            got = pt.attrs.get(unit)
            if got is not None and got != want:
                score -= 0.25
                reasons.append(f"lệch {unit}: {got} ≠ {want}")

    # -- loại xung đột rõ: trừ điểm
    m = _CONFLICT_WORDS.search(re.sub(r"[^a-z0-9]+", " ", _fold(title)))
    if m and not (spec.kind == "fuse" and m.group(1) in ("ptc",)):
        score -= _CONFLICT_PENALTY
        reasons.append(f"loại xung đột ({m.group(1)})")
    return max(0.0, min(1.0, score)), "; ".join(reasons)


_VENDOR_PREFIX = re.compile(r"^([a-z]{3,})-([a-z]+\d[a-z0-9]*)$")


def strip_vendor(mpn: str) -> str:
    """'HUIKE-HK4100F' -> 'HK4100F'. Không có tiền tố hãng thì trả ''."""
    m = _VENDOR_PREFIX.match(mpn.lower())
    if not m or not MPN_RE.match(m.group(2)):
        return ""
    return mpn[len(m.group(1)) + 1:]


def _line_queries(line: BomLine) -> list[str]:
    raw = _strip_note(line.raw or "")
    out = [q for q in (raw, line.raw, line.key, *line.alt_keys) if q]
    p = parse(raw or line.key)
    if p.kind == "mpn" and strip_vendor(p.mpn):
        out.append(strip_vendor(p.mpn))
    return list(dict.fromkeys(out))


def score_candidate(line: BomLine, cand: Candidate) -> Candidate:
    """Chấm điểm ứng viên theo mọi tên chấp nhận được của dòng BOM (lấy cao nhất).

    Dòng có giá trị + kích thước chân: chấm bằng `score_spec` (bắt buộc đúng
    cả hai), các tên thay thế chỉ dùng thêm nếu là mã linh kiện.
    """
    spec = line_spec(line)
    if spec is not None:
        s, r = score_spec(spec, cand.title)
        for alt in line.alt_keys:
            if parse(alt).kind == "mpn":
                s2, r2 = score_title(alt, cand.title)
                if s2 > s:
                    s, r = s2, r2
        cand.score, cand.reason = round(s, 4), r
        return cand
    best, why = 0.0, ""
    for q in _line_queries(line):
        s, r = score_title(q, cand.title)
        if s > best or not why:
            best, why = s, r
    cand.score, cand.reason = round(best, 4), why
    return cand


_KIND_VN = {
    "resistor": "điện trở", "potentiometer": "biến trở", "thermistor": "điện trở nhiệt",
    "capacitor": "tụ", "inductor": "cuộn cảm", "crystal": "thạch anh",
}
_KIND_UNIT_LABEL = {"capacitor": "F", "inductor": "H", "crystal": "Hz"}


def search_queries(line: BomLine) -> list[str]:
    """Các chuỗi để gõ vào ô tìm kiếm của shop, theo thứ tự nên thử.

    Ô tìm kiếm của shop VN thường khớp chữ khá ngây thơ, gõ nguyên câu dài
    ("Điện trở 10K 1/4W 5%") hay ra rỗng. Nên thử dạng gọn trước.

    Dòng có giá trị + kích thước chân: KHÔNG kèm tên loại, chỉ
    '<giá trị> <kích thước>' ("100nF 0603"), lần lượt từng cách viết của
    giá trị (100nF, 0.1uF, 104, 100n).
    """
    spec = line_spec(line)
    if spec is not None:
        forms = value_forms(spec.kind, spec.value)
        out = [f"{v} {spec.size_label}" for v in forms]
        label_c = spec.size_label.lower()
        if spec.kind == "capacitor" and canon_size(label_c) != label_c:
            # tụ tantal 3216 -> thử thêm 1206
            out.append(f"{forms[0]} {canon_size(label_c).upper()}")
        for alt in line.alt_keys:
            pa = parse(alt)
            out.append(pa.mpn.upper() if pa.kind == "mpn" else alt)
        seen: set[str] = set()
        return [q for q in out if not (q.lower() in seen or seen.add(q.lower()))]

    p = parse(line.raw or line.key)
    out: list[str] = []
    if p.kind == "mpn":
        short = strip_vendor(p.mpn)
        if short:
            out.append(short.upper())
        out.append(p.mpn.upper())
    elif p.kind in _KIND_VN and p.value is not None:
        val = format_eng(p.value).replace("meg", "M")
        out.append(f"{_KIND_VN[p.kind]} {val}{_KIND_UNIT_LABEL.get(p.kind, '')}")
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", line.raw or "").strip()
    if raw:
        out.append(raw)
    for alt in line.alt_keys:
        pa = parse(alt)
        out.append(pa.mpn.upper() if pa.kind == "mpn" else alt)
    if not out:
        out.append(line.key)
    seen: set[str] = set()
    return [q for q in out if not (q.lower() in seen or seen.add(q.lower()))]


# ------------------------------------------------------------ quyết định

MATCH, UNCERTAIN, NONE = "match", "uncertain", "none"


@dataclass
class MatchDecision:
    status: str                         # match / uncertain / none
    best: Optional[Candidate] = None
    ranked: list[Candidate] = field(default_factory=list)
    message: str = ""

    @property
    def shortlist(self) -> list[Candidate]:
        """Các ứng viên đáng cho người dùng xem lại."""
        return [c for c in self.ranked if c.score >= CONSIDER_SCORE][:5]


def pick_best(line: BomLine, candidates: Iterable[Candidate], *,
              accept: float = ACCEPT_SCORE, consider: float = CONSIDER_SCORE,
              margin: float = MARGIN) -> MatchDecision:
    """Chọn sản phẩm khớp nhất cho một dòng BOM.

    match      ứng viên đạt `accept`, còn hàng (hoặc không rõ), và hơn hẳn
               ứng viên nhì một khoảng `margin` (hoặc gần như tuyệt đối).
    uncertain  có ứng viên >= `consider` nhưng chưa đủ chắc để tự bỏ giỏ.
    none       không có gì đáng xem.
    """
    scored = [score_candidate(line, c) for c in candidates]
    # Sắp theo điểm; cùng điểm thì giữ thứ tự của shop (sort ổn định).
    ranked = sorted(scored, key=lambda c: -c.score)
    if not ranked or ranked[0].score < consider:
        return MatchDecision(NONE, None, ranked, "không có sản phẩm nào khớp")

    good = [c for c in ranked if c.score >= accept]
    available = [c for c in good if c.in_stock is not False]
    if good and not available:
        return MatchDecision(NONE, good[0], ranked,
                             "có sản phẩm khớp nhưng đã hết hàng")
    if not available:
        return MatchDecision(UNCERTAIN, ranked[0], ranked,
                             f"khớp chưa đủ chắc ({ranked[0].score:.2f}): "
                             f"{ranked[0].reason}")

    best = available[0]
    rivals = [c for c in available[1:]]
    if rivals and best.score - rivals[0].score < margin and best.score < SURE_SCORE:
        return MatchDecision(UNCERTAIN, best, ranked,
                             "nhiều sản phẩm khớp ngang nhau, cần chọn tay")
    return MatchDecision(MATCH, best, ranked, best.reason)
