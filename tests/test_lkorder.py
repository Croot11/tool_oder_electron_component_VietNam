"""Bộ kiểm thử. Chạy: python -m unittest discover -s tests

Trọng tâm là hai chỗ dễ sai nhất và tốn tiền thật nếu sai:
khớp tên linh kiện, và tính đúng tổng chi phí khi có gói/MOQ/freeship.
"""

from __future__ import annotations

import sys
import unittest
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lkorder import bom, catalog, normalize            # noqa: E402
from lkorder.models import BomLine, Offer, PriceBreak, Shop, Solution  # noqa: E402
from lkorder.optimizer import Options, compare_scenarios, solve        # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_mpn_bo_qua_nhieu_marketing(self):
        keys = {
            normalize.normalize("STM32F103C8T6"),
            normalize.normalize("Vi điều khiển STM32F103C8T6 LQFP-48 chính hãng"),
            normalize.normalize("IC MCU STM32F103C8T6 (SMD)"),
        }
        self.assertEqual(keys, {"stm32f103c8t6"})

    def test_quy_doi_don_vi_tu_dien(self):
        # 0.1uF và 100nF là cùng một con tụ
        self.assertEqual(
            normalize.normalize("Tụ gốm 100nF 50V 0805"),
            normalize.normalize("Tụ 0.1uF 50V"),
        )

    def test_quy_doi_cong_suat_dien_tro(self):
        # 1/4W == 0.25W, và công suất không được lọt vào khoá
        self.assertEqual(
            normalize.normalize("Điện trở 10K 1/4W 5%"),
            normalize.normalize("Trở 10K 0.25W"),
        )

    def test_ky_hieu_chau_au(self):
        self.assertEqual(
            normalize.normalize("Điện trở 4K7"),
            normalize.normalize("Điện trở 4.7K"),
        )

    def test_khong_nham_gia_tri_thanh_ma_linh_kien(self):
        p = normalize.parse("Điện trở 10K 1/4W 5%")
        self.assertEqual(p.kind, "resistor")
        self.assertEqual(p.key, "resistor 10k")     # không phải "1/4w"

    def test_tach_package_khoi_khoa(self):
        p = normalize.parse("IC NE555P DIP-8")
        self.assertEqual(p.key, "ne555p")
        self.assertEqual(p.package, "dip8")

    def test_loai_khac_nhau_thi_khoa_khac_nhau(self):
        self.assertNotEqual(
            normalize.normalize("Điện trở 10K"),
            normalize.normalize("Tụ gốm 10K"),
        )

    def test_mega_va_milli(self):
        # 1M ohm là mega, không phải milli
        self.assertEqual(normalize.parse("Điện trở 1M").value, 1e6)
        self.assertEqual(normalize.parse("Cuộn cảm 10mH").value, 10e-3)


class TestBom(unittest.TestCase):
    def test_cac_kieu_ghi_so_luong(self):
        for text, qty in [
            ("STM32F103C8T6 x2", 2),
            ("2 x STM32F103C8T6", 2),
            ("STM32F103C8T6, 2", 2),
            ("STM32F103C8T6", 1),
        ]:
            line = bom.parse_line(text)
            self.assertIsNotNone(line, text)
            self.assertEqual(line.key, "stm32f103c8t6", text)
            self.assertEqual(line.qty, qty, text)

    def test_bo_qua_chu_thich_va_dong_trong(self):
        lines = bom.parse_text("# ghi chú\n\nNE555 x2\n\n")
        self.assertEqual(len(lines), 1)

    def test_gop_dong_trung_lap(self):
        lines = bom.merge_duplicates(bom.parse_text("NE555 x2\nIC NE555 x3"))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].qty, 5)

    def test_doc_csv_co_header(self):
        text = "ten,so_luong,ghi_chu\nSTM32F103C8T6,2,mcu\nĐiện trở 10K,100,\n"
        lines = bom.parse_csv(text)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].qty, 2)
        self.assertEqual(lines[1].qty, 100)


