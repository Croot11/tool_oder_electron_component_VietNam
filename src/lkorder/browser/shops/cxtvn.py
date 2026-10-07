"""Bộ xử lý riêng cho shop CXT (cxtvn.com / linhkien.cxtvn.com).

Trang của CXT không theo khuôn Haravan/Woo nên bộ đọc chung đoán sai nhiều:

* Tìm kiếm: ``GET https://linhkien.cxtvn.com/tim-kiem.html?q=<kw>``. Mỗi sản
  phẩm là một ``.item_lkpa``: tên trong ``<h5>``, link dạng ``<id>-<slug>.html``,
  giá ghi ``Bán lẻ: 1.500₫``. Món giá 0₫ (giá "liên hệ") bị bỏ khỏi danh sách
  ứng viên — nếu chỉ còn những món đó thì dòng BOM thành ``uncertain`` để người
  dùng tự kiểm tra.
* Khớp: dùng điểm của `matcher` rồi phạt nặng khi sai kiểu chân (SMA, SOD-123,
  SOD-323, 0603, 0805…), khi BOM là IC mà kết quả là "Module"/"Kit"/"Mạch",
  và khi mã đúng nhưng thông số (điện áp…) lệch. "(50c)" = gói 50 con.
* Sản phẩm gom nhiều giá trị ("thông số từ 1K đến 99K") hoặc nhiều sản phẩm
  trùng tên (HK4100F nhiều điện áp): vào trang sản phẩm, chọn biến thể
  ``kthuoc``/``color`` nếu có; không chắc thì ``uncertain``.
* Đăng nhập: nút "CHO VÀO GIỎ" trỏ tới ``dang-nhap.html`` nghĩa là chưa đăng
  nhập.
* Thêm giỏ: đọc bảng giá bậc (``.pricelist_head``) và ``#min_buy``, điền
  ``#sl``, bấm ``.addCart`` "CHO VÀO GIỎ"; xác nhận khi ``.slcart`` tăng hoặc
  nút đổi sang "ĐÃ THÊM".

AN TOÀN: tuyệt đối không bấm "ĐẶT HÀNG"/"MUA NGAY", không bấm nút nào gọi
``addToCart`` với tham số ``checkout``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote_plus, urljoin, urlsplit

from ...models import BomLine
from ...normalize import format_eng, parse
from ..cart import (
    ADDED, ERROR, NOT_FOUND, UNCERTAIN, CartFiller, CheckoutRefused, LineResult,
    is_checkout_like, units_to_order,
)
from ..matcher import (
    ACCEPT_SCORE, CONSIDER_SCORE, MARGIN, MATCH, NONE, SURE_SCORE, Candidate,
    MatchDecision, Node, _fold, detect_pack, parse_html, parse_price,
    score_candidate, search_queries,
)
from ..matcher import UNCERTAIN as M_UNCERTAIN

SEARCH_URL = "https://linhkien.cxtvn.com/tim-kiem.html?q={q}"
MIN_DELAY = 2.0                       # CXT: nghỉ 2–5 giây giữa các lượt
DEFAULT_CXT_DELAY = (2.0, 5.0)
VARIANT_FIELDS = ("kthuoc", "color")

LOGIN_SELECTORS = ("a[href*='dang-xuat']", "a[href*='logout']")


def is_cxt_url(url: str) -> bool:
    """Link có thuộc cxtvn.com (kể cả tên miền con) không."""
    u = (url or "").strip()
    if "://" not in u:
        u = "https://" + u
    host = urlsplit(u).hostname or ""
    return host == "cxtvn.com" or host.endswith(".cxtvn.com")


# ------------------------------------------------------------ ứng viên


@dataclass
class CxtCandidate(Candidate):
    pid: str = ""                                   # id sản phẩm trên CXT
    multi: bool = False                             # tên gom nhiều giá trị
    variant: Optional[tuple[str, str, str]] = None  # (trường, value, nhãn)


_ID_RE = re.compile(r"(?:^|/)(\d+)-[^/?#]*\.html", re.I)
_RETAIL_RE = re.compile(r"ban le\s*:?\s*(\d[\d.,\s]*)")
_CXT_PACK_RE = re.compile(r"\(\s*(\d+)\s*c\s*\)")


def detect_cxt_pack(title: str) -> int:
    """'Điện trở 10K 0603 (50c)' -> 50. Không thấy thì theo `detect_pack`."""
    m = _CXT_PACK_RE.search(_fold(title))
    if m and int(m.group(1)) > 1:
        return int(m.group(1))
    return detect_pack(title)


def _has_class(n: Node, name: str) -> bool:
    return name.lower() in n.classes


def parse_cxt_search(html: str, base_url: str = SEARCH_URL
                     ) -> tuple[list[CxtCandidate], list[CxtCandidate]]:
    """Đọc trang tìm kiếm CXT -> (ứng viên có giá, món giá 0₫ cần kiểm tra)."""
    root = parse_html(html)
    priced: list[CxtCandidate] = []
    zero: list[CxtCandidate] = []
    seen: set[str] = set()
    for card in root.find_all(lambda n: _has_class(n, "item_lkpa")):
        h5 = card.find(lambda n: n.tag == "h5")
        title = h5.text() if h5 else ""
        href = ""
        links = ([h5] if h5 is not None and h5.tag == "a" else []) + \
            (h5.find_all(lambda n: n.tag == "a") if h5 else []) + \
            card.find_all(lambda n: n.tag == "a")
        for a in links:
            if _ID_RE.search(a.get("href")):
                href = a.get("href")
                break
        if not title or not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        text = _fold(card.text())
        m = _RETAIL_RE.search(text)
        price = parse_price(m.group(1)) if m else 0     # không ghi giá = 0₫
        stock: Optional[bool] = None
        if any(k in text for k in ("het hang", "tam het", "ngung kinh doanh")):
            stock = False
        c = CxtCandidate(title=title, url=url, price=price, in_stock=stock,
                         pack=detect_cxt_pack(title),
                         pid=_ID_RE.search(href).group(1),
                         multi=is_multi_value(title))
        (priced if price > 0 else zero).append(c)
    return priced, zero


# ------------------------------------------------------------ thông số


# Một giá trị có đơn vị trong chuỗi đã bỏ dấu: 5v, dc5v, 5vdc, 10k, 4k7, 100nf,
# 0.1uf, 16mhz, 3a. Không ăn vào giữa mã linh kiện (hk4100f) hay phân số (1/4w).
_SPEC_RE = re.compile(
    r"(?<![a-z0-9./])(?:dc|ac)?(\d+(?:\.\d+)?)(meg|[pnumkg])?(\d{1,2})?"
    r"(ohm|hz|v|a|w|f|h|r)?(?:dc|ac)?(?![a-z0-9/])"
)
_MULT = {"": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6, "k": 1e3, "meg": 1e6,
         "g": 1e9}


def spec_values(text: str) -> dict[str, list[float]]:
    """Các thông số có đơn vị trong chuỗi: {'v': [5.0], 'ohm': [10000.0]}."""
    t = _fold(text).replace(",", ".")
    t = re.sub(r"[_()\[\]]", " ", t)
    out: dict[str, list[float]] = {}
    for m in _SPEC_RE.finditer(t):
        num, pre, frac, unit = m.group(1), m.group(2) or "", m.group(3), \
            m.group(4) or ""
        if not pre and not unit:
            continue                                  # số trần: bỏ
        if frac and "." in num:
            continue
        if frac and (unit and unit != "r"):
            continue                                  # 4k7 không đi kèm đơn vị
        val = float(f"{num}.{frac}") if frac else float(num)
        if unit == "r":
            unit = "ohm"
        if pre == "m":
            mult = 1e6 if unit in ("ohm", "hz", "") else 1e-3
        else:
            mult = _MULT.get(pre, 1.0)
        if not unit:
            unit = "ohm"                              # 10k, 4k7 -> điện trở
        out.setdefault(unit, []).append(val * mult)
    return out


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-15)


def spec_compatible(want: dict[str, list[float]],
                    got: dict[str, list[float]]) -> bool:
    """Có ít nhất một thông số chung và mọi thông số chung đều trùng giá trị."""
    common = set(want) & set(got)
    if not common:
        return False
    return all(any(_close(a, b) for a in want[u] for b in got[u]) for u in common)


def spec_conflict(want: dict[str, list[float]],
                  got: dict[str, list[float]]) -> bool:
    """Có thông số chung mà không trùng giá trị nào (vd 5V ≠ 12V)."""
    return any(not any(_close(a, b) for a in want[u] for b in got[u])
               for u in set(want) & set(got))


# ------------------------------------------------------------ gom nhiều giá trị

_RANGE_RE = re.compile(
    r"(?:thong so\s*)?\btu\s+(\d+(?:[.,]\d+)?\s*[a-z]*)\s*(?:den|-|~)\s*"
    r"(\d+(?:[.,]\d+)?\s*[a-z]*)")
_MULTI_HINTS = ("nhieu gia tri", "cac gia tri", "du gia tri", "nhieu thong so",
                "cac thong so", "nhieu loai", "nhieu dien ap")


def is_multi_value(title: str) -> bool:
    t = _fold(title)
    return bool(_RANGE_RE.search(t)) or any(h in t for h in _MULTI_HINTS)


def _strip_range(title: str) -> str:
    t = _fold(title)
    t = _RANGE_RE.sub(" ", t)
    for h in _MULTI_HINTS:
        t = t.replace(h, " ")
    return re.sub(r"\s+", " ", t).strip()


def _range_bounds(title: str) -> Optional[tuple[str, float, float]]:
    m = _RANGE_RE.search(_fold(title))
    if not m:
        return None
    lo = spec_values(m.group(1).replace(" ", ""))
    hi = spec_values(m.group(2).replace(" ", ""))
    for unit in lo:
        if unit in hi:
            return unit, lo[unit][0], hi[unit][0]
    return None


_UNIT_LABEL = {"capacitor": "F", "inductor": "H", "crystal": "Hz"}
_MAIN_UNIT = {"resistor": "ohm", "potentiometer": "ohm", "thermistor": "ohm",
              "capacitor": "f", "inductor": "h", "crystal": "hz"}


# ------------------------------------------------------------ chấm điểm riêng

_PKG_RE = re.compile(
    r"(?<![a-z0-9])("
    r"sma|smb|smc|sod-?\d+[a-z]?|sot-?\d+[a-z]?|to-?\d+[a-z]?|"
    r"dip-?\d*|sop-?\d*|soic-?\d*|ssop-?\d*|tssop-?\d*|msop-?\d*|"
    r"qfp-?\d*|lqfp-?\d*|tqfp-?\d*|qfn-?\d*|dfn-?\d*|"
    r"0201|0402|0603|0805|1206|1210|1812|2010|2512"
    r")(?![a-z0-9])")
_MODULE_RE = re.compile(r"\b(module|modul|kit|mach|board)\b")


def packages(text: str) -> set[str]:
    """Kiểu chân trong tên: {'sod123', '0603'} (đã bỏ gạch nối)."""
    t = _fold(text)
    return {m.group(1).replace("-", "") for m in _PKG_RE.finditer(t)}


def _line_text(line: BomLine) -> str:
    return line.raw or line.key


def _is_ic_line(line: BomLine) -> bool:
    text = _line_text(line)
    t = _fold(text)
    if _MODULE_RE.search(t):
        return False
    return parse(text).kind == "mpn" or bool(re.search(r"\bic\b", t))


def cxt_score(line: BomLine, cand: Candidate) -> Candidate:
    """Điểm khớp theo `matcher` + các luật riêng của CXT."""
    text = _line_text(line)
    pq = parse(text)
    reasons: list[str] = []
    multi = getattr(cand, "multi", False)

    if multi and pq.kind in _MAIN_UNIT and pq.value is not None:
        # "Điện trở 1/4W thông số từ 1K đến 99K": chấm như thể tên ghi đúng
        # giá trị BOM, nếu giá trị đó nằm trong khoảng.
        bounds = _range_bounds(cand.title)
        if bounds and bounds[0] == _MAIN_UNIT[pq.kind] and not (
                bounds[1] * (1 - 1e-9) <= pq.value <= bounds[2] * (1 + 1e-9)):
            cand.score, cand.reason = 0.0, "giá trị ngoài khoảng của sản phẩm"
            return cand
        val = format_eng(pq.value) + _UNIT_LABEL.get(pq.kind, "")
        probe = Candidate(title=f"{_strip_range(cand.title)} {val}", url=cand.url)
        score_candidate(line, probe)
        # Kém cụ thể hơn sản phẩm một giá trị: không để thắng/hoà sản phẩm đúng tên.
        score, why = min(probe.score, 0.85), probe.reason
        reasons.append("sản phẩm gom nhiều giá trị, cần chọn biến thể")
    else:
        score_candidate(line, cand)
        score, why = cand.score, cand.reason

    want_pkg, got_pkg = packages(text), packages(cand.title)
    if want_pkg and got_pkg and not (want_pkg & got_pkg):
        score -= 0.7
        reasons.append(f"sai kiểu chân: {'/'.join(sorted(got_pkg))} ≠ "
                       f"{'/'.join(sorted(want_pkg))}")

    if _is_ic_line(line) and _MODULE_RE.search(_fold(cand.title)):
        score -= 0.7
        reasons.append("BOM là IC nhưng sản phẩm là module/kit/mạch")

    if pq.kind == "mpn" and not multi:
        want, got = spec_values(text), spec_values(cand.title)
        if spec_conflict(want, got):
            score -= 0.5
            reasons.append("lệch thông số")

    cand.score = round(max(0.0, min(1.0, score)), 4)
    cand.reason = "; ".join([why, *reasons]) if why else "; ".join(reasons)
    return cand


def _same_name(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", _fold(a)).strip() == \
        re.sub(r"\s+", " ", _fold(b)).strip()


def cxt_pick(line: BomLine, cands: list[CxtCandidate],
             zeros: list[CxtCandidate] = (), *, accept: float = ACCEPT_SCORE,
             consider: float = CONSIDER_SCORE, margin: float = MARGIN
             ) -> tuple[MatchDecision, list[CxtCandidate]]:
    """Như `pick_best` nhưng dùng `cxt_score`.

    Trả thêm `group`: ứng viên tốt nhất cùng các sản phẩm TRÙNG TÊN với nó —
    cần vào trang sản phẩm để phân biệt (nếu nhiều hơn một).
    """
    for c in [*cands, *zeros]:
        cxt_score(line, c)
    ranked = sorted(cands, key=lambda c: -c.score)
    zero_hits = sorted((z for z in zeros if z.score >= consider),
                       key=lambda c: -c.score)

    if not ranked or ranked[0].score < consider:
        if zero_hits:
            names = "; ".join(z.title for z in zero_hits[:3])
            return MatchDecision(M_UNCERTAIN, zero_hits[0], zero_hits,
                                 f"chỉ thấy sản phẩm giá 0₫, cần kiểm tra: "
                                 f"{names}"), []
        return MatchDecision(NONE, None, ranked, "không có sản phẩm nào khớp"), []

    good = [c for c in ranked if c.score >= accept]
    available = [c for c in good if c.in_stock is not False]
    if good and not available:
        return MatchDecision(NONE, good[0], ranked,
                             "có sản phẩm khớp nhưng đã hết hàng"), []
    if not available:
        return MatchDecision(M_UNCERTAIN, ranked[0], ranked,
                             f"khớp chưa đủ chắc ({ranked[0].score:.2f}): "
                             f"{ranked[0].reason}"), []

    best = available[0]
    group = [best] + [c for c in available[1:] if _same_name(c.title, best.title)]
    others = [c for c in available[1:] if c not in group]
    if others and best.score - others[0].score < margin and best.score < SURE_SCORE:
        return MatchDecision(M_UNCERTAIN, best, ranked,
                             "nhiều sản phẩm khớp ngang nhau, cần chọn tay"), []
    return MatchDecision(MATCH, best, ranked, best.reason), group


# ------------------------------------------------------------ trang sản phẩm


@dataclass
class CxtProduct:
    title: str = ""
    tiers: list[tuple[int, int]] = field(default_factory=list)  # (từ SL, giá)
    min_buy: int = 1
    has_sl: bool = False
    variants: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    variant_kind: dict[str, str] = field(default_factory=dict)  # select/radio/item
    logged_in: Optional[bool] = None
    cart_count: Optional[int] = None
    added: bool = False                   # nút đã đổi sang "ĐÃ THÊM"
    in_stock: Optional[bool] = None
    spec_text: str = ""

    def price_for(self, units: int) -> int:
        """Đơn giá theo bậc ứng với số lượng `units` (0 nếu không có bảng)."""
        price = 0
        for q, p in sorted(self.tiers):
            if units >= q:
                price = p
        if not price and self.tiers:
            price = sorted(self.tiers)[0][1]
        return price


def _int(text: str) -> Optional[int]:
    m = re.search(r"\d[\d.,]*", text or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return int(digits) if digits else None


def _cells(row: Node) -> list[str]:
    kids = [c for c in row.children if isinstance(c, Node)]
    texts = [k.text() for k in kids if k.text()]
    return texts or [row.text()]


def _parse_tiers(root: Node) -> list[tuple[int, int]]:
    head = root.find(lambda n: _has_class(n, "pricelist_head"))
    if head is None:
        return []
    parent = head.parent
    if parent is None:
        return []
    siblings = [c for c in parent.children if isinstance(c, Node)]
    rows = siblings[siblings.index(head) + 1:]
    tiers: list[tuple[int, int]] = []

    # Dạng ngang: hàng tiêu đề là các mốc số lượng, hàng dưới là giá.
    head_cells = _cells(head)
    head_qty = [_int(c) for c in head_cells]
    if rows and sum(q is not None for q in head_qty) >= 2:
        price_cells = _cells(rows[0])
        if len(price_cells) == len(head_cells):
            for q, p in zip(head_qty, price_cells):
                if q is not None and parse_price(p):
                    tiers.append((q, parse_price(p)))
            if tiers:
                return tiers

    # Dạng dọc: mỗi hàng "số lượng | đơn giá".
    for row in rows:
        cells = _cells(row)
        if len(cells) < 2:
            continue
        q = _int(cells[0])
        p = parse_price(cells[-1])
        if q is not None and p:
            tiers.append((max(1, q), p))
    return tiers


def _variant_options(root: Node) -> tuple[dict[str, list[tuple[str, str]]],
                                          dict[str, str]]:
    variants: dict[str, list[tuple[str, str]]] = {}
    kinds: dict[str, str] = {}
    labels = {n.get("for"): n.text() for n in root.iter()
              if n.tag == "label" and n.get("for")}
    for name in VARIANT_FIELDS:
        sel = root.find(lambda n: n.tag == "select" and (
            n.get("name").lower() == name or n.id == name))
        if sel is not None:
            opts = []
            for o in sel.find_all(lambda n: n.tag == "option"):
                v = o.get("value").strip()
                if v and v not in ("0", "-1"):
                    opts.append((v, o.text()))
            if opts:
                variants[name], kinds[name] = opts, "select"
                continue
        radios = root.find_all(lambda n: n.tag == "input" and n.get("type").lower()
                               in ("radio", "checkbox")
                               and n.get("name").lower() == name)
        if radios:
            opts = []
            for r in radios:
                label = (r.get("title") or r.get("data-name")
                         or (labels.get(r.get("id"), "") if r.get("id") else "")
                         or (r.parent.text() if r.parent else ""))
                opts.append((r.get("value"), label.strip()))
            variants[name], kinds[name] = opts, "radio"
            continue
        items = root.find_all(lambda n: any(name in c for c in n.classes)
                              and (n.get("data-id") or n.get("data-value")))
        if items:
            variants[name] = [(n.get("data-id") or n.get("data-value"), n.text())
                              for n in items]
            kinds[name] = "item"
    return variants, kinds


def _cart_buttons(root: Node) -> list[Node]:
    return [n for n in root.iter() if _has_class(n, "addcart")]


def _is_add_label(text: str) -> bool:
    t = _fold(text)
    return "cho vao gio" in t or "them vao gio" in t


def parse_cxt_product(html: str) -> CxtProduct:
    root = parse_html(html)
    info = CxtProduct()
    h1 = root.find(lambda n: n.tag == "h1")
    info.title = h1.text() if h1 else ""
    info.tiers = _parse_tiers(root)

    mb = root.find(lambda n: n.id == "min_buy")
    if mb is not None:
        v = _int(mb.get("value") or mb.text())
        if v and v > 0:
            info.min_buy = v
    info.has_sl = root.find(lambda n: n.id == "sl") is not None
    info.variants, info.variant_kind = _variant_options(root)

    sl = root.find(lambda n: _has_class(n, "slcart"))
    if sl is not None:
        info.cart_count = _int(sl.text())

    for b in _cart_buttons(root):
        t = _fold(b.text() or b.get("value"))
        if "da them" in t:
            info.added = True
        if _is_add_label(t) or "da them" in t:
            href = b.get("href").lower()
            onclick = b.get("onclick").lower()
            if "dang-nhap" in href or "dang-nhap" in onclick:
                info.logged_in = False
            elif info.logged_in is None:
                info.logged_in = True
    if info.logged_in is None and root.find(
            lambda n: n.tag == "a" and "dang-xuat" in n.get("href").lower()):
        info.logged_in = True

    # Phần mô tả để phân biệt các sản phẩm trùng tên (điện áp, giá trị…).
    parts = [info.title]
    for n in root.iter():
        names = " ".join(n.classes + [n.id])
        if re.search(r"thongso|thong_so|mota|mo_ta|desc|info_sp|spec", names):
            parts.append(n.text())
    info.spec_text = " ".join(parts)

    body = _fold(root.text())
    if any(k in body for k in ("het hang", "tam het hang", "ngung kinh doanh")):
        info.in_stock = False
    return info


def choose_variant(line: BomLine, variants: dict[str, list[tuple[str, str]]]
                   ) -> tuple[Optional[tuple[str, str, str]], str]:
    """Chọn đúng một biến thể kthuoc/color theo dòng BOM.

    Trả về ((trường, value, nhãn), "") hoặc (None, lý do).
    """
    text = _line_text(line)
    want = spec_values(text)
    bom_words = set(re.findall(r"[a-z0-9]+", _fold(text)))
    hits: list[tuple[str, str, str]] = []
    for name, opts in variants.items():
        for value, label in opts:
            got = spec_values(label)
            if got:
                if want and spec_compatible(want, got):
                    hits.append((name, value, label))
                continue
            words = set(re.findall(r"[a-z0-9]+", _fold(label)))
            if words and words <= bom_words:
                hits.append((name, value, label))
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, "không có biến thể nào khớp dòng BOM"
    return None, "nhiều biến thể cùng khớp: " + ", ".join(h[2] for h in hits)


# ------------------------------------------------------------ thao tác


def _onclick_forbidden(attrs: str) -> bool:
    a = attrs.lower()
    return "checkout" in a or is_checkout_like(a)


class CxtCartFiller(CartFiller):
    """CartFiller cho CXT. Được `CartFiller(...)` tự chọn khi link là *.cxtvn.com."""

    def __init__(self, page: Any, base_url: str, **kwargs: Any) -> None:
        kwargs.pop("search_url", None)
        kwargs.pop("platform", None)
        lo, hi = kwargs.pop("delay", DEFAULT_CXT_DELAY)
        lo = max(MIN_DELAY, lo)
        kwargs["delay"] = (lo, max(lo, hi))
        super().__init__(page, base_url, search_url=SEARCH_URL, **kwargs)
        self.logged_out = False

    # -- tiện ích

    def _pause(self) -> None:
        lo, hi = self.delay
        self.sleep(self.rand(lo, hi) if hi > lo else lo)

    def search_page_url(self, query: str) -> str:
        return SEARCH_URL.format(q=quote_plus(query))

    def _all(self, selector: str) -> list[Any]:
        try:
            if hasattr(self.page, "query_selector_all"):
                return list(self.page.query_selector_all(selector) or [])
            h = self.page.query_selector(selector)
            return [h] if h is not None else []
        except Exception:
            return []

    def _attrs(self, handle: Any) -> str:
        out = []
        for name in ("onclick", "href", "data-action", "data-type", "data-checkout"):
            try:
                out.append(handle.get_attribute(name) or "")
            except Exception:
                pass
        return " ".join(out)

    def _add_button(self) -> Any:
        """Nút .addCart 'CHO VÀO GIỎ' — bỏ qua mọi nút ĐẶT HÀNG/MUA NGAY/checkout."""
        for h in self._all(".addCart"):
            text, href = self._label(h)
            if not _is_add_label(text):
                continue
            if is_checkout_like(text, href) or _onclick_forbidden(self._attrs(h)):
                continue
            return h
        return None

    def _click_add(self, handle: Any) -> None:
        text, href = self._label(handle)
        if (not _is_add_label(text) or is_checkout_like(text, href)
                or _onclick_forbidden(self._attrs(handle))):
            raise CheckoutRefused(f"Từ chối bấm nút không phải 'CHO VÀO GIỎ': {text!r}")
        handle.click()

    # -- tìm kiếm

    def search_cxt(self, query: str) -> tuple[list[CxtCandidate], list[CxtCandidate]]:
        url = self.search_page_url(query)
        html = self._goto(url)
        return parse_cxt_search(html, getattr(self.page, "url", "") or url)

    def search(self, query: str) -> list[Candidate]:
        return list(self.search_cxt(query)[0])

    def find(self, line: BomLine) -> tuple[MatchDecision, str]:
        best: tuple[MatchDecision, str] | None = None
        for q in search_queries(line):
            cands, zeros = self.search_cxt(q)
            dec, group = cxt_pick(line, cands, zeros, accept=self.accept,
                                  consider=self.consider)
            if dec.status == MATCH:
                dec = self._resolve(line, dec, group)
                if dec.status == MATCH:
                    return dec, q
            if best is None or (best[0].status == NONE and dec.status != NONE) or (
                    dec.best and best[0].best
                    and dec.best.score > best[0].best.score):
                best = (dec, q)
        assert best is not None
        return best

    def _resolve(self, line: BomLine, dec: MatchDecision,
                 group: list[CxtCandidate]) -> MatchDecision:
        """Gom nhiều giá trị / trùng tên: vào trang sản phẩm để chọn cho chắc."""
        best = group[0] if group else dec.best
        if best is None or (len(group) <= 1 and not getattr(best, "multi", False)):
            return dec
        want = spec_values(_line_text(line))
        hits: list[CxtCandidate] = []
        notes: list[str] = []
        for i, c in enumerate(group):
            if i:
                self._pause()
            info = parse_cxt_product(self._goto(c.url))
            if info.variants:
                v, why = choose_variant(line, info.variants)
                if v:
                    c.variant = v
                    hits.append(c)
                else:
                    notes.append(why)
            elif len(group) > 1 and want and spec_compatible(
                    want, spec_values(info.spec_text)):
                hits.append(c)
        if len(hits) == 1:
            c = hits[0]
            extra = f"; chọn biến thể {c.variant[0]}={c.variant[2]}" if c.variant \
                else "; phân biệt qua trang sản phẩm"
            return MatchDecision(MATCH, c, dec.ranked, f"{c.reason}{extra}")
        if len(group) > 1:
            msg = (f"{len(group)} sản phẩm trùng tên '{best.title}', "
                   f"không chắc chọn cái nào")
        else:
            msg = "sản phẩm gom nhiều giá trị, không chọn chắc được biến thể"
        if notes:
            msg += f" ({notes[0]})"
        return MatchDecision(M_UNCERTAIN, best, dec.ranked, msg)

    # -- thêm vào giỏ

    def _select_variant(self, kind: str, v: tuple[str, str, str]) -> None:
        name, value, _label = v
        if kind == "select":
            sel = f"select[name='{name}'], select#{name}"
            if hasattr(self.page, "select_option"):
                self.page.select_option(sel, value)
                return
            h = self._first((sel,))
            if h is None:
                raise RuntimeError(f"không thấy ô chọn {name}")
            h.select_option(value)
            return
        sel = (f"input[name='{name}'][value='{value}']" if kind == "radio"
               else f"[data-id='{value}'], [data-value='{value}']")
        h = self._first((sel,))
        if h is None:
            raise RuntimeError(f"không thấy biến thể {name}={value}")
        text, href = self._label(h)
        if is_checkout_like(text, href) or _onclick_forbidden(self._attrs(h)):
            raise CheckoutRefused(f"Từ chối bấm: {text!r}")
        h.click()

    def _wait_added_cxt(self, before: Optional[int], button: Any) -> tuple[bool, str]:
        deadline = self.clock() + self.confirm_timeout
        while True:
            info = parse_cxt_product(self.page.content())
            after = info.cart_count
            if before is not None and after is not None and after > before:
                return True, f"giỏ tăng {before} -> {after}"
            if before is None and after is not None and after > 0:
                return True, f"giỏ có {after} món"
            try:
                label = button.inner_text() or ""
            except Exception:
                label = ""
            if "da them" in _fold(label) or info.added:
                return True, "nút đổi sang 'ĐÃ THÊM'"
            if self.clock() >= deadline:
                return False, "bấm CHO VÀO GIỎ nhưng không thấy giỏ tăng"
            self.sleep(self.poll)

    def add_to_cart(self, line: BomLine, cand: Candidate) -> LineResult:
        if self.logged_out:
            return LineResult(line, ERROR, cand, message="chưa đăng nhập CXT")
        html = self._goto(cand.url)
        info = parse_cxt_product(html)
        if info.logged_in is False:
            self.logged_out = True
            return LineResult(line, ERROR, cand, candidates=[cand],
                              message="chưa đăng nhập CXT (nút CHO VÀO GIỎ trỏ "
                                      "tới dang-nhap.html) — đăng nhập rồi chạy lại")
        if info.in_stock is False:
            return LineResult(line, NOT_FOUND, cand, message="sản phẩm đã hết hàng")

        notes: list[str] = []
        if info.variants:
            v = getattr(cand, "variant", None)
            if v is None or v[0] not in info.variants:
                v, why = choose_variant(line, info.variants)
                if v is None:
                    return LineResult(line, UNCERTAIN, cand, candidates=[cand],
                                      message=f"cần chọn biến thể: {why}")
            self._select_variant(info.variant_kind.get(v[0], "select"), v)
            fresh = parse_cxt_product(self.page.content())   # giá/MOQ theo biến thể
            if fresh.tiers:
                info.tiers = fresh.tiers
            if fresh.min_buy > 1:
                info.min_buy = fresh.min_buy
            if fresh.cart_count is not None:
                info.cart_count = fresh.cart_count
            notes.append(f"biến thể {v[0]}={v[2]}")

        pack = cand.pack if cand.pack > 1 else detect_cxt_pack(info.title)
        units = units_to_order(line.qty, pack, info.min_buy)
        before = info.cart_count

        sl = self._first(("#sl", "input#sl", "input[name='sl']"))
        if sl is not None:
            sl.fill(str(units))
        elif units > 1:
            return LineResult(line, ERROR, cand, units, units * pack, [cand],
                              message=f"không thấy ô #sl để điền {units}")

        button = self._add_button()
        if button is None:
            return LineResult(line, ERROR, cand, units, units * pack, [cand],
                              message="không thấy nút CHO VÀO GIỎ")
        self._click_add(button)

        ok, why = self._wait_added_cxt(before, button)
        notes.insert(0, why)
        price = info.price_for(units)
        if price:
            cand.price = price
            if len(info.tiers) > 1:
                notes.append(f"giá bậc {price:,}₫/đv".replace(",", "."))
        if pack > 1:
            notes.append(f"gói {pack} con")
        if info.min_buy > 1:
            notes.append(f"mua tối thiểu {info.min_buy}")
        return LineResult(line, ADDED if ok else UNCERTAIN, cand, units,
                          units * pack, [cand], message="; ".join(notes))

    def add_line(self, line: BomLine) -> LineResult:
        if self.logged_out:
            return LineResult(line, ERROR, message="chưa đăng nhập CXT")
        return super().add_line(line)


__all__ = [
    "SEARCH_URL", "LOGIN_SELECTORS", "CxtCandidate", "CxtProduct",
    "CxtCartFiller", "choose_variant", "cxt_pick", "cxt_score",
    "detect_cxt_pack", "is_cxt_url", "is_multi_value", "packages",
    "parse_cxt_product", "parse_cxt_search", "spec_values",
]
