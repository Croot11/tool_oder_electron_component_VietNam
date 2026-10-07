"""Kiểm thử đọc BOM nhiều định dạng: KiCad, Altium/LCSC, .xlsx, CSV không UTF-8.

Chạy: python -m unittest discover -s tests
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lkorder import bom                                  # noqa: E402
from lkorder.normalize import normalize                  # noqa: E402


KICAD_CSV = (
    '"Reference","Value","Datasheet","Footprint","Qty","DNP"\n'
    '"C1,C2,C3","100nF","~","Capacitor_SMD:C_0805","3",""\n'
    '"R1,R2","10K","~","Resistor_SMD:R_0805","2",""\n'
    '"R9","1K","~","Resistor_SMD:R_0805","1","DNP"\n'
    '"U1","STM32F103C8T6","","Package_QFP:LQFP-48","1",""\n'
)

# KiCad xuất kiểu mỗi linh kiện một dòng, không có cột số lượng.
KICAD_PER_PART = (
    "Ref;Value;Footprint\n"
    "R1;10K;R_0805\n"
    "R2;10K;R_0805\n"
    "U1;NE555;SOIC-8\n"
)


def _by_key(lines):
    return {ln.key: ln for ln in lines}


def make_xlsx(rows: list[list[object]], shared: bool = True) -> bytes:
    """Dựng file .xlsx tối thiểu bằng zipfile (giống file Excel thật)."""
    strings: list[str] = []

    def col(i: int) -> str:
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    xml_rows = []
    for ri, row in enumerate(rows, start=1):
        cells = []
        for ci, v in enumerate(row):
            if v is None or v == "":
                continue
            ref = f"{col(ci)}{ri}"
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            elif shared:
                strings.append(str(v))
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>')
        xml_rows.append(f'<row r="{ri}">{"".join(cells)}</row>')

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "xl/workbook.xml",
            f'<workbook {ns} {rns}><sheets>'
            f'<sheet name="BOM" sheetId="1" r:id="rId7"/>'
            f'<sheet name="Khac" sheetId="2" r:id="rId8"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId8" Type="ws" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId7" Type="ws" Target="worksheets/sheet2.xml"/>'
            '</Relationships>',
        )
        # Sheet đầu tiên (theo workbook) nằm ở sheet2.xml; sheet1.xml là rác.
        z.writestr("xl/worksheets/sheet2.xml",
                   f'<worksheet {ns}><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="inlineStr">'
                   f'<is><t>khong phai BOM</t></is></c></row></sheetData></worksheet>')
        if shared:
            sst = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
            z.writestr("xl/sharedStrings.xml", f'<sst {ns}>{sst}</sst>')
    return buf.getvalue()


class TestKiCad(unittest.TestCase):
    def test_kicad_co_cot_qty_va_bo_dnp(self):
        lines = _by_key(bom.parse_any(KICAD_CSV))
        self.assertEqual(lines[normalize("100nF")].qty, 3)
        self.assertEqual(lines[normalize("10K")].qty, 2)
        self.assertEqual(lines["stm32f103c8t6"].qty, 1)
        self.assertNotIn(normalize("1K"), lines)          # DNP bị bỏ
        self.assertEqual(lines[normalize("100nF")].designator, "C1,C2,C3")

    def test_kicad_moi_dong_mot_linh_kien_dem_theo_ref(self):
        lines = _by_key(bom.parse_any(KICAD_PER_PART))
        self.assertEqual(lines[normalize("10K")].qty, 2)
        self.assertEqual(lines["ne555"].qty, 1)


class TestAltium(unittest.TestCase):
    def test_file_mau_altium(self):
        lines = _by_key(bom.load(ROOT / "data" / "bom_mau_altium.csv"))
        # MPN thắng Comment khi có
        self.assertIn(normalize("LM358DR2G"), lines)
        self.assertEqual(lines[normalize("LM358DR2G")].qty, 2)
        self.assertEqual(lines[normalize("LM358DR2G")].designator, "U2, U3")
        # MPN trống -> lấy Comment
        self.assertEqual(lines[normalize("10K")].qty, 4)
        self.assertEqual(lines[normalize("100nF")].qty, 6)
        self.assertEqual(len(lines), 8)

    def test_uu_tien_mpn_comment_description(self):
        text = (
            "Description,Comment,Designator,Quantity,MPN\n"
            "Mo ta A,NE555,U1,1,NE555DR\n"      # có MPN
            "Mo ta B,LM358,U2,1,\n"             # không MPN -> Comment
            "STM32F103C8T6,,U3,1,\n"            # chỉ có Description
        )
        keys = [ln.key for ln in bom.parse_any(text)]
        self.assertEqual(keys, [normalize("NE555DR"), "lm358", "stm32f103c8t6"])

    def test_manufacturer_part_number_va_value(self):
        text = "Value\tManufacturer Part Number 1\tQuantity\n10K\tRC0805FR-0710KL\t5\n"
        (ln,) = bom.parse_any(text)
        self.assertEqual(ln.key, normalize("RC0805FR-0710KL"))
        self.assertEqual(ln.qty, 5)

    def test_lcsc_jlcpcb(self):
        text = "Comment,Designator,Footprint,LCSC Part #\n,R1 R2,0805,C17414\nNE555,U1,SOIC-8,C7593\n"
        lines = _by_key(bom.parse_any(text))
        self.assertEqual(lines[normalize("C17414")].qty, 2)   # chỉ còn LCSC
        self.assertEqual(lines["ne555"].qty, 1)               # Comment thắng LCSC

    def test_tieu_de_khong_o_dong_dau(self):
        text = (
            "Bill of Materials,,,\n"
            "Project: KZ-E02,,,\n"
            ",,,\n"
            "Comment,Designator,Quantity,Footprint\n"
            "NE555,U1,1,SOIC-8\n"
        )
        (ln,) = bom.parse_any(text)
        self.assertEqual((ln.key, ln.qty), ("ne555", 1))


class TestXlsx(unittest.TestCase):
    ROWS = [
        ["Bill of Materials - KZ-E02"],
        [],
        ["Comment", "Description", "Designator", "Quantity", "Manufacturer Part Number"],
        ["STM32F103C8T6", "MCU", "U1", 1, "STM32F103C8T6"],
        ["10K", "Điện trở 10K 0805", "R1, R2, R3", 3.0, ""],
        ["LM358", "Op-amp", "U2, U3", 2, "LM358DR2G"],
        ["", "", "", "", ""],
    ]

    def _check(self, lines):
        d = _by_key(lines)
        self.assertEqual(d["stm32f103c8t6"].qty, 1)
        self.assertEqual(d[normalize("10K")].qty, 3)
        self.assertEqual(d[normalize("LM358DR2G")].qty, 2)
        self.assertEqual(len(d), 3)

    def test_xlsx_shared_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bom.xlsx"
            p.write_bytes(make_xlsx(self.ROWS, shared=True))
            self._check(bom.load(p))

    def test_xlsx_inline_strings(self):
        self._check(bom.parse_xlsx(make_xlsx(self.ROWS, shared=False)))

    def test_xlsx_thieu_cot_bao_loi(self):
        data = make_xlsx([["Linh tinh", "Footprint"], ["NE555", "SOIC-8"]])
        with self.assertRaises(bom.BomFormatError) as cm:
            bom.parse_xlsx(data)
        self.assertIn("Manufacturer Part Number", str(cm.exception))

    def test_xlsx_hong(self):
        with self.assertRaises(bom.BomFormatError):
            bom.parse_xlsx(b"PK\x03\x04 khong phai zip")


class TestEncoding(unittest.TestCase):
    TEXT = "ten,so_luong,ghi_chu\nĐiện trở 10K,100,dán\nNE555,2,\n"

    def _write(self, data: bytes) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name) / "bom.csv"
        p.write_bytes(data)
        return p

    def test_utf8_va_utf8_bom(self):
        for data in (self.TEXT.encode("utf-8"), self.TEXT.encode("utf-8-sig")):
            with warnings.catch_warnings():
                warnings.simplefilter("error")          # không được cảnh báo
                lines = bom.load(self._write(data))
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].raw.startswith("Điện trở 10K"))

    def test_cp1258_co_canh_bao(self):
        # cp1258: "ỏ" lưu thành "o" + dấu hỏi tổ hợp (0xD2)
        p = self._write("ten,so_luong\nNE555,2\nĐèn LED đỏ,5\n".encode("cp1258"))
        with self.assertWarns(bom.BomEncodingWarning) as cm:
            lines = bom.load(p)
        self.assertIn("cp1258", str(cm.warning))
        self.assertEqual(lines[1].raw, "Đèn LED đỏ")

    def test_cp1252_khi_cp1258_khong_doc_duoc(self):
        # 0x8A (Š) không có trong cp1258 nhưng có trong cp1252
        p = self._write("ten,so_luong\nNE555,2\nŠkoda relay,1\n".encode("cp1252"))
        with self.assertWarns(bom.BomEncodingWarning) as cm:
            lines = bom.load(p)
        self.assertIn("cp1252", str(cm.warning))
        self.assertEqual(len(lines), 2)

    def test_decode_bytes(self):
        self.assertEqual(bom.decode_bytes("abc".encode("utf-8-sig")), ("abc", "utf-8"))


class TestCotKhongNhanRa(unittest.TestCase):
    def test_bang_khong_co_cot_ten_bao_loi(self):
        text = "Item,Footprint,Supplier\n1,SOIC-8,Digikey\n2,0805,Mouser\n"
        with self.assertRaises(bom.BomFormatError) as cm:
            bom.parse_any(text)
        msg = str(cm.exception)
        self.assertIn("'Item'", msg)
        for col in ("MPN", "Comment", "Description", "LCSC Part", "Quantity"):
            self.assertIn(col, msg)

    def test_co_cot_ten_nhung_khong_co_so_luong_bao_loi(self):
        with self.assertRaises(bom.BomFormatError):
            bom.parse_any("Comment,Footprint,Supplier\nNE555,SOIC-8,LCSC\n")

    def test_parse_csv_bao_loi_thay_vi_doc_tu_do(self):
        with self.assertRaises(bom.BomFormatError):
            bom.parse_csv("a,b,c\nx,y,z\n")

    def test_van_ban_tu_do_khong_bi_coi_la_bang(self):
        for text, n in [
            ("STM32F103C8T6, 2\nNE555, 5\n", 2),
            ("Tụ 100nF, 50V, 0805 x10\nLED, đỏ, 5mm x20\n", 2),
            ("Điện trở 10K, 1/4W, 100\nNE555\n", 2),
        ]:
            lines = bom.parse_any(text)
            self.assertEqual(len(lines), n, text)

    def test_file_mau_van_ban(self):
        lines = bom.load(ROOT / "data" / "bom_mau.txt")
        self.assertEqual(len(lines), 10)


if __name__ == "__main__":
    unittest.main()