class TestOffer(unittest.TestCase):
    def test_mua_theo_goi(self):
        # gói 100 con giá 18.000đ; cần 100 con -> mua 1 gói
        o = Offer("s", "Điện trở 10K (100 con)", 18_000, "resistor 10k", pack=100)
        self.assertEqual(o.units_for(100), 1)
        self.assertEqual(o.cost_for(100), 18_000)
        self.assertEqual(o.pieces_for(100), 100)

    def test_goi_lam_tron_len(self):
        o = Offer("s", "gói 100", 18_000, "k", pack=100)
        self.assertEqual(o.units_for(101), 2)       # cần 101 -> phải mua 2 gói
        self.assertEqual(o.pieces_for(101), 200)
        self.assertEqual(o.cost_for(101), 36_000)

    def test_moq(self):
        o = Offer("s", "tụ", 500, "k", moq=10)
        self.assertEqual(o.units_for(3), 10)
        self.assertEqual(o.cost_for(3), 5_000)

    def test_bac_gia(self):
        o = Offer("s", "ne555", 5_000, "ne555",
                  price_breaks=(PriceBreak(10, 4_000), PriceBreak(100, 3_000)))
        self.assertEqual(o.cost_for(5), 25_000)
        self.assertEqual(o.cost_for(10), 40_000)
        self.assertEqual(o.cost_for(100), 300_000)

    def test_ton_kho_chan_offer(self):
        o = Offer("s", "x", 1_000, "k", stock=5)
        self.assertTrue(o.can_supply(5))
        self.assertFalse(o.can_supply(6))


class TestCatalog(unittest.TestCase):
    def test_doan_goi_tu_ten(self):
        self.assertEqual(catalog._guess_pack("Điện trở 10K 1/4W 5% (100 con)"), 100)
        self.assertEqual(catalog._guess_pack("Combo 50 tụ gốm"), 50)
        # không được hiểu nhầm thông số kỹ thuật thành số lượng gói
        self.assertEqual(catalog._guess_pack("Tụ gốm 100nF 50V"), 1)
        self.assertEqual(catalog._guess_pack("LED 5mm đỏ"), 1)

    def test_doc_gia_kieu_viet_nam(self):
        self.assertEqual(catalog._parse_price("45.000 đ"), 45_000)
        self.assertEqual(catalog._parse_price("45,000"), 45_000)
        self.assertEqual(catalog._parse_price("1.250.000đ"), 1_250_000)

    def test_khop_tien_to_ma(self):
        cat = catalog.Catalog([
            Offer("a", "IC NE555P DIP-8", 4_500, "ne555p"),
            Offer("b", "IC NE555", 4_000, "ne555"),
        ])
        ms = cat.match(BomLine("ne555", 1, raw="NE555"))
        self.assertEqual(len(ms), 2)
        self.assertEqual(ms[0].score, 1.0)
        self.assertEqual(ms[1].how, "prefix")

    def test_loc_luc_lay_gia_dung_cung_luat_voi_luc_ghep(self):
        """Hồi quy: khâu lấy giá từng vứt mất chính thứ khâu ghép chấp nhận.

        Người dùng gõ "NE555", shop trả "IC NE555P DIP-8". Bộ lọc lúc fetch
        phải giữ lại nó, vì `Catalog.match` sau đó vẫn coi hai cái là một.
        """
        score = catalog.relevance("NE555", "IC NE555P DIP-8")
        self.assertGreaterEqual(score, catalog.DEFAULT_MIN_SCORE)

        cat = catalog.Catalog([Offer("a", "IC NE555P DIP-8", 4_500)])
        self.assertTrue(cat.match(BomLine("ne555", 1, raw="NE555")))

    def test_loc_van_vut_hang_khong_lien_quan(self):
        self.assertLess(catalog.relevance("NE555", "Cáp USB Type-C 1m"), 0.35)

    def test_khong_khop_tien_to_qua_ngan(self):
        self.assertFalse(catalog._prefix_match("lm78", "lm7805"))
        self.assertTrue(catalog._prefix_match("ne555", "ne555p"))
        # đuôi dài quá thì là mã khác, không phải biến thể
        self.assertFalse(catalog._prefix_match("lm2596", "lm2596abcde"))


