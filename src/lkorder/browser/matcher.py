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
    KIND_MAIN_UNIT, MPN_RE, NOISE_WORDS, KIND_TOKENS, format_eng, parse,
    parse_value, similarity, strip_accents,
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


def _line_queries(line: BomLine) -> list[str]:
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", line.raw or "").strip()
    out = [q for q in (raw, line.raw, line.key, *line.alt_keys) if q]
    return list(dict.fromkeys(out))


def score_candidate(line: BomLine, cand: Candidate) -> Candidate:
    """Chấm điểm ứng viên theo mọi tên chấp nhận được của dòng BOM (lấy cao nhất)."""
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
    """
    p = parse(line.raw or line.key)
    out: list[str] = []
    if p.kind == "mpn":
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
