"""Đọc BOM từ file ibom.html (plugin KiCad InteractiveHtmlBom).

File ibom.html nhúng toàn bộ dữ liệu mạch trong một dòng:

    var pcbdata = JSON.parse(LZString.decompressFromBase64("..."))

Module này tự giải nén LZString bằng Python thuần (không cần thư viện ngoài),
lấy `bom.both` + `bom.fields` (thứ tự cột theo `config.fields`, thường là
Value và Footprint), bỏ các linh kiện trong `bom.skipped` (DNP).

Sau đó "làm giàu" tên để tìm được trên shop VN: KiCad chỉ ghi "104" với
footprint "C_0603_1608Metric", còn shop bán "Tụ 0603 100nF".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import BomLine
from .normalize import normalize

CAN_KIEM_TRA = "cần kiểm tra"

# ------------------------------------------------------------ LZString

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_B64_IDX = {c: i for i, c in enumerate(_B64)}


def _fix_surrogates(s: str) -> str:
    """JS ghép ký tự ngoài BMP bằng cặp surrogate; Python cần gộp lại."""
    try:
        return s.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        return s


def decompress_from_base64(data: str) -> str:
    """Tương đương LZString.decompressFromBase64 của JS."""
    data = re.sub(r"\s+", "", data or "")
    if not data:
        return ""
    out = _decompress(len(data), 32, lambda i: _B64_IDX[data[i]])
    if out is None:
        raise ValueError("Dữ liệu LZString hỏng")
    return _fix_surrogates(out)


def _decompress(length: int, reset_value: int, get_next) -> str | None:
    dictionary: dict[int, str] = {0: "", 1: "", 2: ""}
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    result: list[str] = []
    val = get_next(0)
    position = reset_value
    index = 1

    def read(n: int) -> int:
        nonlocal val, position, index
        bits = 0
        power = 1
        maxpower = 1 << n
        while power != maxpower:
            resb = val & position
            position >>= 1
            if position == 0:
                position = reset_value
                val = get_next(index) if index < length else 0
                index += 1
            if resb:
                bits |= power
            power <<= 1
        return bits

    nxt = read(2)
    if nxt == 0:
        c = chr(read(8))
    elif nxt == 1:
        c = chr(read(16))
    else:
        return ""
    dictionary[3] = c
    w = c
    result.append(c)

    while True:
        if index > length:
            return ""
        cc = read(num_bits)
        if cc in (0, 1):
            dictionary[dict_size] = chr(read(8 if cc == 0 else 16))
            dict_size += 1
            cc = dict_size - 1
            enlarge_in -= 1
        elif cc == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

        if cc in dictionary and dictionary[cc]:
            entry = dictionary[cc]
        elif cc == dict_size:
            entry = w + w[0]
        else:
            return None
        result.append(entry)
        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1


def compress_to_base64(text: str) -> str:
    """Tương đương LZString.compressToBase64. Dùng để tạo file mẫu/kiểm thử."""
    # JS làm việc trên đơn vị UTF-16
    units = text.encode("utf-16-le", "surrogatepass")
    chars = [chr(units[i] | (units[i + 1] << 8)) for i in range(0, len(units), 2)]
    res = _compress(chars, 6, lambda v: _B64[v])
    return res + "=" * ((4 - len(res) % 4) % 4)


def _compress(chars: list[str], bits_per_char: int, get_char) -> str:
    dictionary: dict[str, int] = {}
    to_create: set[str] = set()
    w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2
    out: list[str] = []
    data_val = 0
    data_pos = 0

    def write_bits(value: int, n: int) -> None:
        nonlocal data_val, data_pos
        for _ in range(n):
            data_val = (data_val << 1) | (value & 1)
            if data_pos == bits_per_char - 1:
                data_pos = 0
                out.append(get_char(data_val))
                data_val = 0
            else:
                data_pos += 1
            value >>= 1

    def emit_w() -> None:
        nonlocal enlarge_in, num_bits
        if w in to_create:
            code = ord(w[0])
            if code < 256:
                write_bits(0, num_bits)
                write_bits(code, 8)
            else:
                write_bits(1, num_bits)
                write_bits(code, 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 1 << num_bits
                num_bits += 1
            to_create.discard(w)
        else:
            write_bits(dictionary[w], num_bits)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

    for c in chars:
        if c not in dictionary:
            dictionary[c] = dict_size
            dict_size += 1
            to_create.add(c)
        wc = w + c
        if wc in dictionary:
            w = wc
        else:
            emit_w()
            dictionary[wc] = dict_size
            dict_size += 1
            w = c

    if w != "":
        emit_w()

    write_bits(2, num_bits)
    # đẩy nốt bit còn dư
    while True:
        data_val <<= 1
        if data_pos == bits_per_char - 1:
            out.append(get_char(data_val))
            break
        data_pos += 1
    return "".join(out)


# ------------------------------------------------------------ đọc file

_PCBDATA_LZ = re.compile(
    r"var\s+pcbdata\s*=\s*JSON\.parse\(\s*LZString\.decompressFromBase64\(\s*"
    r"[\"']([A-Za-z0-9+/=\s]*)[\"']\s*\)\s*\)"
)
_PCBDATA_STR = re.compile(r"var\s+pcbdata\s*=\s*JSON\.parse\(\s*(\"(?:[^\"\\]|\\.)*\")\s*\)")
_PCBDATA_OBJ = re.compile(r"var\s+pcbdata\s*=\s*(\{.*?\})\s*;?\s*$", re.M)
_CONFIG = re.compile(r"var\s+config\s*=\s*(\{.*?\})\s*;?\s*$", re.M)


def looks_like_ibom(text: str) -> bool:
    return bool(re.search(r"var\s+pcbdata\s*=", text))


def extract_pcbdata(text: str) -> dict:
    m = _PCBDATA_LZ.search(text)
    if m:
        return json.loads(decompress_from_base64(m.group(1)))
    m = _PCBDATA_STR.search(text)
    if m:
        return json.loads(json.loads(m.group(1)))
    m = _PCBDATA_OBJ.search(text)
    if m:
        return json.loads(m.group(1))
    raise ValueError("Không tìm thấy 'var pcbdata = ...' trong file ibom.html")


def extract_config_fields(text: str) -> list[str]:
    m = _CONFIG.search(text)
    if m:
        try:
            fields = json.loads(m.group(1)).get("fields")
            if isinstance(fields, list) and fields:
                return [str(f) for f in fields]
        except (ValueError, AttributeError):
            pass
    return ["Value", "Footprint"]


@dataclass
class IbomPart:
    """Một nhóm linh kiện giống nhau trong ibom (cùng Value + Footprint)."""

    value: str
    footprint: str
    refs: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def qty(self) -> int:
        return len(self.refs)


def _fields_of(fields: dict, fid) -> list:
    v = fields.get(str(fid))
    if v is None:
        v = fields.get(fid)
    return v or []


def read_parts(text: str) -> list[IbomPart]:
    """Lấy danh sách nhóm linh kiện (đã bỏ DNP) từ nội dung ibom.html."""
    pcb = extract_pcbdata(text)
    bom = pcb.get("bom") or {}
    names = extract_config_fields(text)
    fields = bom.get("fields") or {}
    skipped = {str(x) for x in (bom.get("skipped") or [])}

    def col(vals: list, name: str) -> str:
        for i, n in enumerate(names):
            if n.lower() == name.lower() and i < len(vals):
                return str(vals[i] if vals[i] is not None else "").strip()
        return ""

    parts: list[IbomPart] = []
    for group in bom.get("both") or []:
        if not group:
            continue
        if isinstance(group[0], (int, float)) and len(group) >= 4:
            # định dạng cũ: [qty, value, footprint, [[ref, id], ...], extra]
            refs = [r for r in group[3] if str(r[1]) not in skipped]
            if refs:
                parts.append(IbomPart(str(group[1]).strip(), str(group[2]).strip(),
                                      [str(r[0]) for r in refs]))
            continue
        refs = [r for r in group if str(r[1]) not in skipped]
        if not refs:
            continue
        vals = _fields_of(fields, refs[0][1])
        extra = {n: col(vals, n) for n in names
                 if n.lower() not in ("value", "footprint") and col(vals, n)}
        parts.append(IbomPart(col(vals, "Value"), col(vals, "Footprint"),
                              [str(r[0]) for r in refs], extra))
    return parts


# ------------------------------------------------------------ làm giàu tên

_SKIP_FP = re.compile(r"MountingHole|TestPoint|Fiducial", re.I)
_GENERIC_VALUE = re.compile(
    r"^(~|-|\?|Conn_\d+x\d+.*|Screw_Terminal_\d+x\d+.*|"
    r"(?:Pin)?Header.*_\d+x\d+.*|Jumper.*|SW_Push.*|Conn.*)$",
    re.I,
)

_SI = [(1e-12, "p"), (1e-9, "n"), (1e-6, "u"), (1e-3, "m"),
       (1.0, ""), (1e3, "K"), (1e6, "M")]


def _fmt(num: float, unit: str, prefixes: str) -> str:
    """Định dạng số theo tiền tố SI. prefixes giới hạn tiền tố được dùng."""
    best = None
    for mult, p in _SI:
        if p not in prefixes:
            continue
        if num / mult >= 1 - 1e-9:
            best = (mult, p)
    mult, p = best or next((m, q) for m, q in _SI if q in prefixes)
    v = round(num / mult, 3)
    s = f"{v:g}"
    return f"{s}{p}{unit}"


def eia_code(code: str) -> tuple[int, int] | None:
    """'104' -> (10, 4), '1002' -> (100, 2). Không phải mã EIA thì None."""
    m = re.fullmatch(r"(\d{2,3})(\d)", code.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def eia_cap(code: str) -> str | None:
    """Mã tụ EIA (pF): 104 -> 100nF, 222 -> 2.2nF, 101 -> 100pF."""
    p = eia_code(code)
    if not p or len(code.strip()) != 3:
        return None
    pf = p[0] * 10 ** p[1]
    return _fmt(pf * 1e-12, "F", "pnu")


def eia_res(code: str) -> str | None:
    """Mã điện trở EIA: 103 -> 10K, 472 -> 4.7K, 1002 -> 10K."""
    p = eia_code(code)
    if not p:
        return None
    ohm = p[0] * 10 ** p[1]
    s = _fmt(float(ohm), "", "KM ")
    return s if s[-1:] in ("K", "M") else f"{s}Ω"


def eia_ind(code: str) -> str | None:
    """Mã cuộn cảm EIA (uH): 100 -> 10uH, 4R7 -> 4.7uH, 101 -> 100uH."""
    c = code.strip().upper()
    if re.fullmatch(r"\d+R\d+|R\d+", c):
        return f"{float(c.replace('R', '.')):g}uH"
    p = eia_code(c)
    if not p or len(c) != 3:
        return None
    return _fmt(p[0] * 10 ** p[1] * 1e-6, "H", "nu m")


@dataclass
class Enriched:
    name: str
    check: bool = False      # đánh dấu 'cần kiểm tra'
    skip: bool = False       # lỗ bắt vít, test point, fiducial...


_PKG = re.compile(
    r"(SOT-\d+(?:-\d+)?|SOD-\d+|SOIC-\d+|SOP-\d+|SSOP-\d+|TSSOP-\d+|MSOP-\d+|"
    r"[LT]QFP-\d+|V?QFN-\d+|DFN-\d+|DIP-\d+|TO-\d+(?:-\d+)?|SMA|SMB|SMC|"
    r"MiniMELF|MELF|DO-\d+[A-Z]*)",
    re.I,
)
_FP_JUNK = {"smd", "tht", "handsolder", "handsoldering", "pad", "vertical",
            "horizontal", "tabpin2", "ep", "thermalvias", "smdpad"}


def _fp_name(footprint: str) -> tuple[str, str]:
    lib, _, name = footprint.rpartition(":")
    return lib, name


def _name_from_footprint(lib: str, fp: str) -> str:
    """Đoán tên linh kiện từ footprint khi Value vô nghĩa."""
    m = re.match(r"(?:Varistor|RV)_(?:MOV-)?(.+)", fp, re.I)
    if m:
        return f"Varistor {m.group(1).split('_')[0]}"
    m = re.match(r"Pin(Header|Socket)_(\d+x\d+)_P([\d.]+)mm", fp, re.I)
    if m:
        kind = "Header đực" if m.group(1).lower() == "header" else "Header cái"
        return f"{kind} {m.group(2)} {m.group(3)}mm"
    toks = [t for t in re.split(r"_", fp) if t]
    keep = [t for t in toks
            if t.lower() not in _FP_JUNK and not re.fullmatch(r"\d+Metric", t, re.I)]
    return " ".join(keep or toks) or fp


def _has_unit(value: str, units: str) -> bool:
    return bool(re.search(rf"\d\s*[pnumkKM]?\s*(?:{units})\b", value, re.I))


def enrich(value: str, footprint: str) -> Enriched:
    """Tên dễ tìm trên shop, suy từ Value + Footprint của KiCad."""
    value = (value or "").strip()
    lib, fp = _fp_name(footprint or "")
    if _SKIP_FP.search(footprint or "") or _SKIP_FP.search(value):
        return Enriched(value or fp, skip=True)

    if not value or _GENERIC_VALUE.match(value):
        if not fp:
            return Enriched(value or "?", check=True)
        return Enriched(_name_from_footprint(lib, fp), check=True)

    check = False
    lib_l, fp_l = lib.lower(), fp.lower()

    # ---- tụ tantal / tụ hoá / tụ gốm
    if "tantal" in lib_l or re.match(r"CP_Tantalum", fp, re.I):
        v = _with_unit(eia_cap(value) or value, "F")
        size = _tantal_size(fp)
        return Enriched(_join("Tụ tantal", size or _case(fp), v))
    if re.match(r"CP_(Elec|Radial|Axial)", fp, re.I):
        v = eia_cap(value) or value
        return Enriched(_join("Tụ hoá", v))
    m = re.match(r"C_(\d{4})_", fp)
    if m:
        v, check = _cap_value(value)
        return Enriched(_join("Tụ", m.group(1), v), check)

    # ---- điện trở
    m = re.match(r"R_(\d{4})_", fp)
    if m:
        v, check = _res_value(value)
        return Enriched(_join("Điện trở", m.group(1), v), check)

    # ---- cuộn cảm / ferrite
    m = re.match(r"L_(\d{4})_", fp)
    if m:
        size = m.group(1)
        if re.fullmatch(r"\d{3}", value):
            p = eia_code(value)
            ohm = p[0] * 10 ** p[1]
            return Enriched(_join("Ferrite bead", size, f"{ohm}Ω"))
        if re.search(r"ferrite|bead|\bFB\b", value, re.I):
            return Enriched(_join("Ferrite bead", size, value))
        v = value if _has_unit(value, "H") else (eia_ind(value) or value)
        return Enriched(_join("Cuộn cảm", size, _with_unit(v, "H")))
    if re.match(r"L_", fp) or "inductor" in lib_l:
        if not re.search(r"ferrite|bead|\bFB\b", value, re.I):
            v = value if _has_unit(value, "H") else (eia_ind(value) or value)
            return Enriched(_join("Cuộn cảm", _ind_size(fp), _with_unit(v, "H")))

    # ---- thạch anh
    if "crystal" in lib_l or re.match(r"Crystal|Resonator", fp, re.I):
        freq = _freq(value)
        size = _crystal_size(fp)
        if freq:
            return Enriched(_join("Thạch anh", size, freq))
        return Enriched(_join("Thạch anh", size, value), check=True)

    # ---- cầu chì
    if "fuse" in lib_l or re.match(r"Fuse_", fp, re.I):
        m = re.match(r"Fuse_(\d{4})_", fp, re.I)
        size = m.group(1) if m else ""
        amp = _amp(value)
        if amp:
            return Enriched(_join("Cầu chì", size, amp))
        return Enriched(_join("Cầu chì", size, value), check=True)

    # ---- LED
    m = re.match(r"LED_D(\d+(?:\.\d+)?)mm", fp, re.I)
    if m:
        return Enriched(_join("LED", f"{float(m.group(1)):g}mm", _drop(value, "LED")))
    m = re.match(r"LED_(\d{4})_", fp, re.I)
    if m:
        return Enriched(_join("LED", m.group(1), _drop(value, "LED")))

    # ---- diode
    m = re.match(r"D_(SMA|SMB|SMC|SOD-\d+|MiniMELF|MELF|DO-\d+[A-Z]*|PowerDI-\d+)", fp, re.I)
    if m:
        prefix = "" if re.search(r"diode|zener|tvs", value, re.I) else "Diode"
        return Enriched(_join(prefix, value, m.group(1)))

    # ---- IC / transistor: thêm kiểu chân nếu chưa có
    m = _PKG.search(fp)
    if m:
        pkg = m.group(1)
        if re.fullmatch(r"SOT-223-\d+", pkg, re.I):
            pkg = pkg[:7]
        base = re.sub(r"^((?:TO|SOT)-\d+)-\d+$", r"\1", pkg, flags=re.I)
        if base.upper() not in value.upper():
            return Enriched(_join(value, pkg.upper() if pkg[0].isalpha() else pkg))
    return Enriched(value, check)


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def _drop(value: str, word: str) -> str:
    return re.sub(rf"^\s*{word}(?![A-Za-z0-9])[\s_-]*", "", value, flags=re.I).strip()


def _case(fp: str) -> str:
    m = re.search(r"(?:Case|Kemet|AVX)-([A-Z])(?![A-Za-z])", fp)
    return f"case {m.group(1)}" if m else ""


_TANTAL_CASE = {"A": "3216", "B": "3528", "C": "6032", "D": "7343", "E": "7343"}


def _tantal_size(fp: str) -> str:
    """Mã kích thước hệ mét của tụ tantal: CP_EIA-3216-18_Kemet-A -> '3216'."""
    m = re.search(r"(?:EIA-|Tantalum_)(\d{4})", fp, re.I) or \
        re.search(r"(?<!\d)(3216|3528|6032|7343)(?!\d)", fp)
    if m:
        return m.group(1)
    m = re.search(r"Case-([A-E])(?![A-Za-z])", fp)
    return _TANTAL_CASE.get(m.group(1), "") if m else ""


def _ind_size(fp: str) -> str:
    """Kích thước cuộn cảm công suất: L_Bourns_SRN6045TA -> '6045'."""
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", fp)
    return m.group(1) if m else ""


def _crystal_size(fp: str) -> str:
    if re.search(r"HC-?49", fp, re.I):
        return "HC49 SMD" if re.search(r"SMD", fp, re.I) else "HC49"
    m = re.search(r"(?<!\d)(\d{4})-\d+Pin", fp, re.I) or \
        re.search(r"(?<!\d)(2016|2520|3225|5032|7050)(?!\d)", fp)
    return m.group(1) if m else ""


def _with_unit(v: str, unit: str) -> str:
    """'10u' -> '10uF' ; '10uF' giữ nguyên ; '10' giữ nguyên (không đoán)."""
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[pnum]", v, re.I):
        return v.replace(" ", "") + unit
    return v


def _freq(value: str) -> str:
    """Tần số thạch anh chuẩn hoá: '8M' -> '8MHz', '32.768K' -> '32.768kHz'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM])?(?:Hz|HZ|hz)", value)
    if not m:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM])?\s*", value)
    if not m:
        return ""
    num, pre = float(m.group(1)), (m.group(2) or "").lower()
    if not pre:
        if num >= 1000:
            num, pre = num / 1000, "k"
            if num >= 1000:
                num, pre = num / 1000, "m"
        else:
            pre = "m"           # '16.000' trên thạch anh: hầu như luôn là MHz
    return f"{num:g}{'MHz' if pre == 'm' else 'kHz'}"