class TestOptimizer(unittest.TestCase):
    def setUp(self):
        self.shops = {
            "a": Shop("a", "A", shipping_fee=25_000, order_penalty=20_000),
            "b": Shop("b", "B", shipping_fee=30_000, order_penalty=20_000),
            "c": Shop("c", "C", shipping_fee=35_000, order_penalty=20_000),
        }

    def test_gom_ve_it_shop_khi_chenh_gia_nho(self):
        """Rẻ hơn 500đ không đáng để đặt thêm một đơn 30.000đ tiền ship."""
        lines = [BomLine("p1", 1), BomLine("p2", 1)]
        offers = [
            Offer("a", "p1 ở A", 10_000, "p1"),
            Offer("a", "p2 ở A", 10_000, "p2"),
            Offer("b", "p2 ở B", 9_500, "p2"),     # rẻ hơn 500đ
        ]
        sol = solve(lines, offers, self.shops)
        self.assertEqual(sol.n_shops, 1)
        self.assertEqual(sol.shop_ids, ["a"])

    def test_van_tach_don_khi_chenh_gia_lon(self):
        lines = [BomLine("p1", 1), BomLine("p2", 1)]
        offers = [
            Offer("a", "p1 ở A", 10_000, "p1"),
            Offer("a", "p2 ở A", 500_000, "p2"),
            Offer("b", "p2 ở B", 100_000, "p2"),   # rẻ hơn 400.000đ
        ]
        sol = solve(lines, offers, self.shops)
        self.assertEqual(sol.n_shops, 2)

    def test_penalty_cao_ep_gom_don(self):
        lines = [BomLine("p1", 1), BomLine("p2", 1)]
        offers = [
            Offer("a", "p1 ở A", 10_000, "p1"),
            Offer("a", "p2 ở A", 500_000, "p2"),
            Offer("b", "p2 ở B", 100_000, "p2"),
        ]
        sol = solve(lines, offers, self.shops, Options(shop_penalty=1_000_000))
        self.assertEqual(sol.n_shops, 1)

    def test_freeship_lam_thay_doi_lua_chon(self):
        """Mua chỗ đắt hơn nhưng miễn ship có thể rẻ hơn tổng."""
        shops = {
            "x": Shop("x", "X", shipping_fee=40_000, order_penalty=0,
                      free_ship_threshold=300_000),
            "y": Shop("y", "Y", shipping_fee=40_000, order_penalty=0),
        }
        lines = [BomLine("p1", 1), BomLine("p2", 1)]
        offers = [
            Offer("x", "p1 X", 250_000, "p1"), Offer("y", "p1 Y", 245_000, "p1"),
            Offer("x", "p2 X", 60_000, "p2"),  Offer("y", "p2 Y", 58_000, "p2"),
        ]
        sol = solve(lines, offers, shops, Options(shop_penalty=0))
        self.assertEqual(sol.shop_ids, ["x"])
        self.assertEqual(sol.shipping_cost, 0)
        self.assertEqual(sol.real_total, 310_000)   # rẻ hơn 343.000đ ở Y

    def test_max_shops_duoc_ton_trong(self):
        lines = [BomLine("p1", 1), BomLine("p2", 1), BomLine("p3", 1)]
        offers = [
            Offer("a", "p1", 1_000, "p1"),
            Offer("b", "p2", 1_000, "p2"),
            Offer("c", "p3", 1_000, "p3"),
        ]
        sol = solve(lines, offers, self.shops, Options(max_shops=2))
        self.assertLessEqual(sol.n_shops, 2)

    def test_bao_thieu_khi_khong_ai_ban(self):
        lines = [BomLine("p1", 1), BomLine("khong_ai_ban", 1)]
        offers = [Offer("a", "p1", 1_000, "p1")]
        sol = solve(lines, offers, self.shops)
        self.assertEqual(len(sol.unmet), 1)
        self.assertEqual(sol.unmet[0].key, "khong_ai_ban")

    def test_mua_du_hang_uu_tien_hon_mua_re(self):
        """Kể cả khi penalty rất cao, phương án đủ hàng vẫn phải thắng."""
        lines = [BomLine("p1", 1), BomLine("p2", 1)]
        offers = [Offer("a", "p1", 1_000, "p1"), Offer("b", "p2", 1_000, "p2")]
        sol = solve(lines, offers, self.shops, Options(shop_penalty=10_000_000))
        self.assertEqual(len(sol.unmet), 0)
        self.assertEqual(sol.n_shops, 2)

    def test_ket_qua_dung_bang_duyet_vet_can(self):
        """Đối chiếu với vét cạn ngây thơ trên dữ liệu ngẫu nhiên.

        Đây là bài kiểm tra quan trọng nhất: nó bắt lỗi cắt nhánh sai, thứ mà
        các test riêng lẻ rất dễ bỏ sót.
        """
        import random
        rng = random.Random(42)
        for trial in range(60):
            n_shop = rng.randint(2, 5)
            n_line = rng.randint(2, 6)
            shops = {
                f"s{i}": Shop(f"s{i}", f"S{i}",
                              shipping_fee=rng.choice([0, 20_000, 35_000]),
                              order_penalty=rng.choice([0, 15_000]),
                              free_ship_threshold=rng.choice([0, 0, 200_000]))
                for i in range(n_shop)
            }
            lines = [BomLine(f"p{j}", rng.randint(1, 5)) for j in range(n_line)]
            offers = [
                Offer(sid, f"{sid}-p{j}", rng.randint(1_000, 80_000), f"p{j}",
                      pack=rng.choice([1, 1, 10]), moq=rng.choice([1, 1, 5]))
                for j in range(n_line) for sid in shops if rng.random() < 0.6
            ]
            if not offers:
                continue

            got = solve(lines, offers, shops, Options(shop_penalty=25_000))
            want = _brute_force(lines, offers, shops, 25_000)
            self.assertEqual(
                (len(got.unmet), _score(got, 25_000)),
                (len(want.unmet), _score(want, 25_000)),
                f"lần {trial}: bộ tối ưu ra {_score(got, 25_000)}, "
                f"vét cạn ra {_score(want, 25_000)}",
            )


