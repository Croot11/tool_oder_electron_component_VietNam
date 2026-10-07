"""Kiểm thử đọc BOM từ ibom.html (KiCad InteractiveHtmlBom).

tests/data/ibom_mau.html là file mẫu nhỏ tự tạo (không phải thiết kế thật),
dữ liệu pcbdata nén bằng LZString y như plugin thật.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder import bom, bom_ibom                       # noqa: E402
from lkorder.bom_ibom import enrich                     # noqa: E402

MAU = Path(__file__).resolve().parent / "data" / "ibom_mau.html"


class TestLZString(unittest.TestCase):
    # Vector sinh bằng thư viện lz-string 1.5.0 (JS) gốc.
    VECTORS = [
        ("a", "IZA="),
        ("Hello, world", "BIUwNmD2A0AEDukBOYAmQ==="),
    ]

    def test_giai_nen_khop_lz_string_js(self):
        for plain, b64 in self.VECTORS:
            self.assertEqual(bom_ibom.decompress_from_base64(b64), plain)
            self.assertEqual(bom_ibom.compress_to_base64(plain), b64)

    def test_khu_hoi_unicode_va_chuoi_dai(self):
        for s in ["Tụ 100nF Ω 😀", "x" * 5000 + ",".join(map(str, range(3000))),
                  json.dumps({"bom": {"both": [[["C1", 0]]]}})]:
            self.assertEqual(
                bom_ibom.decompress_from_base64(bom_ibom.compress_to_base64(s)), s)

    def test_chuoi_rong(self):
        self.assertEqual(bom_ibom.decompress_from_base64(""), "")


class TestEIA(unittest.TestCase):
    def test_tu(self):
        self.assertEqual(bom_ibom.eia_cap("104"), "100nF")
        self.assertEqual(bom_ibom.eia_cap("222"), "2.2nF")
        self.assertEqual(bom_ibom.eia_cap("101"), "100pF")
        self.assertEqual(bom_ibom.eia_cap("105"), "1uF")
        self.assertEqual(bom_ibom.eia_cap("107"), "100uF")

    def test_dien_tro(self):
        self.assertEqual(bom_ibom.eia_res("103"), "10K")
        self.assertEqual(bom_ibom.eia_res("472"), "4.7K")
        self.assertEqual(bom_ibom.eia_res("1002"), "10K")
        self.assertEqual(bom_ibom.eia_res("105"), "1M")
        self.assertEqual(bom_ibom.eia_res("101"), "100Ω")

    def test_cuon_cam(self):
        self.assertEqual(bom_ibom.eia_ind("4R7"), "4.7uH")
        self.assertEqual(bom_ibom.eia_ind("101"), "100uH")


class TestEnrich(unittest.TestCase):
    def name(self, value, fp):
        return enrich(value, fp).name

    def test_tu_dien_tro_theo_kich_thuoc(self):
        self.assertEqual(self.name("104", "Capacitor_SMD:C_0603_1608Metric"), "Tụ 0603 100nF")
        self.assertEqual(self.name("100n", "Capacitor_SMD:C_0805_2012Metric"), "Tụ 0805 100nF")
        self.assertEqual(self.name("103", "Resistor_SMD:R_0805_2012Metric"), "Điện trở 0805 10K")
        self.assertEqual(self.name("10k", "Resistor_SMD:R_0603_1608Metric"), "Điện trở 0603 10k")
        # "220" trên điện trở: người vẽ ghi thẳng ohm, không phải mã EIA 22Ω
        self.assertEqual(self.name("220", "Resistor_SMD:R_0603_1608Metric"), "Điện trở 0603 220Ω")

    def test_tu_hoa_tantal(self):
        self.assertEqual(self.name("470uF", "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm"), "Tụ hoá 470uF")
        self.assertEqual(self.name("22uF", "Capacitor_SMD:CP_Elec_5x5.4"), "Tụ hoá 22uF")
        self.assertTrue(self.name("10uF", "Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A")
                        .startswith("Tụ tantal"))

    def test_ferrite_va_cuon_cam(self):
        self.assertEqual(self.name("121", "Inductor_SMD:L_0603_1608Metric"), "Ferrite bead 0603 120Ω")
        self.assertEqual(self.name("10uH", "Inductor_SMD:L_1210_3225Metric"), "Cuộn cảm 1210 10uH")

    def test_led_diode_ic(self):
        self.assertEqual(self.name("LED_Red", "LED_THT:LED_D3.0mm"), "LED 3mm Red")
        self.assertEqual(self.name("LED", "LED_THT:LED_D5.0mm"), "LED 5mm")
        self.assertEqual(self.name("SS34", "Diode_SMD:D_SMA"), "Diode SS34 SMA")
        self.assertEqual(self.name("M7", "Diode_SMD:D_SMB"), "Diode M7 SMB")
        self.assertEqual(self.name("MMBT3904", "Package_TO_SOT_SMD:SOT-23"), "MMBT3904 SOT-23")
        self.assertEqual(self.name("SP3485", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), "SP3485 SOIC-8")
        self.assertEqual(self.name("STM32F407ZGTx", "Package_QFP:LQFP-144_20x20mm_P0.5mm"),
                         "STM32F407ZGTx LQFP-144")
        # đã có kiểu chân trong tên thì không thêm lần nữa
        self.assertEqual(self.name("LM2596S-5.0 TO-263", "Package_TO_SOT_SMD:TO-263-5_TabPin3"),
                         "LM2596S-5.0 TO-263")

    def test_bo_qua_lo_vit_test_point_fiducial(self):
        for v, fp in [("MountingHole", "MountingHole:MountingHole_3.2mm_M3"),
                      ("TestPoint", "TestPoint:TestPoint_Pad_D1.0mm"),
                      ("Fiducial", "Fiducial:Fiducial_1mm_Mask2mm")]:
            self.assertTrue(enrich(v, fp).skip, fp)

    def test_value_vo_nghia_lay_ten_tu_footprint(self):
        e = enrich("~", "MauLib:MAX485_SMD")
        self.assertEqual((e.name, e.check), ("MAX485", True))
        e = enrich("~", "MauLib:Varistor_MOV-07D271K")
        self.assertEqual((e.name, e.check), ("Varistor 07D271K", True))
        e = enrich("Conn_01x04", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
        self.assertTrue(e.check)
        self.assertIn("1x04", e.name)


class TestDocFileMau(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = bom.load(MAU)
        cls.by_ref = {}
        for ln in cls.lines:
            for r in ln.designator.split():
                cls.by_ref[r] = ln

    def test_nhan_dien_ibom(self):
        text = MAU.read_text(encoding="utf-8")
        self.assertTrue(bom_ibom.looks_like_ibom(text))
        self.assertFalse(bom_ibom.looks_like_ibom("STM32F103C8T6 x2"))
        # parse_any cũng tự nhận ra (đường đi của web UI)
        self.assertEqual(len(bom.parse_any(text)), len(self.lines))

    def test_doc_cot_theo_config_fields(self):
        parts = bom_ibom.read_parts(MAU.read_text(encoding="utf-8"))
        c1 = next(p for p in parts if "C1" in p.refs)
        self.assertEqual((c1.value, c1.footprint, c1.qty),
                         ("104", "Capacitor_SMD:C_0603_1608Metric", 3))

    def test_so_luong_va_ten(self):
        c1 = self.by_ref["C1"]
        self.assertEqual((c1.raw, c1.qty, c1.designator), ("Tụ 0603 100nF", 3, "C1 C2 C3"))
        self.assertEqual(c1.key, "capacitor 100n")
        self.assertEqual(self.by_ref["C4"].raw, "Tụ 0603 2.2nF")
        self.assertEqual(self.by_ref["C5"].raw, "Tụ 0402 100pF")
        self.assertEqual(self.by_ref["R1"].raw, "Điện trở 0805 10K")
        self.assertEqual(self.by_ref["L1"].raw, "Ferrite bead 0603 120Ω")
        self.assertEqual(self.by_ref["D1"].raw, "LED 3mm Red")
        self.assertEqual(self.by_ref["U3"].raw, "AMS1117-3.3 SOT-223")

    def test_bo_dnp(self):
        self.assertNotIn("R5", self.by_ref)          # cả nhóm DNP
        self.assertNotIn("R7", self.by_ref)          # một con trong nhóm DNP
        self.assertEqual(self.by_ref["R6"].qty, 1)

    def test_bo_lo_vit_test_point_fiducial(self):
        for r in ("H1", "H2", "TP1", "FID1"):
            self.assertNotIn(r, self.by_ref)

    def test_danh_dau_can_kiem_tra(self):
        self.assertEqual(self.by_ref["U2"].raw, "MAX485 (cần kiểm tra)")
        self.assertEqual(self.by_ref["U2"].key, "max485")
        self.assertEqual(self.by_ref["RV1"].raw, "Varistor 07D271K (cần kiểm tra)")
        self.assertIn("cần kiểm tra", self.by_ref["J1"].raw)
        self.assertNotIn("cần kiểm tra", self.by_ref["U1"].raw)

    def test_khong_gop_cung_gia_tri_khac_kich_thuoc(self):
        text = MAU.read_text(encoding="utf-8")
        pcb = bom_ibom.extract_pcbdata(text)
        fields = pcb["bom"]["fields"]
        nid = str(len(fields))
        fields[nid] = ["104", "Capacitor_SMD:C_0805_2012Metric"]
        pcb["bom"]["both"].append([["C99", int(nid)]])
        b64 = bom_ibom.compress_to_base64(json.dumps(pcb))
        start = text.index('decompressFromBase64("') + len('decompressFromBase64("')
        end = text.index('"', start)
        lines = bom.parse_any(text[:start] + b64 + text[end:])
        names = {ln.raw for ln in lines}
        self.assertIn("Tụ 0603 100nF", names)
        self.assertIn("Tụ 0805 100nF", names)

    def test_dinh_dang_cu_khong_nen(self):
        old = {"bom": {"both": [[2, "104", "C_0603_1608Metric", [["C1", 0], ["C2", 1]], []]],
                       "skipped": []}}
        text = "<script>\nvar pcbdata = " + json.dumps(old) + "\n</script>"
        lines = bom_ibom.parse_ibom(text)
        self.assertEqual([(ln.raw, ln.qty) for ln in lines], [("Tụ 0603 100nF", 2)])


if __name__ == "__main__":
    unittest.main()
