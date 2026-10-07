"""Chuẩn hoá tên linh kiện.

Vấn đề thực tế: cùng một con hàng, mỗi shop VN đặt tên một kiểu:

    "STM32F103C8T6"  ==  "Vi điều khiển STM32F103C8T6 LQFP-48 chính hãng"
    "Điện trở 10K 1/4W 5%"  ==  "Trở 10K 0.25W"
    "Tụ gốm 100nF"  ==  "Tụ 0.1uF 50V 0805"

Hai chiến lược, theo thứ tự ưu tiên:

1. Có mã linh kiện (MPN) rõ ràng  -> khoá chính là mã đó. Chắc ăn nhất.
2. Linh kiện thụ động            -> khoá là "<loại> <giá trị quy về đơn vị gốc>",
                                    nên 0.1uF và 100nF cho ra cùng một khoá.

Các thuộc tính phụ (package, điện áp, công suất, dung sai) tách ra `attrs`
chứ không nhét vào khoá — nếu nhét vào thì "10K 1/4W" sẽ không bao giờ khớp
"10K 0.25W", trong khi thực chất chúng là một.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------- bỏ dấu


def strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt. 'điện trở' -> 'dien tro'."""
    s = s.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# --------------------------------------------------------------- từ điển

# Cụm từ (đã bỏ dấu) cho biết LOẠI linh kiện. Cụm dài đặt trước cụm ngắn.
KIND_PHRASES: list[tuple[str, str]] = [
    ("dien tro nhiet", "thermistor"),
    ("dien tro", "resistor"),
    ("bien tro", "potentiometer"),
    ("tu dien", "capacitor"),
    ("tu gom", "capacitor"),
    ("tu hoa", "capacitor"),
    ("tu tantalum", "capacitor"),
    ("cuon cam", "inductor"),
    ("thach anh", "crystal"),
    ("resistor", "resistor"),
    ("capacitor", "capacitor"),
    ("inductor", "inductor"),
    ("crystal", "crystal"),
    ("xtal", "crystal"),
    ("diode", "diode"),
    ("mosfet", "mosfet"),
    ("transistor", "transistor"),
    ("led", "led"),
    ("tro", "resistor"),
    ("tu", "capacitor"),
]

# Đơn vị gốc của giá trị CHÍNH, theo từng loại.
KIND_MAIN_UNIT = {
    "resistor": "ohm",
    "potentiometer": "ohm",
    "thermistor": "ohm",
    "capacitor": "f",
    "inductor": "h",
    "crystal": "hz",
}

# Từ nhiễu: không phân biệt được linh kiện, bỏ trước khi so khớp.
NOISE_WORDS = {
    "ic", "chip", "vi", "dieu", "khien", "mcu", "module", "mach", "board",
    "kit", "linh", "kien", "dien", "tu", "con", "cai", "chiec", "bo", "loai",
    "chinh", "hang", "gia", "re", "tot", "cao", "cap", "moi", "new", "zin",
    "thao", "may", "chat", "luong", "sale", "hot", "freeship", "san", "pham",
    "hcm", "vn", "shop", "phan", "phoi", "cho", "va", "the", "kem", "tang",
    "chan", "cam", "gom", "hoa", "co", "hien", "thi", "ha", "ap", "giam",
}

PACKAGE_RE = re.compile(
    r"^("
    r"dip\d*|sop\d*|soic\d*|ssop\d*|tssop\d*|msop\d*|sod\d*|"
    r"qfp\d*|lqfp\d*|tqfp\d*|qfn\d*|dfn\d*|bga\d*|"
    r"to-?\d+[a-z]?|sot-?\d+[a-z]?|smd|smt|dip|dth|"
    r"0201|0402|0603|0805|1206|1210|2010|2512"
    r")$"
)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9./%+-]*")