def _score(sol: Solution, penalty: int) -> int:
    return sol.real_total + penalty * sol.n_shops


def _brute_force(lines, offers, shops, penalty) -> Solution:
    """Vét cạn ngây thơ: thử MỌI cách gán từng dòng vào từng offer.

    Chậm khủng khiếp nên chỉ dùng cho bài toán tí hon trong test, nhưng nó
    không hề dùng chung mã với bộ tối ưu — nhờ vậy mới làm chứng độc lập được.
    """
    from itertools import product

    from lkorder.models import Allocation

    per_line = []
    for ln in lines:
        opts = [o for o in offers if o.part_key == ln.key and o.can_supply(ln.qty)]
        per_line.append(opts + [None])

    best: Solution | None = None
    best_key = None
    for combo in product(*per_line):
        sol = Solution(shops=shops)
        for ln, o in zip(lines, combo):
            if o is None:
                sol.unmet.append(ln)
                continue
            units = o.units_for(ln.qty)
            sol.allocations.append(
                Allocation(ln, o, units, o.unit_price_at(units))
            )
        key = (len(sol.unmet), _score(sol, penalty))
        if best_key is None or key < best_key:
            best, best_key = sol, key
    return best


class TestScenarios(unittest.TestCase):
    def test_bang_danh_doi_tang_dan_theo_so_don(self):
        shops = {
            "a": Shop("a", "A", shipping_fee=30_000, order_penalty=20_000),
            "b": Shop("b", "B", shipping_fee=30_000, order_penalty=20_000),
        }
        lines = [BomLine("p1", 1), BomLine("p2", 1)]
        offers = [
            Offer("a", "p1 A", 10_000, "p1"), Offer("a", "p2 A", 90_000, "p2"),
            Offer("b", "p1 B", 10_000, "p1"), Offer("b", "p2 B", 20_000, "p2"),
        ]
        rows = compare_scenarios(lines, offers, shops)
        self.assertTrue(rows)
        # phương án 1 đơn phải mua đủ hàng (shop b bán cả hai món)
        k1 = [s for k, s in rows if s.n_shops == 1]
        self.assertTrue(k1)
        self.assertEqual(len(k1[0].unmet), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
