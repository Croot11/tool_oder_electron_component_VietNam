"""Tìm và khớp theo GIÁ TRỊ + KÍCH THƯỚC CHÂN (dòng BOM từ ibom.html).

Dữ liệu mẫu chép tay từ các dòng điển hình của BOM KZ_E32 (chỉ Value +
Footprint, không dùng file thiết kế thật).
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder import cli                                  # noqa: E402
from lkorder.bom_ibom import enrich                      # noqa: E402
from lkorder.browser import matcher as M                 # noqa: E402
from lkorder.models import BomLine                       # noqa: E402

MAU = Path(__file__).resolve().parent / "data" / "ibom_mau.html"

# (Value, Footprint) kiểu KZ_E32
KZ_E32 = {
    "C_100n": ("104", "Capacitor_SMD:C_0603_1608Metric"),
    "C_22p": ("22p", "Capacitor_SMD:C_0603_1608Metric"),
    "C_tantal": ("10uF", "Capacitor_Tantalum_SMD:CP_EIA-3216-18_Kemet-A"),
    "R_10k": ("10k", "Resistor_SMD:R_0603_1608Metric"),
    "R_2k2": ("2.2k", "Resistor_SMD:R_0603_1608Metric"),
    "R_4k7": ("4K7", "Resistor_SMD:R_0603_1608Metric"),
    "L_10u": ("10uH", "Inductor_SMD:L_1210_3225Metric"),
    "L_pwr": ("22uH", "Inductor_SMD:L_Bourns_SRN6045TA"),
    "Y_8M": ("8MHz", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm"),
    "F_500m": ("500mA", "Fuse:Fuse_1206_3216Metric"),
    "RELAY": ("HUIKE-HK4100F", "MauLib:Relay_HK4100F"),
}


def line(key: str) -> BomLine:
    v, fp = KZ_E32[key]
    return BomLine(key=key, qty=1, raw=enrich(v, fp).name)


def score(key: str, title: str) -> float:
    return M.score_candidate(line(key), M.Candidate(title, "https://x.vn/p")).score


class TestEnrich(unittest.TestCase):
    def test_du_gia_tri_va_kich_thuoc(self):
        self.assertEqual(line("C_tantal").raw, "Tụ tantal 3216 10uF")
        self.assertEqual(line("L_10u").raw, "Cuộn cảm 1210 10uH")
        self.assertEqual(line("L_pwr").raw, "Cuộn cảm 6045 22uH")
        self.assertEqual(line("Y_8M").raw, "Thạch anh 3225 8MHz")
        self.assertEqual(line("F_500m").raw, "Cầu chì 1206 500mA")
        self.assertEqual(enrich("8M", "Crystal:Crystal_HC49-U_Vertical").name,
                         "Thạch anh HC49 8MHz")
        self.assertEqual(enrich("10u", "Capacitor_Tantalum_SMD:"
                                "CP_Tantalum_Case-B_EIA-3528-21_Kemet-B").name,
                         "Tụ tantal 3528 10uF")


class TestSearchQueries(unittest.TestCase):
    def q(self, key):
        return M.search_queries(line(key))

    def test_gia_tri_kich_thuoc_khong_ten_loai(self):
        self.assertEqual(self.q("C_100n")[0], "100nF 0603")
        self.assertEqual(self.q("R_10k")[0], "10k 0603")
        self.assertEqual(self.q("R_2k2")[0], "2.2k 0603")
        self.assertEqual(self.q("C_tantal")[0], "10uF 3216")
        for key in KZ_E32:
            for q in self.q(key):
                low = q.lower()
                for word in ("tụ", "điện trở", "cuộn cảm", "thạch anh", "cầu chì"):
                    self.assertNotIn(word, low, (key, q))

    def test_cac_cach_viet_cung_gia_tri(self):
        self.assertEqual(self.q("C_100n"),
                         ["100nF 0603", "0.1uF 0603", "104 0603", "100n 0603"])
        self.assertEqual(set(self.q("C_22p")), {"22pF 0603", "22p 0603", "220 0603"})
        r10k = [q.lower() for q in self.q("R_10k")]
        self.assertIn("10k 0603", r10k)
        self.assertIn("10000 0603", r10k)
        r4k7 = self.q("R_4k7")
        self.assertIn("4.7k 0603", r4k7)
        self.assertIn("4K7 0603", r4k7)
        self.assertIn("10uF 1206", self.q("C_tantal"))      # 3216 = 1206

    def test_ma_bo_tien_to_hang(self):
        self.assertEqual(self.q("RELAY")[:2], ["HK4100F", "HUIKE-HK4100F"])
        self.assertEqual(M.strip_vendor("ESP32-WROOM-32"), "")

    def test_dong_khong_co_kich_thuoc_giu_cach_cu(self):
        ln = BomLine(key="x", qty=1, raw="Tụ gốm 100nF")
        self.assertEqual(M.search_queries(ln)[0], "tụ 100nF")


class TestKhopGiaTri(unittest.TestCase):
    def test_100nF_khop_moi_cach_viet(self):
        for t in ["Tụ 0603 100nF 50V", "Tụ gốm 0.1uF 0603", "Tụ 104 0603 (gói 100 con)",
                  "100n 0603 X7R", "Tụ 100nF/50V 0603", "Tụ dán 0.1UF 1608"]:
            self.assertGreaterEqual(score("C_100n", t), M.ACCEPT_SCORE, t)

    def test_100nF_khong_khop_10nF_1uF(self):
        for t in ["Tụ 0603 10nF", "Tụ 0603 1uF", "Tụ 103 0603", "Tụ 105 0603",
                  "Tụ 0603 0.01uF"]:
            self.assertEqual(score("C_100n", t), 0.0, t)

    def test_dien_tro(self):
        self.assertGreaterEqual(score("R_10k", "Điện trở 10K 0603 1%"), M.ACCEPT_SCORE)
        self.assertGreaterEqual(score("R_10k", "Trở dán 0603 10000 ohm"), M.ACCEPT_SCORE)
        self.assertGreaterEqual(score("R_4k7", "Điện trở 4.7K 0603"), M.ACCEPT_SCORE)
        self.assertGreaterEqual(score("R_4k7", "Điện trở 4K7 0603"), M.ACCEPT_SCORE)
        self.assertEqual(score("R_10k", "Điện trở 100K 0603"), 0.0)
        self.assertEqual(score("R_10k", "Điện trở 1K 0603"), 0.0)

    def test_22pF(self):
        for t in ["Tụ 22pF 0603", "Tụ 22p 0603", "Tụ 220 0603"]:
            self.assertGreaterEqual(score("C_22p", t), M.ACCEPT_SCORE, t)
        self.assertEqual(score("C_22p", "Tụ 0603 220pF"), 0.0)


class TestKhopKichThuoc(unittest.TestCase):
    def test_0603_khong_khop_0805_hay_cam(self):
        self.assertEqual(score("C_100n", "Tụ 100nF 0805"), 0.0)
        self.assertEqual(score("R_10k", "Điện trở 10K 0805"), 0.0)
        self.assertEqual(score("R_10k", "Điện trở 10K 1206"), 0.0)
        self.assertEqual(score("C_100n", "Tụ gốm 104 chân cắm"), 0.0)
        self.assertEqual(score("R_10k", "Điện trở cắm 10K 1/4W"), 0.0)

    def test_tantal_3216_bang_1206_va_case_A(self):
        self.assertGreaterEqual(score("C_tantal", "Tụ tantalum 10uF 16V 1206"),
                                M.ACCEPT_SCORE)
        self.assertGreaterEqual(score("C_tantal", "Tụ tantalum 10uF 16V case A"),
                                M.ACCEPT_SCORE)
        self.assertEqual(score("C_tantal", "Tụ tantalum 10uF 3528"), 0.0)

    def test_cuon_cam_thach_anh_cau_chi(self):
        self.assertGreaterEqual(score("L_pwr", "Cuộn cảm SRN6045 22uH"), M.ACCEPT_SCORE)
        self.assertEqual(score("L_pwr", "Cuộn cảm 22uH 0805"), 0.0)
        self.assertGreaterEqual(score("Y_8M", "Thạch anh 8MHz 3225"), M.ACCEPT_SCORE)
        self.assertEqual(score("Y_8M", "Thạch anh 8MHz HC49S"), 0.0)
        self.assertEqual(score("Y_8M", "Thạch anh 12MHz 3225"), 0.0)
        self.assertGreaterEqual(score("F_500m", "Cầu chì tự phục hồi 1206 0.5A"),
                                M.ACCEPT_SCORE)
        self.assertEqual(score("F_500m", "Cầu chì 1206 1A"), 0.0)

    def test_khong_ghi_kich_thuoc_thi_chua_chac(self):
        s = score("C_100n", "Tụ 100nF 50V")
        self.assertGreaterEqual(s, M.CONSIDER_SCORE)
        self.assertLess(s, M.ACCEPT_SCORE)


class TestTenLoai(unittest.TestCase):
    def test_khong_bat_buoc_ten_loai(self):
        self.assertGreaterEqual(score("C_100n", "100nF 0603 50V X7R"), M.ACCEPT_SCORE)

    def test_tru_diem_loai_xung_dot(self):
        for t in ["NTC 10K 0603", "Biến trở 10K 0603", "Module điện trở 10K 0603",
                  "Kit điện trở 10K 0603"]:
            s = score("R_10k", t)
            self.assertLess(s, M.ACCEPT_SCORE, t)

    def test_loai_khac_doi_nghia_con_so(self):
        # "104" trên tụ là 100nF, không phải 100kΩ
        ln = BomLine(key="x", qty=1, raw="Điện trở 0603 100K")
        cand = M.score_candidate(ln, M.Candidate("Tụ gốm 104 0603", "https://x.vn/p"))
        self.assertEqual(cand.score, 0.0)


class TestPickBest(unittest.TestCase):
    def test_chon_dung_trong_danh_sach(self):
        cands = [M.Candidate("Tụ 0603 10nF 50V", "https://x.vn/a"),
                 M.Candidate("Tụ 0805 100nF 50V", "https://x.vn/b"),
                 M.Candidate("Tụ 0603 0.1uF 50V", "https://x.vn/c")]
        d = M.pick_best(line("C_100n"), cands)
        self.assertEqual(d.status, M.MATCH)
        self.assertEqual(d.best.url, "https://x.vn/c")

    def test_sai_het_thi_khong_co_ung_vien(self):
        cands = [M.Candidate("Tụ 0603 10nF", "https://x.vn/a"),
                 M.Candidate("Tụ 0805 100nF", "https://x.vn/b")]
        d = M.pick_best(line("C_100n"), cands)
        self.assertEqual(d.status, M.NONE)
        self.assertEqual(d.shortlist, [])


class TestCliShowQueries(unittest.TestCase):
    def run_cli(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["cart", str(MAU), "--shop", "https://x.vn", *extra])
        return rc, buf.getvalue()

    def test_show_queries_khong_mo_trinh_duyet(self):
        rc, out = self.run_cli("--show-queries")
        self.assertEqual(rc, 0)
        self.assertIn("100nF 0603 | 0.1uF 0603 | 104 0603", out)
        self.assertIn("[C1 C2 C3]", out)

    def test_parser(self):
        a = cli.build_parser().parse_args(
            ["cart", "bom.txt", "--shop", "x.vn", "--show-queries"])
        self.assertTrue(a.show_queries)


if __name__ == "__main__":
    unittest.main()
