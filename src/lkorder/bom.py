"""Đọc danh sách linh kiện cần mua (BOM).

Chấp nhận đúng thứ người ta hay có sẵn trong tay, không bắt phải theo mẫu:

    CSV/TSV có header    ten,so_luong,ghi_chu
                         BOM xuất từ KiCad (Reference, Value, Qty, ...)
                         BOM xuất từ Altium / LCSC (Comment, Description,
                         Designator, Quantity, Manufacturer Part Number, ...)
    File Excel .xlsx     sheet đầu tiên, tự tìm dòng tiêu đề
    Mỗi dòng một món      STM32F103C8T6 x2
                          2 x STM32F103C8T6
                          STM32F103C8T6, 2
                          Điện trở 10K 1/4W    100
                          NE555            (không ghi số -> mặc định 1)

Dòng trống và dòng bắt đầu bằng # bị bỏ qua.

File trông như bảng (có dòng tiêu đề) nhưng không nhận ra cột tên/số lượng
thì báo lỗi `BomFormatError` kèm danh sách tên cột được hỗ trợ, chứ không
đoán bừa thành văn bản tự do.

File CSV: đọc UTF-8 (có hoặc không BOM); không được thì thử cp1258 rồi
cp1252 và phát cảnh báo `BomEncodingWarning` để người dùng biết.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import BomLine
from .normalize import normalize


class BomFormatError(ValueError):
    """BOM không đọc được: sai định dạng, thiếu cột, sai encoding."""


class BomEncodingWarning(UserWarning):
    """File BOM không phải UTF-8, đã đọc bằng bảng mã dự phòng."""


# Cột tên linh kiện, theo thứ tự ưu tiên: nhóm trước thắng nhóm sau.
# Ưu tiên tính theo từng dòng: ô MPN trống thì lấy Comment/Value, rồi Description.
NAME_COL_GROUPS: tuple[tuple[str, ...], ...] = (
    # Mã nhà sản xuất — chính xác nhất để tìm hàng.
    ("mpn", "manufacturer_part_number", "manufacturer_part", "mfr_part_number",
     "mfr_part", "mfr_pn", "mfrpn", "part_number", "partnumber",
     "ma", "ma_lk", "ma_linh_kien"),
    # Tên / giá trị.
    ("ten", "ten_lk", "tenlinhkien", "ten_linh_kien", "linhkien", "linh_kien",
     "part", "name", "component", "product", "comment", "value", "gia_tri"),
    # Mô tả.
    ("description", "desc", "mota", "mo_ta"),
    # Mã LCSC (Cxxxx) — chỉ dùng khi không còn gì khác.
    ("lcsc_part", "lcsc_part_number", "lcsc", "lcsc_pn"),
)
NAME_COLS = {c for g in NAME_COL_GROUPS for c in g}
QTY_COLS = {"soluong", "so_luong", "sl", "qty", "quantity", "amount", "count",
            "qty_per_board", "quantity_per_board"}
REF_COLS = {"designator", "designators", "ref", "refs", "reference",
            "references", "vitri", "vi_tri", "refdes", "kyhieu", "ky_hieu"}
NOTE_COLS = {"ghichu", "ghi_chu", "note", "notes"}
ALT_COLS = {"thay_the", "thaythe", "alt", "alternative", "tuongduong",
            "tuong_duong"}
DNP_COLS = {"dnp", "do_not_populate", "do_not_place"}
KNOWN_COLS = NAME_COLS | QTY_COLS | REF_COLS | NOTE_COLS | ALT_COLS | DNP_COLS

SUPPORTED_COLS_HELP = (
    "  - Tên (ưu tiên từ trái sang): Manufacturer Part Number / MPN / Mã  >  "
    "Comment / Value / Tên / Part / Name  >  Description / Mô tả  >  LCSC Part\n"
    "  - Số lượng: Quantity / Qty / Số lượng / SL "
    "(không có thì đếm theo Designator / Reference / Ref)\n"
    "  - Tuỳ chọn: Ghi chú / Note, Thay thế / Alt, DNP"
)

# "STM32 x2" / "2 x STM32" / "STM32 * 2"
_TRAIL_QTY = re.compile(r"^(?P<name>.+?)\s*[x*]\s*(?P<qty>\d+)\s*$", re.I)
_LEAD_QTY = re.compile(r"^(?P<qty>\d+)\s*[x*]\s*(?P<name>.+?)\s*$", re.I)
# "STM32, 2" / "STM32   2" / "STM32;2"  — số lượng ở cuối, cách bởi dấu phân cách
_SEP_QTY = re.compile(r"^(?P<name>.+?)[,;\t]\s*(?P<qty>\d+)\s*$")
_SPACE_QTY = re.compile(r"^(?P<name>.+?)\s{2,}(?P<qty>\d+)\s*$")

_HEADER_SCAN_ROWS = 30      # tìm dòng tiêu đề trong chừng này dòng đầu
_DELIMS = ",;\t"


def _norm_col(c: str) -> str:
    """'Số lượng' -> 'so_luong', 'Manufacturer Part Number' -> 'manufacturer_part_number'."""
    s = (c or "").strip().lower().replace("đ", "d")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[\s\-./#]+", "_", s)
    return re.sub(r"[^a-z0-9_]", "", s).strip("_")


def _col_in(norm: str, names) -> bool:
    """Khớp tên cột; bỏ số cuối để 'Manufacturer Part Number 1' cũng nhận."""
    if not norm:
        return False
    if norm in names:
        return True
    stripped = re.sub(r"_?\d+$", "", norm)
    return bool(stripped) and stripped in names


# --------------------------------------------------------------------------
# Văn bản tự do

def parse_line(raw: str) -> BomLine | None:
    """Bóc một dòng tự do thành BomLine. Trả None nếu dòng rỗng/chú thích."""
    s = raw.strip()
    if not s or s.startswith("#"):
        return None

    qty = 1
    name = s
    for pat in (_LEAD_QTY, _TRAIL_QTY, _SEP_QTY, _SPACE_QTY):
        m = pat.match(s)
        if m:
            n = m.group("name").strip()
            # "1N4007 x10": phần tên phải còn lại thứ gì đó có nghĩa
            if n:
                name, qty = n, int(m.group("qty"))
                break

    key = normalize(name)
    if not key:
        return None
    return BomLine(key=key, qty=max(1, qty), raw=name)


def parse_text(text: str) -> list[BomLine]:
    return [ln for ln in (parse_line(r) for r in text.splitlines()) if ln]


# --------------------------------------------------------------------------
# Bảng (CSV / xlsx)

def _meaningful_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _guess_delimiter(text: str) -> str | None:
    lines = _meaningful_lines(text)[:_HEADER_SCAN_ROWS]
    if not lines:
        return None
    try:
        return csv.Sniffer().sniff("\n".join(lines), delimiters=_DELIMS).delimiter
    except csv.Error:
        pass
    counts = {d: sum(ln.count(d) for ln in lines) for d in _DELIMS}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else None


def _split_rows(text: str, delim: str) -> list[list[str]]:
    return [[c.strip() for c in row]
            for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _is_header(row: list[str]) -> bool:
    norms = [_norm_col(c) for c in row]
    has_name = any(_col_in(n, NAME_COLS) for n in norms)
    has_qty = any(_col_in(n, QTY_COLS | REF_COLS) for n in norms)
    return has_name and has_qty


def _find_header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:_HEADER_SCAN_ROWS]):
        if _is_header(row):
            return i
    return None


def _looks_like_header_row(row: list[str]) -> bool:
    """Dòng có dáng tiêu đề bảng dù không nhận ra đủ cột."""
    cells = [c for c in row if c.strip()]
    if len(cells) < 2:
        return False
    if any(re.fullmatch(r"[\d.,\s]+", c) for c in cells):
        return False      # tiêu đề không có ô toàn số ("STM32, 2" là dòng tự do)
    if any(_TRAIL_QTY.match(c) or _LEAD_QTY.match(c) for c in cells):
        return False      # "Tụ 100nF, 50V, 0805 x10" là dòng tự do
    if any(_col_in(_norm_col(c), KNOWN_COLS) for c in cells):
        return True
    return len(cells) >= 3


def _looks_tabular(rows: list[list[str]]) -> bool:
    rows = [r for r in rows if any(c.strip() for c in r)
            and not r[0].lstrip().startswith("#")]
    if not rows or not _looks_like_header_row(rows[0]):
        return False
    width = len(rows[0])
    if len(rows) == 1:
        return True
    same = sum(1 for r in rows[1:] if len(r) == width)
    return same >= max(1, (len(rows) - 1) // 2)


def _missing_cols_error(header: list[str] | None, source: str) -> BomFormatError:
    seen = ", ".join(f"'{c}'" for c in (header or []) if c.strip()) or "(không có)"
    return BomFormatError(
        f"Không nhận ra cột tên linh kiện / số lượng trong {source}.\n"
        f"Các cột đọc được: {seen}\n"
        f"Tên cột được hỗ trợ:\n{SUPPORTED_COLS_HELP}"
    )


def _parse_qty(cell: str) -> int | None:
    s = (cell or "").strip().replace(" ", "")
    if not s:
        return None
    # "1,000" kiểu Excel -> 1000; "2,0"/"2.0" -> 2
    if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
        s = s.replace(",", "")
    s = s.replace(",", ".")
    try:
        return int(float(s))
    except ValueError:
        return None


def _count_refs(cell: str) -> int:
    """'R1, R2, R5-R7' -> 5."""
    total = 0
    for part in re.split(r"[,;\s]+", cell.strip()):
        if not part:
            continue
        m = re.fullmatch(r"([A-Za-z_]+)(\d+)-(?:[A-Za-z_]+)?(\d+)", part)
        if m and int(m.group(3)) >= int(m.group(2)):
            total += int(m.group(3)) - int(m.group(2)) + 1
        else:
            total += 1
    return max(1, total)


_DNP_TRUE = {"1", "x", "yes", "y", "true", "dnp", "co"}


def parse_rows(rows: list[list[str]], source: str = "bảng") -> list[BomLine]:
    """Đọc BOM từ bảng (list các dòng). Tự tìm dòng tiêu đề.

    Raise BomFormatError nếu không nhận ra cột tên / số lượng.
    """
    hi = _find_header(rows)
    if hi is None:
        first = next((r for r in rows if any(c.strip() for c in r)
                      and not r[0].lstrip().startswith("#")), None)
        raise _missing_cols_error(first, source)
    norms = [_norm_col(c) for c in rows[hi]]

    def pick(names) -> int | None:
        return next((i for i, n in enumerate(norms) if _col_in(n, names)), None)

    name_idx = [i for g in NAME_COL_GROUPS if (i := pick(g)) is not None]
    c_qty, c_ref = pick(QTY_COLS), pick(REF_COLS)
    c_note, c_alt, c_dnp = pick(NOTE_COLS), pick(ALT_COLS), pick(DNP_COLS)

    def cell(row: list[str], i: int | None) -> str:
        return row[i].strip() if i is not None and i < len(row) else ""

    out: list[BomLine] = []
    for row in rows[hi + 1:]:
        if not any(c.strip() for c in row) or row[0].lstrip().startswith("#"):
            continue
        if c_dnp is not None and _norm_col(cell(row, c_dnp)) in _DNP_TRUE:
            continue
        name = key = ""
        for i in name_idx:          # ưu tiên MPN > Comment/Value > Description
            name = cell(row, i)
            key = normalize(name) if name else ""
            if key:
                break
        if not key:
            continue

        ref = cell(row, c_ref)
        qty = _parse_qty(cell(row, c_qty)) if c_qty is not None else None
        if qty is None:
            qty = _count_refs(ref) if ref else 1
        if qty <= 0:
            continue

        alts: tuple[str, ...] = ()
        alt_txt = cell(row, c_alt)
        if alt_txt:
            alts = tuple(normalize(a) for a in re.split(r"[|/,]", alt_txt)
                         if normalize(a))
        note = cell(row, c_note)
        out.append(BomLine(key=key, qty=qty,
                           raw=f"{name} ({note})" if note else name,
                           designator=ref, alt_keys=alts))
    return out


def parse_csv(text: str) -> list[BomLine]:
    """Đọc BOM dạng CSV/TSV có tiêu đề (dòng tiêu đề không nhất thiết ở đầu)."""
    text = text.lstrip("﻿")
    delim = _guess_delimiter(text) or ","
    return parse_rows(_split_rows(text, delim), source="file CSV")


def parse_any(text: str) -> list[BomLine]:
    """Đọc BOM từ chuỗi, tự nhận biết bảng có tiêu đề hay danh sách tự do.

    Raise BomFormatError nếu trông như bảng mà không nhận ra cột.
    """
    text = text.lstrip("﻿")
    delim = _guess_delimiter(text)
    if delim:
        rows = _split_rows(text, delim)
        if _find_header(rows) is not None or _looks_tabular(rows):
            return merge_duplicates(parse_rows(rows, source="file CSV"))
    return merge_duplicates(parse_text(text))


# --------------------------------------------------------------------------
# Encoding

FALLBACK_ENCODINGS = ("cp1258", "cp1252")


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Giải mã nội dung file BOM. Trả (text, encoding đã dùng).

    Thử UTF-8 (có/không BOM) trước, rồi cp1258 (tiếng Việt Windows),
    rồi cp1252. Không được thì raise BomFormatError.
    """
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError as e:
        pos = e.start
    for enc in FALLBACK_ENCODINGS:
        try:
            # cp1258 lưu dấu thanh dạng ký tự tổ hợp -> gộp lại (NFC)
            return unicodedata.normalize("NFC", data.decode(enc)), enc
        except UnicodeDecodeError:
            continue
    raise BomFormatError(
        f"Không giải mã được file BOM: không phải UTF-8 (lỗi ở byte {pos}), "
        f"cũng không phải {' / '.join(FALLBACK_ENCODINGS)}. "
        "Hãy lưu lại file dạng 'CSV UTF-8' rồi thử lại."
    )