# Mã linh kiện: có cả chữ lẫn số, >= 4 ký tự, không chứa '/' hay '%'.
MPN_RE = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9][a-z0-9+-]{3,}$")

# Một giá trị có đơn vị: 10k, 4.7uf, 1/4w, 100nf, 50v, 5%, 16mhz
VALUE_RE = re.compile(
    r"^(\d+(?:[./]\d+)?)"                       # số, cho phép phân số 1/4
    r"(p|n|u|m|k|meg|g)?"                       # tiền tố bội
    r"(ohm|r|f|h|hz|v|w|a|%|ppm)?$"             # đơn vị
)
# Ký hiệu kỹ thuật kiểu châu Âu: 4k7 = 4.7k, 1r5 = 1.5 ohm, 4u7 = 4.7uF
EU_VALUE_RE = re.compile(r"^(\d+)(r|k|meg|m|u|n|p)(\d+)$")

MULTIPLIER = {
    "": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6,
    "k": 1e3, "meg": 1e6, "g": 1e9,
}

_ENG_PREFIX = [
    (1e9, "g"), (1e6, "meg"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
]

_PUNCT_RE = re.compile("[()\\[\\]{}“”\"'`;:!?*]")

# Mọi token xuất hiện trong KIND_PHRASES. Khi đã xác định được loại linh kiện
# thì các token này thành thừa: "LED 5mm đỏ" cho khoá "led 5mm do", không phải
# "led 5mm do led".
KIND_TOKENS = {w for phrase, _ in KIND_PHRASES for w in phrase.split()}


@dataclass
class Parsed:
    """Kết quả bóc tách một tên linh kiện."""

    key: str                                  # khoá dùng để so khớp
    kind: str = ""                            # resistor / capacitor / mpn / ""
    mpn: str = ""
    value: float | None = None                # giá trị chính, đơn vị gốc (ohm/F/H)
    package: str = ""
    attrs: dict[str, str] = field(default_factory=dict)  # v, w, %, ...
    tokens: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.key


# ----------------------------------------------------------- hàm phụ trợ


def _clean(s: str) -> str:
    s = strip_accents(s).lower()
    s = s.replace("_", " ").replace("µ", "u").replace("ω", "ohm")
    s = s.replace(",", ".")
    s = _PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokenize(s: str) -> list[str]:
    return TOKEN_RE.findall(_clean(s))


def _num(text: str) -> float:
    """'1/4' -> 0.25 ; '4.7' -> 4.7"""
    if "/" in text:
        a, b = text.split("/", 1)
        return float(a) / float(b) if float(b) else 0.0
    return float(text)


def parse_value(token: str) -> tuple[float, str] | None:
    """'4.7uf' -> (4.7e-06, 'f') ; '10k' -> (10000.0, '') ; '4k7' -> (4700.0, '')

    Trả về None nếu token không phải một giá trị có đơn vị.
    """
    t = token.lower()

    m = EU_VALUE_RE.match(t)
    if m:
        whole, pre, frac = m.groups()
        pre = "" if pre == "r" else pre
        mult = MULTIPLIER.get(pre, 1.0)
        return (float(f"{whole}.{frac}") * mult, "")

    m = VALUE_RE.match(t)
    if not m:
        return None
    num, pre, unit = m.group(1), m.group(2) or "", m.group(3) or ""
    if not unit and not pre and "." not in num and "/" not in num:
        # số trần như "48", "100": chưa chắc là giá trị, để nơi gọi quyết định
        return (_num(num), "")
    if unit == "r":
        unit = "ohm"
    # 'm' nhập nhằng: với ohm/Hz nghĩa là mega, với F/H nghĩa là milli
    if pre == "m":
        mult = 1e6 if unit in ("ohm", "hz", "") else 1e-3
    else:
        mult = MULTIPLIER.get(pre, 1.0)
    return (_num(num) * mult, unit)


def format_eng(value: float, unit: str = "") -> str:
    """10000.0 -> '10k' ; 1e-07 -> '100n'. Phải ổn định vì dùng làm khoá."""
    if value == 0:
        return f"0{unit}"
    for scale, prefix in _ENG_PREFIX:
        if abs(value) >= scale * (1 - 1e-9):
            n = value / scale
            s = f"{n:.6f}".rstrip("0").rstrip(".")
            return f"{s}{prefix}{unit}"
    s = f"{value / 1e-12:.6f}".rstrip("0").rstrip(".")
    return f"{s}p{unit}"


def _detect_kind(clean_name: str) -> str:
    for phrase, kind in KIND_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", clean_name):
            return kind
    return ""


# --------------------------------------------------------------- API chính


def parse(name: str) -> Parsed:
    clean = _clean(name)
    tokens = TOKEN_RE.findall(clean)
    if not tokens:
        return Parsed(key="")

    package = ""
    rest: list[str] = []
    for t in tokens:
        # Kiểu chân được viết cả có lẫn không có gạch nối: "DIP-8" = "DIP8",
        # "TO-220" = "TO220". Bỏ gạch nối rồi mới so, và lưu dạng không gạch.
        flat = t.replace("-", "")
        if not package and PACKAGE_RE.match(flat):
            package = flat
        else:
            rest.append(t)

    # --- 1) mã linh kiện rõ ràng
    mpns = [t for t in rest if MPN_RE.match(t) and parse_value(t) is None]
    if mpns:
        mpn = max(mpns, key=len)
        return Parsed(key=mpn, kind="mpn", mpn=mpn, package=package,
                      tokens=tuple(tokens))

    # --- 2) linh kiện thụ động
    kind = _detect_kind(clean)
    main_unit = KIND_MAIN_UNIT.get(kind, "")
    value: float | None = None
    attrs: dict[str, str] = {}
    leftover: list[str] = []

    for t in rest:
        pv = parse_value(t)
        if pv is None:
            if t not in NOISE_WORDS and not (kind and t in KIND_TOKENS):
                leftover.append(t)
            continue
        mag, unit = pv
        has_letter = any(c.isalpha() or c == "%" for c in t)
        if unit and unit == main_unit:
            if value is None:
                value = mag
        elif not unit and main_unit and value is None and has_letter:
            # "10k" trong ngữ cảnh điện trở -> 10k ohm
            value = mag
        elif unit:
            attrs.setdefault(unit, format_eng(mag, "" if unit == "%" else unit))
        # số trần không đơn vị: bỏ, thường là "50" trong "50 cái"

    if kind and value is not None:
        key = f"{kind} {format_eng(value)}"
    elif kind and leftover:
        key = f"{kind} " + " ".join(sorted(set(leftover)))
    else:
        key = " ".join(sorted(set(leftover or rest)))

    return Parsed(key=key, kind=kind, value=value, package=package,
                  attrs=attrs, tokens=tuple(tokens))


def normalize(name: str) -> str:
    """Khoá chuẩn hoá của một tên linh kiện."""
    return parse(name).key


def similarity(a: str, b: str) -> float:
    """Độ giống 0..1 giữa hai tên, dùng khi khoá không trùng khít.

    Token là mã linh kiện được cho trọng số cao hơn hẳn token mô tả: trùng
    'stm32f103c8t6' có ý nghĩa hơn nhiều so với trùng 'module'.
    """
    pa, pb = parse(a), parse(b)
    if pa.key and pa.key == pb.key:
        return 1.0
    ta, tb = set(pa.tokens), set(pb.tokens)
    if not ta or not tb:
        return 0.0

    def w(t: str) -> float:
        if MPN_RE.match(t) and parse_value(t) is None:
            return 4.0
        if t in NOISE_WORDS:
            return 0.2
        return 1.0

    inter = sum(w(t) for t in ta & tb)
    union = sum(w(t) for t in ta | tb)
    return inter / union if union else 0.0
