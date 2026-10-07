"""Bộ xử lý riêng cho shop CXT (cxtvn.com / linhkien.cxtvn.com).

Trang của CXT không theo khuôn Haravan/Woo nên bộ đọc chung đoán sai nhiều:

* Tìm kiếm: ``GET https://linhkien.cxtvn.com/tim-kiem.html?q=<kw>``. Mỗi sản
  phẩm là một ``.item_lkpa``: tên trong ``<h5>``, link dạng ``<id>-<slug>.html``,
  giá ghi ``Bán lẻ: 1.500₫``, ảnh trong ``.imgitem_lkpa``.
* Hết hàng: món giá 0₫, và (khi shops.json bật
  ``"no_image_means_out_of_stock"``) món có ảnh là ``images/no_image.jpg`` —
  kể cả ảnh chính trên trang sản phẩm — bị coi là hết hàng (``in_stock=False``,
  ``stock_reason``). Khi chọn: bỏ qua món hết hàng, xét tiếp ứng viên khác đúng
  giá trị + kiểu chân; không còn món nào thì dòng BOM thành ``out_of_stock``
  ("Hết hàng (không có ảnh)" / "Hết hàng (giá 0₫)"): KHÔNG bỏ giỏ, KHÔNG đổi
  sang shop khác.
* Khớp: dùng điểm của `matcher` rồi phạt nặng khi sai kiểu chân (SMA, SOD-123,
  SOD-323, 0603, 0805…), khi BOM là IC mà kết quả là "Module"/"Kit"/"Mạch",
  và khi mã đúng nhưng thông số (điện áp…) lệch. "(50c)" = gói 50 con.
* Sản phẩm gom nhiều giá trị ("thông số từ 1K đến 99K") hoặc nhiều sản phẩm
  trùng tên (HK4100F nhiều điện áp): vào trang sản phẩm, chọn biến thể
  ``kthuoc``/``color`` nếu có; không chắc thì ``uncertain``.
* Đăng nhập (`is_logged_in`): có cookie ``mem_logged``/``mem_token`` trên
  ``.cxtvn.com`` VÀ nút "CHO VÀO GIỎ"/"ĐẶT HÀNG" không trỏ tới
  ``dang-nhap.html``. Nút trỏ tới ``dang-nhap.html`` nghĩa là chưa đăng nhập.
* Thêm giỏ: đọc bảng giá bậc (``.pricelist_head``) và ``#min_buy``, điền
  ``#sl``, bấm thẻ ``a`` "CHO VÀO GIỎ" trong ``.sale-button`` (khi đã đăng
  nhập: ``href="javascript:;" onclick="addToCart(id, $('#sl').val())"``, không
  có class ``addCart``); xác nhận khi ``.slcart`` tăng hoặc nút đổi sang
  "ĐÃ THÊM".

* Tên chung: CXT đặt nhiều sản phẩm khác nhau cùng tên "Tụ 0603 không phân
  cực" trong danh sách (`is_generic_title`); giá trị thật chỉ có ở trang chi
  tiết. Với dòng BOM thụ động (tụ, điện trở, cuộn cảm) chưa có ứng viên khớp
  chắc, mở trang chi tiết của các ứng viên tên chung (tối đa
  ``MAX_DETAIL_PAGES`` trang mỗi dòng, nghỉ 2–5 giây, ưu tiên theo nhãn
  "Thông số" đã biết / gợi ý từ tên file ảnh thumbnail), đọc tên thật từ thẻ
  ``<title>`` (bỏ tiền tố "CXT – Từ ý tưởng đến sản phẩm - "), dự phòng tên
  file ảnh chính và "Mã lưu trữ" (`cxt_real_name`), rồi chấm lại. Tên thật lưu
  đệm theo URL trong cả lượt chạy. Quy cách gói lấy từ tên thật ("(20c)" = 20
  con/dây), đơn vị bán đọc từ bảng giá ("Giá / Dây").
* selectProduct(): trang tải đầy đủ của sản phẩm thuộc nhóm "Thông số"
  (``#sanphamcungloai``) có nút CHO VÀO GIỎ/ĐẶT HÀNG gọi ``selectProduct()`` —
  hàm này chỉ ``alert('Vui lòng lựa chọn thông số của sản phẩm')``, KHÔNG thêm
  giỏ. Đây đúng là bước chọn sản phẩm: bấm link thông số của chính sản phẩm
  trong ``#sanphamcungloai`` (ajax nạp lại ``#viewAjax``), khi đó nút mới gọi
  ``addToCart(id, $('#sl').val())`` (đã đăng nhập) hoặc trỏ ``dang-nhap.html``
  (chưa đăng nhập). Không bao giờ bấm nút ``selectProduct()``.

AN TOÀN: tuyệt đối không bấm "ĐẶT HÀNG"/"MUA NGAY" (``.btn-buy``), không bấm
nút nào gọi ``addToCart`` với tham số ``checkout`` hay tham số thứ 5 là
``true`` (mua ngay).
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
    ADDED, ERROR, OUT_OF_STOCK, UNCERTAIN, CartFiller, CheckoutRefused,
    LineResult, is_checkout_like, units_to_order,
)
from ..matcher import (
    ACCEPT_SCORE, ARRAY_REASON, CONSIDER_SCORE, MARGIN, MATCH, NONE, SURE_SCORE,
    Candidate, MatchDecision, Node, _fold, detect_pack, is_array, parse_html,
    parse_price, score_candidate, search_queries,
)
from ..matcher import UNCERTAIN as M_UNCERTAIN

SEARCH_URL = "https://linhkien.cxtvn.com/tim-kiem.html?q={q}"
CART_URL = "https://cxtvn.com/gio-hang.html"
MIN_DELAY = 2.0                       # CXT: nghỉ 2–5 giây giữa các lượt
DEFAULT_CXT_DELAY = (2.0, 5.0)
VARIANT_FIELDS = ("kthuoc", "color")
MAX_OOS_RETRY = 3                     # số lần chọn lại khi trang SP báo hết hàng
MAX_DETAIL_PAGES = 10                 # số trang chi tiết tên chung mở mỗi dòng

LOGIN_SELECTORS = ("a[href*='dang-xuat']", "a[href*='logout']")
LOGIN_COOKIES = ("mem_logged", "mem_token")
LOGIN_SIGNS = ("cookie mem_logged/mem_token trên .cxtvn.com và nút "
               "CHO VÀO GIỎ/ĐẶT HÀNG không trỏ tới dang-nhap.html")


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
    image: str = ""                                 # link ảnh trong kết quả
    stock_reason: str = ""                          # vì sao coi là hết hàng
    generic_title: str = ""                         # tên chung trong danh sách
    unit: str = ""                                  # đơn vị bán ("Dây")


# ------------------------------------------------------------ hết hàng

REASON_NO_IMAGE = "không có ảnh"
REASON_ZERO_PRICE = "giá 0₫"
REASON_SHOP_SAYS = "shop ghi hết hàng"
NO_IMAGE_PATH = "images/no_image.jpg"


def oos_message(reason: str = "") -> str:
    """'Hết hàng (không có ảnh)' / 'Hết hàng (giá 0₫)' / 'Hết hàng'."""
    return f"Hết hàng ({reason})" if reason else "Hết hàng"


def is_no_image(src: str) -> bool:
    """Ảnh giữ chỗ của CXT: .../images/no_image.jpg (bỏ qua ?query)."""
    path = urlsplit((src or "").strip()).path.lower()
    return path == NO_IMAGE_PATH or path.endswith("/" + NO_IMAGE_PATH)


def _img_src(n: Node) -> str:
    for k in ("data-zoom-image", "data-src", "data-original", "src"):
        v = n.get(k).strip()
        if v:
            return v
    return ""


def _card_image(card: Node) -> str:
    """Ảnh của một kết quả tìm kiếm: <img> trong .imgitem_lkpa."""
    box = card.find(lambda n: _has_class(n, "imgitem_lkpa"))
    if box is None:
        return ""
    img = box if box.tag == "img" else box.find(lambda n: n.tag == "img")
    return _img_src(img) if img is not None else ""


def _stock_of(price: int, image: str, shop_says_out: bool,
              no_image_out_of_stock: bool) -> tuple[Optional[bool], str]:
    """(in_stock, stock_reason) của một món theo ảnh / giá / chữ trên trang."""
    if no_image_out_of_stock and image and is_no_image(image):
        return False, REASON_NO_IMAGE
    if price <= 0:
        return False, REASON_ZERO_PRICE
    if shop_says_out:
        return False, REASON_SHOP_SAYS
    return None, ""


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


def parse_cxt_search(html: str, base_url: str = SEARCH_URL, *,
                     no_image_out_of_stock: bool = False
                     ) -> tuple[list[CxtCandidate], list[CxtCandidate]]:
    """Đọc trang tìm kiếm CXT -> (ứng viên có giá, món giá 0₫).

    Món giá 0₫ luôn bị đánh dấu hết hàng; món có ảnh ``images/no_image.jpg``
    bị đánh dấu hết hàng khi `no_image_out_of_stock` (shops.json) bật.
    """
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
        image = _card_image(card)
        stock, why = _stock_of(
            price, image,
            any(k in text for k in ("het hang", "tam het", "ngung kinh doanh")),
            no_image_out_of_stock)
        c = CxtCandidate(title=title, url=url, price=price, in_stock=stock,
                         pack=detect_cxt_pack(title),
                         pid=_ID_RE.search(href).group(1),
                         multi=is_multi_value(title),
                         image=urljoin(base_url, image) if image else "",
                         stock_reason=why)
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


def _canon_pkg(p: str) -> str:
    p = p.replace("-", "")
    # SOIC-8 và SOP-8 là một (shop VN ghi lẫn lộn)
    return "sop" + p[4:] if p.startswith("soic") else p


def packages(text: str) -> set[str]:
    """Kiểu chân trong tên: {'sod123', '0603'} (đã bỏ gạch nối; SOIC = SOP)."""
    t = _fold(text).replace("_", " ")
    return {_canon_pkg(m.group(1)) for m in _PKG_RE.finditer(t)}


# Nhóm kiểu chân: cắm lỗ (DIP/THT) hay dán (SMD/SOP/SOIC/SOT...).
_SMD_RE = re.compile(
    r"(?<![a-z0-9])(?:smd|smt|smdip|dan|"
    r"sma|smb|smc|sod-?\d+[a-z]?|sot-?\d+[a-z]?|"
    r"sop-?\d*|soic-?\d*|ssop-?\d*|tssop-?\d*|msop-?\d*|"
    r"qfp-?\d*|lqfp-?\d*|tqfp-?\d*|qfn-?\d*|dfn-?\d*|"
    r"0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![a-z0-9])")
_DIP_RE = re.compile(
    r"(?<![a-z0-9])(?:dip-?\d*|pdip-?\d*|tht|chan cam|cam lo|xuyen lo)(?![a-z0-9])")


def mount_kind(text: str) -> str:
    """'smd' (dán), 'dip' (cắm lỗ) hoặc '' (không rõ / ghi cả hai)."""
    t = re.sub(r"[_]+", " ", _fold(text))
    smd, dip = bool(_SMD_RE.search(t)), bool(_DIP_RE.search(t))
    if smd == dip:
        return ""
    return "smd" if smd else "dip"


def _pkg_conflict(want: set[str], got: set[str]) -> bool:
    """Hai bộ kiểu chân cụ thể không có cái nào chung ('dip' khớp 'dip4')."""
    if not want or not got or want & got:
        return False
    for w in want:
        for g in got:
            a, b = re.sub(r"\d+$", "", w), re.sub(r"\d+$", "", g)
            if a == b and (a == w or b == g):   # một bên không ghi số chân
                return False
    return True


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
    want_mount, got_mount = mount_kind(text), mount_kind(cand.title)
    if want_mount and got_mount and want_mount != got_mount:
        score -= 0.7
        reasons.append(f"sai kiểu chân: {got_mount.upper()} ≠ {want_mount.upper()}")
    elif _pkg_conflict(want_pkg, got_pkg):
        score -= 0.7
        reasons.append(f"sai kiểu chân: {'/'.join(sorted(got_pkg))} ≠ "
                       f"{'/'.join(sorted(want_pkg))}")
    elif want_mount and not got_mount and not got_pkg and _is_ic_line(line) \
            and score >= ACCEPT_SCORE:
        # BOM ghi rõ SMD/DIP mà tên shop không nói: không tự bỏ giỏ.
        score = ACCEPT_SCORE - 0.05
        reasons.append(f"tên không ghi kiểu chân ({want_mount.upper()}), "
                       f"cần kiểm tra")

    if pq.kind in ("resistor", "capacitor") and is_array(cand.title) \
            and not is_array(text):
        score -= 0.7
        reasons.append(ARRAY_REASON)

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
    """Như `pick_best` nhưng dùng `cxt_score`, và bỏ qua món hết hàng.

    Món hết hàng (giá 0₫, không có ảnh, shop ghi hết hàng) không bao giờ được
    chọn; các ứng viên còn hàng khác đúng giá trị + kiểu chân vẫn được xét.
    Không còn ứng viên còn hàng nào khớp mà món hết hàng khớp chắc -> status
    ``out_of_stock`` với lời nhắn "Hết hàng (<lý do>)".

    Trả thêm `group`: ứng viên tốt nhất cùng các sản phẩm TRÙNG TÊN với nó —
    cần vào trang sản phẩm để phân biệt (nếu nhiều hơn một).
    """
    for z in zeros:                        # món giá 0₫ luôn coi là hết hàng
        if z.in_stock is not False:
            z.in_stock = False
        if not getattr(z, "stock_reason", ""):
            z.stock_reason = REASON_ZERO_PRICE
    for c in [*cands, *zeros]:
        cxt_score(line, c)
    ranked = sorted((c for c in cands if not _is_oos(c)), key=lambda c: -c.score)
    oos = sorted((c for c in [*cands, *zeros] if _is_oos(c)),
                 key=lambda c: -c.score)

    dec, group = _pick_available(ranked, accept=accept, consider=consider,
                                 margin=margin)
    if dec.status == MATCH:
        return dec, group

    good_oos = [c for c in oos if c.score >= accept]
    if good_oos and (dec.best is None or good_oos[0].score > dec.best.score):
        top = good_oos[0]
        return MatchDecision(OUT_OF_STOCK, top, good_oos,
                             oos_message(getattr(top, "stock_reason", ""))), []
    weak_oos = [c for c in oos if c.score >= consider]
    if dec.status == NONE and weak_oos:
        top = weak_oos[0]
        names = "; ".join(z.title for z in weak_oos[:3])
        why = oos_message(getattr(top, "stock_reason", "")).lower()
        return MatchDecision(M_UNCERTAIN, top, weak_oos,
                             f"khớp chưa đủ chắc, món gần giống đã {why} — "
                             f"cần kiểm tra: {names}"), []
    return dec, []


def _is_oos(c: Candidate) -> bool:
    return c.in_stock is False


def _pick_available(ranked: list[CxtCandidate], *, accept: float,
                    consider: float, margin: float
                    ) -> tuple[MatchDecision, list[CxtCandidate]]:
    """Chọn trong các ứng viên CÒN HÀNG (đã chấm điểm, sắp giảm dần)."""
    if not ranked or ranked[0].score < consider:
        return MatchDecision(NONE, None, ranked, "không có sản phẩm nào khớp"), []
    good = [c for c in ranked if c.score >= accept]
    if not good:
        return MatchDecision(M_UNCERTAIN, ranked[0], ranked,
                             f"khớp chưa đủ chắc ({ranked[0].score:.2f}): "
                             f"{ranked[0].reason}"), []

    best = good[0]
    group = [best] + [c for c in good[1:] if _same_name(c.title, best.title)]
    others = [c for c in good[1:] if c not in group]
    if others and best.score - others[0].score < margin and best.score < SURE_SCORE:
        return MatchDecision(M_UNCERTAIN, best, ranked,
                             "nhiều sản phẩm khớp ngang nhau, cần chọn tay"), []
    return MatchDecision(MATCH, best, ranked, best.reason), group


# ------------------------------------------------------------ tên chung

_PASSIVE_KINDS = ("resistor", "capacitor", "inductor")
_PASSIVE_UNITS = ("ohm", "f", "h")
_PASSIVE_WORD_RE = re.compile(
    r"(?<![a-z0-9])(?:tu dien|tu|dien tro|cuon cam|capacitor|resistor|inductor)"
    r"(?![a-z0-9])")
_CHIP_SIZES = {"0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010",
               "2512"}
_CXT_TITLE_PREFIX_RE = re.compile(
    r"^\s*cxt\s*[-–—]\s*tu y tuong den san pham\s*[-–—|:]\s*")
_IMG_TAIL_RE = re.compile(r"[-_]img[-_]?\d+$", re.I)
_STORAGE_RE = re.compile(r"ma luu tru\s*:?\s*([a-z0-9._-]+)")


def is_generic_title(title: str) -> bool:
    """Tên chung kiểu "Tụ 0603 không phân cực": có loại (tụ/điện trở/cuộn cảm)
    + kích thước chân (0603...) nhưng không có giá trị đọc được."""
    t = re.sub(r"[_]+", " ", _fold(title))
    if not _PASSIVE_WORD_RE.search(t) or not (packages(title) & _CHIP_SIZES):
        return False
    if is_multi_value(title):
        return False                      # "thông số từ 1K đến 99K": chỗ khác lo
    vals = spec_values(title)
    return not any(u in vals for u in _PASSIVE_UNITS)


def is_passive_line(line: BomLine) -> bool:
    """Dòng BOM là tụ / điện trở / cuộn cảm."""
    text = _line_text(line)
    if parse(text).kind in _PASSIVE_KINDS:
        return True
    return bool(_PASSIVE_WORD_RE.search(_fold(text))) and any(
        u in spec_values(text) for u in _PASSIVE_UNITS)


def image_hint(src: str) -> str:
    """Gợi ý từ tên file ảnh: '.../tu-100nf-50v-10-0603-10c-img-1658908857.jpg'
    -> 'tu 100nf 50v 10 0603 10c'. Ảnh giữ chỗ / không có -> ''."""
    if not src or is_no_image(src):
        return ""
    stem = urlsplit(src.strip()).path.rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    stem = _IMG_TAIL_RE.sub("", stem)
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", stem)).strip()


def strip_cxt_title(title: str) -> str:
    """Bỏ tiền tố 'CXT – Từ ý tưởng đến sản phẩm - ' của thẻ <title>."""
    t = re.sub(r"\s+", " ", title or "").strip()
    m = _CXT_TITLE_PREFIX_RE.match(_fold(t))
    if m and len(_fold(t)) == len(t):     # bỏ dấu không đổi độ dài chuỗi
        return t[m.end():].strip()
    if m:                                 # phòng hờ: cắt theo dấu gạch sau "phẩm"
        parts = re.split(r"\s[-–—|]\s", t, maxsplit=2)
        return parts[-1].strip() if len(parts) == 3 else t
    return t


def _probe_score(line: BomLine, title: str) -> float:
    return cxt_score(line, Candidate(title=title, url="")).score


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
    image: str = ""                       # ảnh chính của sản phẩm
    retail: Optional[int] = None          # giá "Bán lẻ" (None = không ghi)
    stock_reason: str = ""
    page_title: str = ""                  # <title> đã bỏ tiền tố "CXT – ..."
    storage_code: str = ""                # "Mã lưu trữ"
    real_name: str = ""                   # tên thật (xem `cxt_real_name`)
    unit: str = ""                        # đơn vị bán theo bảng giá ("Dây")
    # các link "Thông số" (#sanphamcungloai): (href, nhãn, đang chọn)
    siblings: list[tuple[str, str, bool]] = field(default_factory=list)
    needs_select: bool = False            # nút giỏ gọi selectProduct()

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
    if head.tag in ("td", "th") and head.parent is not None \
            and head.parent.tag == "tr":
        head = head.parent                # trang thật: mỗi ô có class pricelist_head
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


_UNIT_RE = re.compile(r"gi[aá]\s*/\s*([^\s\d<>/|:]+)", re.I)


def _price_unit(root: Node) -> str:
    """Đơn vị bán trong bảng giá: 'Giá / Dây' -> 'Dây'."""
    head = root.find(lambda n: _has_class(n, "pricelist_head"))
    scope = head.parent.parent if head is not None and head.parent is not None \
        and head.parent.parent is not None else root
    for n in scope.iter():
        if _has_class(n, "pricelist_td") or n.tag in ("td", "th", "b"):
            m = _UNIT_RE.search(n.text())
            if m:
                return m.group(1).strip()
    return ""


def _siblings(root: Node) -> list[tuple[str, str, bool]]:
    box = root.find(lambda n: n.id == "sanphamcungloai")
    if box is None:
        return []
    return [(a.get("href").strip(), a.text(), _has_class(a, "active"))
            for a in box.find_all(lambda n: n.tag == "a")
            if _ID_RE.search(a.get("href"))]


def _page_title(root: Node) -> str:
    t = root.find(lambda n: n.tag == "title")
    text = t.text() if t is not None else ""
    if not text:
        pt = root.find(lambda n: n.id == "page-title")
        text = pt.text() if pt is not None else ""
    if not text:
        og = root.find(lambda n: n.tag == "meta"
                       and n.get("property").lower() == "og:title")
        text = og.get("content") if og is not None else ""
    return strip_cxt_title(text)


def cxt_real_name(info: CxtProduct) -> str:
    """Tên thật của sản phẩm: <title> (đã bỏ tiền tố) nếu có giá trị; không
    thì tên + gợi ý từ tên file ảnh chính; không nữa thì tên + 'Mã lưu trữ'."""
    shown = info.title or info.page_title
    if info.page_title and not is_generic_title(info.page_title):
        return info.page_title
    hint = image_hint(info.image)
    if hint and any(u in spec_values(hint) for u in _PASSIVE_UNITS):
        return f"{shown} {hint}".strip()
    if info.storage_code:
        return f"{shown} (mã lưu trữ {info.storage_code})".strip()
    return info.page_title or shown


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


def _in_sale_button(n: Node) -> bool:
    return any(_has_class(a, "sale-button") for a in n.ancestors())


def _cart_buttons(root: Node) -> list[Node]:
    """Nút giỏ/đặt hàng: .addCart, thẻ a trong .sale-button, onclick addToCart."""
    return [n for n in root.iter()
            if _has_class(n, "addcart")
            or (n.tag == "a" and (_in_sale_button(n)
                                  or "addtocart" in n.get("onclick").lower()))]


def has_login_redirect(html: str) -> bool:
    """Nút CHO VÀO GIỎ/ĐẶT HÀNG trên trang có trỏ tới dang-nhap không."""
    for b in _cart_buttons(parse_html(html or "")):
        t = b.text() or b.get("value")
        if not (_is_add_label(t) or "dat hang" in _fold(t)):
            continue
        if "dang-nhap" in b.get("href").lower() or \
                "dang-nhap" in b.get("onclick").lower():
            return True
    return False


def _has_login_cookie(page: Any) -> bool:
    ctx = getattr(page, "context", None)
    if callable(ctx) and not hasattr(ctx, "cookies"):
        ctx = ctx()                       # phòng khi context là phương thức
    if ctx is None or not hasattr(ctx, "cookies"):
        return False
    try:
        cookies = ctx.cookies() or []
    except Exception:
        return False
    for c in cookies:
        if not isinstance(c, dict):
            continue
        dom = (c.get("domain") or "").lstrip(".").lower()
        if c.get("name") in LOGIN_COOKIES and (c.get("value") or "") and \
                (dom == "cxtvn.com" or dom.endswith(".cxtvn.com")):
            return True
    return False


def is_logged_in(page: Any) -> bool:
    """Đã đăng nhập CXT: có cookie mem_logged/mem_token trên .cxtvn.com VÀ
    nút CHO VÀO GIỎ/ĐẶT HÀNG trên trang không trỏ tới dang-nhap.

    Lỗi khi dò (trang đang chuyển hướng...) được coi là "chưa".
    """
    if not _has_login_cookie(page):
        return False
    try:
        html = page.content()
    except Exception:
        return False
    return not has_login_redirect(html)


_ADD_CALL_RE = re.compile(r"addtocart\s*\(", re.I)


def _call_args(js: str) -> Optional[list[str]]:
    """Các tham số cấp ngoài cùng của lời gọi addToCart(...) trong `js`."""
    m = _ADD_CALL_RE.search(js or "")
    if not m:
        return None
    args: list[str] = []
    depth, cur, quote = 0, "", ""
    for ch in js[m.end():]:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                args.append(cur.strip())
                return [] if args == [""] else args
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        cur += ch
    return None                           # thiếu ngoặc đóng: không tin


def is_add_to_cart_onclick(onclick: str) -> bool:
    """onclick gọi addToCart để CHO VÀO GIỎ — không phải MUA NGAY
    (tham số thứ 5 là true) hay checkout."""
    args = _call_args(onclick)
    if args is None:
        return False
    if len(args) >= 5 and args[4].strip().lower() == "true":
        return False
    return not any("checkout" in a.lower() for a in args)


def _is_add_label(text: str) -> bool:
    t = _fold(text)
    return "cho vao gio" in t or "them vao gio" in t


# Khối không thuộc sản phẩm chính: thẻ kết quả/sản phẩm liên quan, đầu/chân trang.
_NOT_MAIN_RE = re.compile(
    r"item_lkpa|imgitem_lkpa|header|footer|menu|logo|banner|sidebar|"
    r"lienquan|lien_quan|related|cungloai|cung_loai|splq|sp_khac|other")
_MAIN_IMG_RE = re.compile(
    r"zoom|main|detail|chitiet|chi_tiet|big|large|anhchinh|anh_chinh|"
    r"img_?sp|imgsp|product|sanpham|san_pham|photo|gallery")


def _names(n: Node) -> str:
    return " ".join(n.classes + [n.id])


def _main_image(root: Node) -> str:
    """Ảnh chính trên trang sản phẩm (bỏ ảnh logo, ảnh sản phẩm liên quan)."""
    imgs: list[tuple[str, list[Node]]] = []
    for n in root.iter():
        if n.tag != "img":
            continue
        chain = [n, *n.ancestors()]
        if any(a.tag in ("header", "footer", "nav")
               or _NOT_MAIN_RE.search(_names(a)) for a in chain):
            continue
        src = _img_src(n)
        if not src or "logo" in src.lower():
            continue
        imgs.append((src, chain))
    for src, chain in imgs:
        if any(_MAIN_IMG_RE.search(_names(a)) for a in chain):
            return src
    if imgs:
        return imgs[0][0]
    og = root.find(lambda n: n.tag == "meta"
                   and n.get("property").lower() == "og:image")
    return og.get("content").strip() if og is not None else ""


def _main_text(root: Node) -> str:
    """Chữ của trang, bỏ các khối sản phẩm liên quan/đầu/chân trang."""
    parts: list[str] = []

    def walk(n: Node) -> None:
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            elif c.tag not in ("script", "style", "header", "footer", "nav") \
                    and not _NOT_MAIN_RE.search(_names(c)):
                walk(c)

    walk(root)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def parse_cxt_product(html: str, *, no_image_out_of_stock: bool = False
                      ) -> CxtProduct:
    """Đọc trang sản phẩm CXT.

    Hết hàng (``in_stock=False`` + ``stock_reason``) khi: ảnh chính là
    ``images/no_image.jpg`` (chỉ khi `no_image_out_of_stock`), giá bán lẻ 0₫
    mà không có bậc giá nào > 0, hoặc trang ghi hết hàng.
    """
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
        if "selectproduct" in b.get("onclick").lower():
            # Nút giả: chỉ báo "Vui lòng lựa chọn thông số" — chưa biết đăng nhập.
            if _is_add_label(t):
                info.needs_select = True
            continue
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
    shop_says_out = any(k in body for k in ("het hang", "tam het hang",
                                            "ngung kinh doanh"))
    info.image = _main_image(root)
    m = _RETAIL_RE.search(_fold(_main_text(root)))
    if m:
        info.retail = parse_price(m.group(1))
    # Giá 0₫: ghi "Bán lẻ: 0₫" và không có bậc giá nào > 0.
    price = info.retail if info.retail is not None else 1
    if info.tiers:
        price = max(p for _, p in info.tiers)
    stock, why = _stock_of(price, info.image, shop_says_out, no_image_out_of_stock)
    if stock is False:
        info.in_stock, info.stock_reason = False, why

    info.page_title = _page_title(root)
    m = _STORAGE_RE.search(_fold(_main_text(root)))
    if m:
        info.storage_code = m.group(1).upper()
    info.unit = _price_unit(root)
    info.siblings = _siblings(root)
    info.real_name = cxt_real_name(info)
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


def cxt_no_image_flag(shops: Any = None) -> bool:
    """"no_image_means_out_of_stock" của shop CXT trong data/shops.json.

    Khớp shop theo tên miền *.cxtvn.com (cxtvn.com hay linhkien.cxtvn.com đều
    là CXT). Không có cấu hình -> False.
    """
    if shops is None:
        try:
            from ...config import load_shops
            shops = load_shops()
        except Exception:
            return False
    items = shops.values() if isinstance(shops, dict) else shops
    for s in items:
        if is_cxt_url(getattr(s, "url", "")):
            return bool(getattr(s, "no_image_means_out_of_stock", False))
    return False


class CxtCartFiller(CartFiller):
    """CartFiller cho CXT. Được `CartFiller(...)` tự chọn khi link là *.cxtvn.com."""

    # Cách dò đăng nhập riêng của CXT — CLI/web truyền vào `open_shop`.
    login_check = staticmethod(is_logged_in)
    login_signs = LOGIN_SIGNS

    def __init__(self, page: Any, base_url: str, **kwargs: Any) -> None:
        kwargs.pop("search_url", None)
        kwargs.pop("platform", None)
        kwargs.setdefault("cart_url", CART_URL)
        lo, hi = kwargs.pop("delay", DEFAULT_CXT_DELAY)
        lo = max(MIN_DELAY, lo)
        kwargs["delay"] = (lo, max(lo, hi))
        if kwargs.get("no_image_out_of_stock") is None:
            kwargs["no_image_out_of_stock"] = cxt_no_image_flag()
        max_pages = kwargs.pop("max_detail_pages", MAX_DETAIL_PAGES)
        super().__init__(page, base_url, search_url=SEARCH_URL, **kwargs)
        self.logged_out = False
        # link sản phẩm -> lý do hết hàng, biết được khi vào trang sản phẩm
        self.known_oos: dict[str, str] = {}
        self._page_oos = False            # add_to_cart vừa gặp trang hết hàng
        self.max_detail_pages = max_pages
        # Bộ nhớ đệm cả lượt chạy: link -> trang chi tiết đã đọc (tên thật...),
        # và link -> nhãn "Thông số" thấy trong #sanphamcungloai của trang khác.
        self.detail_cache: dict[str, CxtProduct] = {}
        self.sibling_labels: dict[str, str] = {}
        self._detail_budget = max_pages   # số trang chi tiết còn được mở (dòng này)

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

    @staticmethod
    def _attr(handle: Any, name: str) -> str:
        try:
            return handle.get_attribute(name) or ""
        except Exception:
            return ""

    def _is_safe_add(self, handle: Any) -> bool:
        """Đúng nút 'CHO VÀO GIỎ': chữ đúng, không phải MUA NGAY/ĐẶT HÀNG/checkout,
        onclick (nếu có) gọi addToCart mà không có tham số thứ 5 = true."""
        text, href = self._label(handle)
        if not _is_add_label(text):
            return False
        if is_checkout_like(text, href) or _onclick_forbidden(self._attrs(handle)):
            return False
        if "btn-buy" in self._attr(handle, "class").lower().split():
            return False
        if "dang-nhap" in href.lower():
            return False
        onclick = self._attr(handle, "onclick")
        if "selectproduct" in onclick.lower():
            return False                  # chỉ alert "chọn thông số", không thêm giỏ
        if "addtocart" in onclick.lower() and not is_add_to_cart_onclick(onclick):
            return False
        return True

    def _add_button(self) -> Any:
        """Thẻ a 'CHO VÀO GIỎ' (trong .sale-button / onclick addToCart / .addCart).

        Bỏ qua mọi nút ĐẶT HÀNG, MUA NGAY (.btn-buy, addToCart(..., true)) và
        checkout.
        """
        for sel in (".sale-button a", "a[onclick*='addToCart']", ".addCart"):
            for h in self._all(sel):
                if self._is_safe_add(h):
                    return h
        return None

    def _click_add(self, handle: Any) -> None:
        if not self._is_safe_add(handle):
            text, _ = self._label(handle)
            raise CheckoutRefused(f"Từ chối bấm nút không phải 'CHO VÀO GIỎ': {text!r}")
        handle.click()

    # -- tìm kiếm

    def search_cxt(self, query: str) -> tuple[list[CxtCandidate], list[CxtCandidate]]:
        url = self.search_page_url(query)
        html = self._goto(url)
        priced, zeros = parse_cxt_search(
            html, getattr(self.page, "url", "") or url,
            no_image_out_of_stock=self.no_image_out_of_stock)
        for c in priced:
            if c.url in self.detail_cache:    # đã đọc tên thật ở dòng trước
                self._apply_detail(c, self.detail_cache[c.url])
            if c.url in self.known_oos:       # đã biết hết hàng qua trang SP
                c.in_stock, c.stock_reason = False, self.known_oos[c.url]
        return priced, zeros

    # -- tên chung: đọc tên thật ở trang chi tiết

    def _detail(self, url: str) -> CxtProduct:
        """Trang chi tiết (đã đọc thì lấy từ bộ nhớ đệm, không mở lại)."""
        info = self.detail_cache.get(url)
        if info is None:
            info = parse_cxt_product(self._goto(url),
                                     no_image_out_of_stock=self.no_image_out_of_stock)
            self.detail_cache[url] = info
            for href, label, _active in info.siblings:
                if label:
                    self.sibling_labels.setdefault(urljoin(url, href), label)
        return info

    def _apply_detail(self, c: CxtCandidate, info: CxtProduct) -> None:
        """Thay tên chung bằng tên thật; cập nhật gói, đơn vị, còn/hết hàng."""
        real = info.real_name
        if not real or not is_generic_title(c.generic_title or c.title):
            return
        if not c.generic_title:
            c.generic_title = c.title
        c.title = real
        c.pack = detect_cxt_pack(real)
        c.multi = is_multi_value(real)
        c.unit = info.unit
        if info.image:
            c.image = info.image
        if c.price <= 0 and info.tiers:
            c.price = sorted(info.tiers)[0][1]
        if info.in_stock is False:
            self._mark_oos(c, info.stock_reason)
        elif c.in_stock is False and c.stock_reason == REASON_NO_IMAGE \
                and info.image and not is_no_image(info.image):
            # Ảnh chính là ảnh thật: còn hàng (thumbnail danh sách có thể cũ).
            c.in_stock, c.stock_reason = None, ""

    def _hint_score(self, line: BomLine, c: CxtCandidate) -> float:
        """Độ hứa hẹn của ứng viên tên chung, trước khi mở trang: nhãn
        "Thông số" đã biết > gợi ý từ tên file ảnh thumbnail."""
        label = self.sibling_labels.get(c.url, "")
        if label:
            return _probe_score(line, f"{c.title} {label}") + 1.0
        hint = image_hint(c.image)
        return _probe_score(line, f"{c.title} {hint}") if hint else 0.0

    def _read_generic(self, line: BomLine, cands: list[CxtCandidate]) -> int:
        """Mở trang chi tiết các ứng viên tên chung (còn hàng) để đọc tên thật.

        Trả về số ứng viên vừa được thay tên. Tối đa `max_detail_pages` trang
        MỚI cho mỗi dòng BOM (`_detail_budget`, đặt lại ở `find`); trang đã đọc
        (bộ nhớ đệm) không tính, không nghỉ.
        """
        if not is_passive_line(line):
            return 0
        pending = [c for c in cands if not _is_oos(c) and not c.generic_title
                   and is_generic_title(c.title)]
        if not pending:
            return 0
        known = {c.url for c in cands}
        changed = 0
        while pending:
            # Link "Thông số" ở trang đã mở mà khớp chắc dòng BOM: thêm vào
            # hàng đợi kể cả khi danh sách tìm kiếm không có.
            for url, label in list(self.sibling_labels.items()):
                if url in known:
                    continue
                generic = pending[0].title
                if _probe_score(line, f"{generic} {label}") >= self.accept:
                    m = _ID_RE.search(url)
                    extra = CxtCandidate(title=generic, url=url,
                                         pid=m.group(1) if m else "")
                    cands.append(extra)
                    pending.append(extra)
                    known.add(url)
            pending.sort(key=lambda c: -self._hint_score(line, c))
            c = pending.pop(0)
            cached = c.url in self.detail_cache
            if not cached:
                if self._detail_budget <= 0:
                    break
                self._pause()
                self._detail_budget -= 1
            self._apply_detail(c, self._detail(c.url))
            if not c.generic_title:
                continue
            changed += 1
            cxt_score(line, c)
            if not _is_oos(c) and c.score >= SURE_SCORE:
                break
        return changed

    def search(self, query: str) -> list[Candidate]:
        return [c for c in self.search_cxt(query)[0] if c.in_stock is not False]

    def _mark_oos(self, c: CxtCandidate, reason: str) -> None:
        c.in_stock, c.stock_reason = False, reason
        self.known_oos[c.url] = reason

    def find(self, line: BomLine) -> tuple[MatchDecision, str]:
        best: tuple[MatchDecision, str] | None = None
        self._detail_budget = self.max_detail_pages
        for q in search_queries(line):
            cands, zeros = self.search_cxt(q)
            read_generic = False
            while True:
                dec, group = cxt_pick(line, cands, zeros, accept=self.accept,
                                      consider=self.consider)
                if dec.status != MATCH:
                    # Chưa có món khớp chắc: đọc tên thật của các món tên chung
                    # ("Tụ 0603 không phân cực") rồi chọn lại.
                    if not read_generic:
                        read_generic = True
                        if self._read_generic(line, cands):
                            continue
                    break
                if getattr(dec.best, "generic_title", ""):
                    dec.message = (f"{dec.message}; tên thật đọc từ trang sản "
                                   f"phẩm (danh sách ghi '{dec.best.generic_title}')")
                n_oos = len(self.known_oos)
                dec = self._resolve(line, dec, group)
                # Cả nhóm hết hàng (biết qua trang sản phẩm): chọn lại từ đầu,
                # các món đó giờ đã bị đánh dấu nên không bị chọn nữa.
                if dec.status != OUT_OF_STOCK or len(self.known_oos) == n_oos:
                    break
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
        oos: list[CxtCandidate] = []
        for i, c in enumerate(group):
            if i:
                self._pause()
            info = parse_cxt_product(
                self._goto(c.url), no_image_out_of_stock=self.no_image_out_of_stock)
            if info.in_stock is False:
                self._mark_oos(c, info.stock_reason)
                oos.append(c)
                continue
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
        if oos and len(oos) == len(group):
            return MatchDecision(OUT_OF_STOCK, oos[0], oos,
                                 oos_message(oos[0].stock_reason))
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

    def _count_of(self, html: str) -> Optional[int]:
        """Số trên biểu tượng giỏ của CXT (.slcart)."""
        n = parse_cxt_product(html).cart_count
        return n if n is not None else super()._count_of(html)

    def _wait_added_cxt(self, before: Optional[int], button: Any) -> tuple[bool, str]:
        self._sync_floor(before)
        deadline = self.clock() + self.confirm_timeout
        while True:
            info = parse_cxt_product(self.page.content())
            after = info.cart_count
            # before=None: lấy số đọc ở bước kiểm tra giỏ làm mốc; không có mốc
            # thì chỉ tin vào nút 'ĐÃ THÊM' (không tin 'giỏ có N món').
            why = self._count_confirms(before, after)
            if why:
                return True, why
            try:
                label = button.inner_text() or ""
            except Exception:
                label = ""
            if "da them" in _fold(label) or info.added:
                self._note_cart(after, True)
                return True, "nút đổi sang 'ĐÃ THÊM'"
            if self.clock() >= deadline:
                self._note_cart(after, False)
                return False, "bấm CHO VÀO GIỎ nhưng không thấy giỏ tăng"
            self.sleep(self.poll)

    def _choose_spec(self, cand: Candidate, info: CxtProduct
                     ) -> tuple[Optional[CxtProduct], str]:
        """Bước chọn sản phẩm thay cho selectProduct(): bấm link "Thông số" của
        chính sản phẩm trong #sanphamcungloai, chờ ajax nạp nút giỏ thật.

        Trả về (trang đã nạp lại, ghi chú) hoặc (None, lý do không làm được).
        """
        m = _ID_RE.search(cand.url)
        pid = getattr(cand, "pid", "") or (m.group(1) if m else "")
        link = None
        for h in self._all("#sanphamcungloai a"):
            hm = _ID_RE.search(self._attr(h, "href"))
            if hm and hm.group(1) == pid:
                link = h
                break
        if link is None:
            return None, ("trang CXT bắt chọn thông số (selectProduct) nhưng "
                          "không thấy link thông số của sản phẩm — cần chọn tay")
        text, href = self._label(link)
        if is_checkout_like(text, href) or _onclick_forbidden(self._attrs(link)):
            raise CheckoutRefused(f"Từ chối bấm: {text!r}")
        link.click()
        deadline = self.clock() + self.confirm_timeout
        while True:
            fresh = parse_cxt_product(self.page.content(),
                                      no_image_out_of_stock=self.no_image_out_of_stock)
            if not fresh.needs_select:
                return fresh, f"đã chọn thông số '{text.strip()}' (thay selectProduct)"
            if self.clock() >= deadline:
                return None, ("bấm chọn thông số nhưng nút CHO VÀO GIỎ vẫn gọi "
                              "selectProduct() — cần chọn tay")
            self.sleep(self.poll)

    def add_to_cart(self, line: BomLine, cand: Candidate) -> LineResult:
        if self.logged_out:
            return LineResult(line, ERROR, cand, message="chưa đăng nhập CXT")
        html = self._goto(cand.url)
        info = parse_cxt_product(html, no_image_out_of_stock=self.no_image_out_of_stock)
        if info.in_stock is False:
            # Kể cả khi trang vẫn có nút CHO VÀO GIỎ: không bấm.
            if isinstance(cand, CxtCandidate):
                self._mark_oos(cand, info.stock_reason)
            else:
                self.known_oos[cand.url] = info.stock_reason
            self._page_oos = True
            return LineResult(line, OUT_OF_STOCK, cand, candidates=[cand],
                              message=oos_message(info.stock_reason))

        notes: list[str] = []
        if info.needs_select:
            # Nút CHO VÀO GIỎ đang gọi selectProduct() (chỉ alert): chọn đúng
            # "Thông số" của sản phẩm này trước, rồi đọc lại nút giỏ thật.
            fresh, why = self._choose_spec(cand, info)
            if fresh is None:
                return LineResult(line, UNCERTAIN, cand, candidates=[cand],
                                  message=why)
            fresh.real_name = fresh.real_name or info.real_name
            if fresh.cart_count is None:
                fresh.cart_count = info.cart_count
            info = fresh
            notes.append(why)
            if info.in_stock is False:
                if isinstance(cand, CxtCandidate):
                    self._mark_oos(cand, info.stock_reason)
                self._page_oos = True
                return LineResult(line, OUT_OF_STOCK, cand, candidates=[cand],
                                  message=oos_message(info.stock_reason))
        if info.logged_in is False:
            self.logged_out = True
            return LineResult(line, ERROR, cand, candidates=[cand],
                              message="chưa đăng nhập CXT (nút CHO VÀO GIỎ trỏ "
                                      "tới dang-nhap.html) — đăng nhập rồi chạy lại")

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

        pack = cand.pack if cand.pack > 1 else max(
            detect_cxt_pack(info.real_name or ""), detect_cxt_pack(info.title))
        units = units_to_order(line.qty, pack, info.min_buy)
        unit = info.unit or getattr(cand, "unit", "")
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
        per = unit.lower() if unit else "đv"
        if price:
            cand.price = price
            if len(info.tiers) > 1:
                notes.append(f"giá bậc {price:,}₫/{per}".replace(",", "."))
        if pack > 1:
            notes.append(f"gói {pack} con/{per}" if unit else f"gói {pack} con")
        if unit:
            notes.append(f"mua {units} {per}")
        if info.min_buy > 1:
            notes.append(f"mua tối thiểu {info.min_buy}")
        return LineResult(line, ADDED if ok else UNCERTAIN, cand, units,
                          units * pack, [cand], message="; ".join(notes))

    def add_line(self, line: BomLine) -> LineResult:
        if self.logged_out:
            return LineResult(line, ERROR, message="chưa đăng nhập CXT")
        self._page_oos = False
        res = super().add_line(line)
        # Trang sản phẩm cho thấy món đã chọn hết hàng: tìm lại (món đó giờ bị
        # đánh dấu) để xét ứng viên khác đúng giá trị + kiểu chân.
        for _ in range(MAX_OOS_RETRY):
            if not (res.status == OUT_OF_STOCK and self._page_oos):
                break
            self._page_oos = False
            self._pause()
            res = super().add_line(line)
        return res


__all__ = [
    "SEARCH_URL", "CART_URL", "LOGIN_SELECTORS", "CxtCandidate", "CxtProduct",
    "CxtCartFiller", "choose_variant", "cxt_no_image_flag", "cxt_pick",
    "cxt_score", "has_login_redirect", "is_add_to_cart_onclick",
    "is_logged_in", "is_no_image", "oos_message", "LOGIN_COOKIES", "LOGIN_SIGNS",
    "detect_cxt_pack", "is_cxt_url", "is_multi_value", "packages",
    "parse_cxt_product", "parse_cxt_search", "spec_values",
    "mount_kind",
    "MAX_DETAIL_PAGES", "cxt_real_name", "image_hint", "is_generic_title",
    "is_passive_line", "strip_cxt_title",
]