def _amp(value: str) -> str:
    """Dòng định mức cầu chì: '500mA', '0.5A', 'Polyfuse_1.5A' -> chuẩn hoá."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(m)?A(?![a-zA-Z])", value)
    if not m:
        return ""
    num = float(m.group(1))
    if m.group(2):
        return f"{num:g}mA"
    return f"{num * 1000:g}mA" if num < 1 else f"{num:g}A"


def _cap_value(value: str) -> tuple[str, bool]:
    if re.fullmatch(r"\d{3}", value):
        if value.endswith("0"):
            # "100", "220": EIA (10pF, 22pF) hay ghi thẳng pF? Không chắc.
            return f"{int(value)}pF", True
        return eia_cap(value) or value, False
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[pnu]", value, re.I):
        return value + "F", False
    return value, False


def _res_value(value: str) -> tuple[str, bool]:
    if re.fullmatch(r"\d{3,4}", value) and not value.endswith("0"):
        return eia_res(value) or value, False
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        # "100", "220", "1000": người vẽ KiCad hầu như luôn ghi thẳng ohm
        return f"{value}Ω", False
    return value, False


# ------------------------------------------------------------ ra BomLine


def parse_ibom(text: str) -> list[BomLine]:
    out: list[BomLine] = []
    for part in read_parts(text):
        e = enrich(part.value, part.footprint)
        if e.skip:
            continue
        key = normalize(e.name)
        if not key:
            continue
        raw = f"{e.name} ({CAN_KIEM_TRA})" if e.check else e.name
        out.append(BomLine(key=key, qty=max(1, part.qty), raw=raw,
                           designator=" ".join(part.refs)))
    return out


def load(path: str | Path) -> list[BomLine]:
    return parse_ibom(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
