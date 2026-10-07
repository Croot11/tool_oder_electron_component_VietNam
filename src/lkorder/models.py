"""Kiểu dữ liệu lõi cho tool đặt linh kiện.

Tất cả số tiền là VND, lưu bằng int (không dùng float cho tiền).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass(frozen=True)
class PriceBreak:
    """Bậc giá theo số lượng: mua từ `min_qty` trở lên thì đơn giá là `unit_price`."""

    min_qty: int
    unit_price: int


@dataclass
class Shop:
    """Một shop linh kiện.

    shipping_fee        phí ship phẳng cho 1 đơn của shop này
    free_ship_threshold đơn >= ngưỡng này thì miễn ship (0 = không có ưu đãi)
    order_penalty       "chi phí phiền" quy ra tiền cho mỗi đơn tách thêm:
                        chờ hàng, nhiều mã vận đơn, nhiều lần nhận hàng.
                        Đây là cần gạt chính để ép tool gom về ít shop.
    """

    id: str
    name: str
    url: str = ""
    platform: str = "csv"
    shipping_fee: int = 30_000
    free_ship_threshold: int = 0
    order_penalty: int = 20_000
    prep_days: int = 2
    enabled: bool = True
    note: str = ""

    def ship_cost(self, subtotal: int) -> int:
        if self.free_ship_threshold and subtotal >= self.free_ship_threshold:
            return 0
        return self.shipping_fee


@dataclass
class Offer:
    """Một sản phẩm cụ thể được bán bởi một shop cụ thể.

    Phân biệt hai đơn vị, vì shop VN rất hay bán theo gói:

        pack  số linh kiện trong MỘT đơn vị bán.
              "Điện trở 10K (gói 100 con)" giá 18.000đ  ->  pack=100
        unit  đơn vị bán. Giá, MOQ, bậc giá, tồn kho đều tính theo đơn vị bán.

    Cần 100 con điện trở với pack=100 nghĩa là mua 1 gói, không phải 100 gói.
    """

    shop_id: str
    title: str
    unit_price: int             # giá của MỘT đơn vị bán (một gói, nếu bán gói)
    part_key: str = ""          # khoá linh kiện đã chuẩn hoá, dùng để khớp BOM
    sku: str = ""
    url: str = ""
    stock: Optional[int] = None  # None = không rõ tồn kho, coi như còn hàng
    moq: int = 1                 # số đơn vị bán tối thiểu
    pack: int = 1                # số linh kiện trong một đơn vị bán
    price_breaks: tuple[PriceBreak, ...] = ()
    updated_at: str = ""

    def unit_price_at(self, units: int) -> int:
        """Giá một đơn vị bán khi mua `units` đơn vị, đã áp bậc giá tốt nhất."""
        price = self.unit_price
        for pb in self.price_breaks:
            if units >= pb.min_qty and pb.unit_price < price:
                price = pb.unit_price
        return price

    def units_for(self, want: int) -> int:
        """Số đơn vị bán phải mua để có đủ `want` linh kiện."""
        pack = max(1, self.pack)
        units = -(-want // pack)          # làm tròn lên theo gói
        if self.moq > 1:
            units = -(-units // self.moq) * self.moq
        return max(1, units)

    def pieces_for(self, want: int) -> int:
        """Số linh kiện thực nhận (có thể dư, do gói hoặc MOQ)."""
        return self.units_for(want) * max(1, self.pack)

    def can_supply(self, want: int) -> bool:
        if self.stock is None:
            return True
        return self.stock >= self.units_for(want)

    def cost_for(self, want: int) -> int:
        units = self.units_for(want)
        return self.unit_price_at(units) * units

    def price_per_piece(self, want: int) -> float:
        """Giá thực trên mỗi linh kiện — con số để so giữa bán lẻ và bán gói."""
        return self.cost_for(want) / max(1, self.pieces_for(want))


@dataclass
class BomLine:
    """Một dòng trong danh sách cần mua."""

    key: str                     # khoá chuẩn hoá, ví dụ "stm32f103c8t6"
    qty: int
    raw: str = ""                # chuỗi gốc người dùng nhập
    designator: str = ""         # R1, C3, U2...
    alt_keys: tuple[str, ...] = ()   # mã thay thế chấp nhận được
    required: bool = True

    @property
    def all_keys(self) -> tuple[str, ...]:
        return (self.key,) + self.alt_keys


@dataclass
class Allocation:
    """Quyết định: mua dòng BOM này, ở shop này, theo offer này."""

    line: BomLine
    offer: Offer
    qty: int          # số ĐƠN VỊ BÁN phải mua (đã tròn theo gói và MOQ)
    unit_price: int   # giá một đơn vị bán

    @property
    def subtotal(self) -> int:
        return self.unit_price * self.qty

    @property
    def shop_id(self) -> str:
        return self.offer.shop_id

    @property
    def pieces(self) -> int:
        """Số linh kiện thực nhận."""
        return self.qty * max(1, self.offer.pack)

    @property
    def surplus(self) -> int:
        """Số linh kiện dư ra so với nhu cầu, do phải mua nguyên gói."""
        return self.pieces - self.line.qty

    def qty_label(self) -> str:
        """'2' cho hàng lẻ, '1 gói (100c)' cho hàng bán theo gói."""
        if self.offer.pack <= 1:
            return str(self.qty)
        return f"{self.qty} gói ({self.pieces}c)"


@dataclass
class Solution:
    allocations: list[Allocation] = field(default_factory=list)
    unmet: list[BomLine] = field(default_factory=list)
    shops: dict[str, Shop] = field(default_factory=dict)

    # --- các con số tổng kết ---
    @property
    def shop_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for a in self.allocations:
            seen.setdefault(a.shop_id, None)
        return list(seen)

    @property
    def n_shops(self) -> int:
        return len(self.shop_ids)

    def subtotal_of(self, shop_id: str) -> int:
        return sum(a.subtotal for a in self.allocations if a.shop_id == shop_id)

    @property
    def parts_cost(self) -> int:
        return sum(a.subtotal for a in self.allocations)

    @property
    def shipping_cost(self) -> int:
        return sum(
            self.shops[sid].ship_cost(self.subtotal_of(sid)) for sid in self.shop_ids
        )

    @property
    def penalty_cost(self) -> int:
        return sum(self.shops[sid].order_penalty for sid in self.shop_ids)

    @property
    def real_total(self) -> int:
        """Số tiền thật sự phải trả (không gồm penalty ảo)."""
        return self.parts_cost + self.shipping_cost

    @property
    def score(self) -> int:
        """Hàm mục tiêu tối ưu (gồm cả penalty ảo)."""
        return self.real_total + self.penalty_cost

    @property
    def max_prep_days(self) -> int:
        if not self.allocations:
            return 0
        return max(self.shops[sid].prep_days for sid in self.shop_ids)

    def by_shop(self) -> dict[str, list[Allocation]]:
        out: dict[str, list[Allocation]] = {}
        for a in self.allocations:
            out.setdefault(a.shop_id, []).append(a)
        return out


def total_lines(lines: Iterable[BomLine]) -> int:
    return sum(1 for _ in lines)