# --------------------------------------------------------------------------
# Excel .xlsx (chỉ dùng thư viện chuẩn: zipfile + xml)

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _col_index(ref: str) -> int:
    """'A1' -> 0, 'AB12' -> 27."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _xml_text(el: ET.Element | None) -> str:
    """Ghép mọi <t> bên trong (hỗ trợ rich text <r><t>..</t></r>)."""
    if el is None:
        return ""
    return "".join(t.text or "" for t in el.iter(f"{{{_NS['m']}}}t"))


def _fmt_number(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    return str(int(f)) if f.is_integer() else v


def _resolve_zip_path(base: str, target: str) -> str:
    target = target.replace("\\", "/")
    path = target.lstrip("/") if target.startswith("/") else f"{base}/{target}"
    parts: list[str] = []
    for p in path.split("/"):
        if p == "..":
            if parts:
                parts.pop()
        elif p and p != ".":
            parts.append(p)
    return "/".join(parts)


def _first_sheet_path(z: zipfile.ZipFile) -> str:
    names = set(z.namelist())
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheet = wb.find("m:sheets/m:sheet", _NS)
        rid = sheet.get(f"{{{_NS['r']}}}id") if sheet is not None else None
        if rid:
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels.findall("pr:Relationship", _NS):
                if rel.get("Id") == rid:
                    path = _resolve_zip_path("xl", rel.get("Target", ""))
                    if path in names:
                        return path
    except (KeyError, ET.ParseError):
        pass
    sheets = sorted((n for n in names
                     if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if not sheets:
        raise BomFormatError("File .xlsx không có sheet nào.")
    return sheets[0]


def read_xlsx_rows(data: bytes | str | Path) -> list[list[str]]:
    """Đọc sheet đầu tiên của file .xlsx thành list các dòng (chuỗi)."""
    src = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        raise BomFormatError(f"File .xlsx hỏng hoặc không phải Excel: {e}") from e
    with z:
        try:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
                shared = [_xml_text(si) for si in sst.findall("m:si", _NS)]
            sheet = ET.fromstring(z.read(_first_sheet_path(z)))
        except ET.ParseError as e:
            raise BomFormatError(f"File .xlsx bị hỏng: {e}") from e

    rows: list[list[str]] = []
    for r in sheet.iterfind("m:sheetData/m:row", _NS):
        row: list[str] = []
        for c in r.findall("m:c", _NS):
            ref = c.get("r")
            idx = _col_index(ref) if ref else len(row)
            if idx < 0:
                idx = len(row)
            t = c.get("t", "n")
            v = c.find("m:v", _NS)
            raw = v.text if v is not None and v.text is not None else ""
            if t == "s":
                try:
                    val = shared[int(raw)]
                except (ValueError, IndexError):
                    val = ""
            elif t == "inlineStr":
                val = _xml_text(c.find("m:is", _NS))
            elif t == "b":
                val = "TRUE" if raw == "1" else "FALSE"
            elif t in ("str", "e"):
                val = raw
            else:
                val = _fmt_number(raw) if raw else ""
            while len(row) <= idx:
                row.append("")
            row[idx] = val.strip()
        rows.append(row)
    return rows


def parse_xlsx(data: bytes | str | Path) -> list[BomLine]:
    """Đọc BOM từ file .xlsx (sheet đầu, tự tìm dòng tiêu đề)."""
    return merge_duplicates(parse_rows(read_xlsx_rows(data), source="file .xlsx"))


# --------------------------------------------------------------------------

def load(path: str | Path) -> list[BomLine]:
    """Đọc BOM từ file (.csv, .tsv, .txt, .xlsx)."""
    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() in (".xlsx", ".xlsm") or data[:4] == b"PK\x03\x04":
        return parse_xlsx(data)
    if p.suffix.lower() == ".xls" or data[:4] == b"\xd0\xcf\x11\xe0":
        raise BomFormatError(
            f"{p.name}: định dạng Excel cũ (.xls) chưa hỗ trợ. "
            "Mở bằng Excel và lưu lại thành .xlsx hoặc CSV UTF-8."
        )
    text, enc = decode_bytes(data)
    if enc != "utf-8":
        warnings.warn(
            f"{p.name}: file không phải UTF-8, đã đọc bằng bảng mã {enc}. "
            "Kiểm tra lại tên linh kiện có dấu; nên lưu file dạng 'CSV UTF-8'.",
            BomEncodingWarning, stacklevel=2,
        )
    return parse_any(text)


def merge_duplicates(lines: list[BomLine]) -> list[BomLine]:
    """Gộp các dòng cùng một linh kiện lại, cộng số lượng.

    Rất hay gặp khi BOM xuất từ KiCad/Altium liệt kê từng designator một.
    """
    merged: dict[str, BomLine] = {}
    for ln in lines:
        cur = merged.get(ln.key)
        if cur is None:
            merged[ln.key] = ln
            continue
        cur.qty += ln.qty
        if ln.designator and ln.designator not in cur.designator:
            cur.designator = f"{cur.designator} {ln.designator}".strip()
    return list(merged.values())
