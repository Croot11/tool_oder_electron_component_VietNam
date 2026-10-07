"""Đọc danh sách linh kiện cần mua (BOM).

Chấp nhận đúng thứ người ta hay có sẵn trong tay, không bắt phải theo mẫu:

    CSV/TSV có header    ten,so_luong,ghi_chu
    Mỗi dòng một món      STM32F103C8T6 x2
                          2 x STM32F103C8T6
                          STM32F103C8T6, 2
                          Điện trở 10K 1/4W    100
                          NE555            (không ghi số -> mặc định 1)

Dòng trống và dòng bắt đầu bằng # bị bỏ qua.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .models import BomLine
from .normalize import normalize

# Tên cột chấp nhận được, cho cả tiếng Việt lẫn tiếng Anh.
NAME_COLS = {"ten", "ten_lk", "tenlinhkien", "linhkien", "part", "name",
             "mpn", "component", "value", "ma", "product"}
QTY_COLS = {"soluong", "so_luong", "sl", "qty", "quantity", "amount", "count"}
REF_COLS = {"designator", "ref", "vitri", "vi_tri", "refdes", "kyhieu"}
NOTE_COLS = {"ghichu", "ghi_chu", "note", "notes", "mota", "comment"}
ALT_COLS = {"thay_the", "thaythe", "alt", "alternative", "tuongduong"}

# "STM32 x2" / "2 x STM32" / "STM32 * 2"
_TRAIL_QTY = re.compile(r"^(?P<name>.+?)\s*[x*]\s*(?P<qty>\d+)\s*$", re.I)
_LEAD_QTY = re.compile(r"^(?P<qty>\d+)\s*[x*]\s*(?P<name>.+?)\s*$", re.I)
# "STM32, 2" / "STM32   2" / "STM32;2"  — số lượng ở cuối, cách bởi dấu phân cách
_SEP_QTY = re.compile(r"^(?P<name>.+?)[,;\t]\s*(?P<qty>\d+)\s*$")
_SPACE_QTY = re.compile(r"^(?P<name>.+?)\s{2,}(?P<qty>\d+)\s*$")


def _norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", c.strip().lower().replace(" ", "_"))


def _looks_like_csv(text: str) -> bool:
    head = text.lstrip().splitlines()[:1]
    if not head:
        return False
    cols = {_norm_col(c) for c in re.split(r"[,;\t]", head[0])}
    return bool(cols & NAME_COLS) and bool(cols & QTY_COLS)


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


def parse_csv(text: str) -> list[BomLine]:
    sample = text[:2048]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            sample, delimiters=",;\t"
        )
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []

    cols = {_norm_col(c): c for c in reader.fieldnames}

    def pick(names: set[str]) -> str | None:
        for norm, orig in cols.items():
            if norm in names:
                return orig
        return None

    c_name, c_qty = pick(NAME_COLS), pick(QTY_COLS)
    c_ref, c_note, c_alt = pick(REF_COLS), pick(NOTE_COLS), pick(ALT_COLS)
    if not c_name:
        return parse_text(text)

    out: list[BomLine] = []
    for row in reader:
        name = (row.get(c_name) or "").strip()
        if not name or name.startswith("#"):
            continue
        try:
            qty = int(float((row.get(c_qty) or "1").strip() or 1)) if c_qty else 1
        except ValueError:
            qty = 1
        key = normalize(name)
        if not key:
            continue
        alts: tuple[str, ...] = ()
        if c_alt and (row.get(c_alt) or "").strip():
            alts = tuple(
                normalize(a) for a in re.split(r"[|/,]", row[c_alt]) if normalize(a)
            )
        note = (row.get(c_note) or "").strip() if c_note else ""
        ref = (row.get(c_ref) or "").strip() if c_ref else ""
        out.append(
            BomLine(key=key, qty=max(1, qty), raw=name, designator=ref,
                    alt_keys=alts)
        )
        if note:
            out[-1].raw = f"{name} ({note})" if note else name
    return out


def parse_any(text: str) -> list[BomLine]:
    """Đọc BOM từ chuỗi, tự nhận biết CSV có header hay danh sách tự do."""
    lines = parse_csv(text) if _looks_like_csv(text) else parse_text(text)
    return merge_duplicates(lines)


def load(path: str | Path) -> list[BomLine]:
    """Đọc BOM từ file."""
    return parse_any(Path(path).read_text(encoding="utf-8-sig"))


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
