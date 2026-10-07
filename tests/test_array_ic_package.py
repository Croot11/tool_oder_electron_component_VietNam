"""Điện trở/tụ BĂNG không được khớp với linh kiện đơn; IC giữ kiểu chân SMD/DIP.

Dữ liệu: tests/data/cxtvn_search_array.html — rút gọn từ kết quả tìm kiếm CXT
cho '100nF 0603', '2.2k 0603', 'PC817', 'ULN2803'.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder.bom_ibom import enrich                          # noqa: E402
from lkorder.browser import matcher as M                     # noqa: E402
from lkorder.browser.shops import cxtvn as X                 # noqa: E402
from lkorder.models import BomLine                           # noqa: E402
from lkorder.normalize import normalize                      # noqa: E402

HTML = (Path(__file__).resolve().parent / "data"
        / "cxtvn_search_array.html").read_text(encoding="utf-8")


def results(q: str) -> str:
    """Khối kết quả của một từ khoá trong file mẫu."""
    m = re.search(rf'<div class="ketqua" data-q="{re.escape(q)}">(.*?)\n</div>',
                  HTML, re.S)
    assert m, q
    return m.group(1)


def search(q: str):
    cands, zeros = X.parse_cxt_search(results(q))
    assert cands, q
    return cands, zeros


def bom(value: str, footprint: str, qty: int = 1) -> BomLine:
    e = enrich(value, footprint)
    return BomLine(key=normalize(e.name), qty=qty, raw=e.name)


def raw_line(raw: str) -> BomLine:
    return BomLine(key=normalize(raw), qty=1, raw=raw)


def by_title(cands, part: str):
    return next(c for c in cands if part in c.title)


class TestIsArray(unittest.TestCase):
    def test_array_titles(self):
        for t in ("Tụ 100nF 0603x4 50V (5c)", "Điện trở băng 2.2K 0603x4 5% (20c)",
                  "Điện trở 8P4R 10K", "Resistor network 10K", "R_Array 10k",
                  "Điện trở 10K 4x0603", "Tụ 0805x4 1uF", "RN 10K 0402x4"):
            self.assertTrue(M.is_array(t), t)

    def test_single_titles(self):
        for t in ("Tụ 100nF 0603 50V (50c)", "Điện trở 2.2K 0603 1% (50c)",
                  "Bảng mạch 0603", "Điện trở dán 10K 0603 (50c)", "PC817 DIP4"):
            self.assertFalse(M.is_array(t), t)


class TestArrayVsSingle(unittest.TestCase):
    def test_score_spec_rejects_cap_array(self):
        spec = M.line_spec(raw_line("Tụ 0603 100nF"))
        self.assertFalse(spec.array)
        s, why = M.score_spec(spec, "Tụ 100nF 0603x4 50V (5c)")
        self.assertEqual(s, 0.0)
        self.assertIn("băng", why)

    def test_score_spec_rejects_res_array(self):
        spec = M.line_spec(raw_line("Điện trở 0603 2.2K"))
        s, _ = M.score_spec(spec, "Điện trở băng 2.2K 0603x4 5% (20c)")
        self.assertEqual(s, 0.0)
        s, _ = M.score_spec(spec, "Điện trở 2.2K 0603 1% (50c)")
        self.assertGreaterEqual(s, M.ACCEPT_SCORE)

    def test_cap_100nf_0603_not_array(self):
        line = bom("104", "Capacitor_SMD:C_0603_1608Metric")
        self.assertEqual(line.raw, "Tụ 0603 100nF")
        cands, zeros = search("100nF 0603")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertEqual(by_title(dec.ranked, "0603x4").score, 0.0)
        self.assertEqual(dec.status, M.MATCH)
        self.assertNotIn("0603x4", dec.best.title)

    def test_res_2k2_0603_not_array(self):
        line = bom("2.2k", "Resistor_SMD:R_0603_1608Metric")
        cands, zeros = search("2.2k 0603")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertEqual(by_title(dec.ranked, "băng").score, 0.0)
        self.assertEqual(dec.status, M.MATCH)
        self.assertEqual(dec.best.title, "Điện trở 2.2K 0603 1% (50c)")

    def test_array_footprint_accepts_array(self):
        line = bom("2.2k", "Resistor_SMD:R_Array_Convex_4x0603")
        self.assertEqual(line.raw, "Điện trở băng 0603x4 2.2k")
        spec = M.line_spec(line)
        self.assertIsNotNone(spec)
        self.assertTrue(spec.array)
        cands, zeros = search("2.2k 0603")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertEqual(dec.status, M.MATCH)
        self.assertIn("băng", dec.best.title)
        single = by_title(dec.ranked, "1% (50c)")
        self.assertLess(single.score, M.ACCEPT_SCORE)

    def test_score_title_without_size(self):
        s, _ = M.score_title("Tụ 100nF", "Tụ 100nF 0603x4 50V (5c)")
        self.assertLess(s, M.CONSIDER_SCORE)


class TestEnrichPackage(unittest.TestCase):
    def test_keep_package(self):
        cases = {
            ("PC817_SMD", "MyLib:PC817_SMD"): "PC817 SMD",
            ("PC817", "MyLib:PC817_SMD"): "PC817 SMD",
            ("PC817", "Package_DIP:SMDIP-4_W9.53mm"): "PC817 SMD",
            ("PC817", "Package_DIP:DIP-4_W7.62mm"): "PC817 DIP-4",
            ("PC817", "Package_SO:SOP-4_4.4x2.6mm_P1.27mm"): "PC817 SOP-4",
            ("MAX485", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"): "MAX485 SOIC-8",
            ("ULN2803", "Package_SO:SOIC-18W_7.5x11.6mm_P1.27mm"): "ULN2803 SOIC-18",
            ("AMS1117-3.3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2"):
                "AMS1117-3.3 SOT-223",
        }
        for (v, fp), want in cases.items():
            self.assertEqual(enrich(v, fp).name, want, (v, fp))


class TestMount(unittest.TestCase):
    def test_mount_kind(self):
        self.assertEqual(X.mount_kind("PC817 DIP4 (5c)"), "dip")
        self.assertEqual(X.mount_kind("Opto PC817 SMD"), "smd")
        self.assertEqual(X.mount_kind("MAX485 SOIC-8"), "smd")
        self.assertEqual(X.mount_kind("SOT-23"), "smd")
        self.assertEqual(X.mount_kind("PC817"), "")

    def test_pc817_smd_not_dip(self):
        line = bom("PC817_SMD", "MyLib:PC817_SMD")
        cands, zeros = search("PC817")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertLess(by_title(dec.ranked, "DIP4").score, M.CONSIDER_SCORE)
        self.assertEqual(dec.status, M.MATCH)
        self.assertIn("SMD", dec.best.title)

    def test_pc817_dip(self):
        line = bom("PC817", "Package_DIP:DIP-4_W7.62mm")
        cands, zeros = search("PC817")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertEqual(dec.status, M.MATCH)
        self.assertIn("DIP4", dec.best.title)

    def test_uln2803_soic_takes_sop(self):
        line = bom("ULN2803", "Package_SO:SOIC-18W_7.5x11.6mm_P1.27mm")
        cands, zeros = search("ULN2803")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertEqual(dec.status, M.MATCH)
        self.assertIn("SOP18", dec.best.title)
        self.assertLess(by_title(dec.ranked, "DIP18").score, M.CONSIDER_SCORE)

    def test_uln2803_dip(self):
        line = bom("ULN2803", "Package_DIP:DIP-18_W7.62mm")
        cands, zeros = search("ULN2803")
        dec, _ = X.cxt_pick(line, cands, zeros)
        self.assertEqual(dec.status, M.MATCH)
        self.assertIn("DIP18", dec.best.title)

    def test_no_package_in_title_needs_check(self):
        """BOM ghi SMD mà tên shop không có kiểu chân: cần kiểm tra, không tự thêm."""
        line = bom("PC817_SMD", "MyLib:PC817_SMD")
        c = X.CxtCandidate(title="Opto PC817 (5c)",
                           url="https://linhkien.cxtvn.com/8300-pc817.html",
                           price=2000, pid="8300")
        dec, _ = X.cxt_pick(line, [c])
        self.assertEqual(dec.status, M.UNCERTAIN)
        self.assertIn("cần kiểm tra", dec.best.reason)


if __name__ == "__main__":
    unittest.main()
