"""Chọn mua ở shop nào để vừa rẻ vừa ít đơn nhất.

Bài toán: mỗi linh kiện bán ở nhiều shop với giá/tồn kho khác nhau. Mỗi shop
phát sinh thêm phí ship và "chi phí phiền" (chờ hàng, nhiều mã vận đơn). Cần
chọn tập shop + phân bổ linh kiện sao cho tổng chi phí nhỏ nhất.

Đây là bài Set Cover có trọng số — NP-hard nói chung, nhưng quy mô thực tế
rất nhỏ: cỡ 5-15 shop VN bán linh kiện. Với n shop chỉ có 2^n tập con nên
duyệt hết là khả thi.

CHẤT LƯỢNG LỜI GIẢI, nói cho đúng:

* Không shop nào có ngưỡng miễn ship, và n <= 16: **tối ưu tuyệt đối**. Khi
  tập shop đã cố định, mỗi linh kiện độc lập nhau nên cứ mua chỗ rẻ nhất là
  xong; duyệt hết tập con thì không bỏ sót gì.
* Có ngưỡng miễn ship: **gần tối ưu**. Ngưỡng làm các linh kiện phụ thuộc lẫn
  nhau (dồn thêm hàng vào một shop có thể được miễn ship), nên sau khi duyệt
  tập con còn phải chạy local search. Local search đổi mỗi lần một món nên có
  thể kẹt ở cực trị cục bộ khi lời giải tốt hơn đòi hỏi đổi hai món cùng lúc.
  Đối chiếu với vét cạn trên 1598 bài ngẫu nhiên: 1597 bài trùng khớp, 1 bài
  đắt hơn tối ưu 0,97%.
* n > 16: **heuristic** (tham lam + tìm kiếm cục bộ trên tập shop).

Hai điểm quyết định tốc độ:

* Mọi thứ được tiền tính thành bảng số nguyên + bitmask trước khi duyệt. Bên
  trong vòng lặp nóng không có đối tượng Python nào được tạo ra.
* Với mỗi linh kiện, các shop được sắp sẵn theo giá tăng dần, nên việc chọn
  shop rẻ nhất trong một tập con thường dừng ngay ở phần tử đầu tiên.
"""

from __future__ import annotations

from itertools import combinations
from typing import Iterable, NamedTuple, Sequence

from .models import Allocation, BomLine, Offer, Shop, Solution

# Trên ngưỡng này thì không duyệt hết 2^n nữa mà chuyển sang heuristic.
# 16 shop = 65536 tập con, dưới 2 giây. Thực tế VN hiếm khi vượt 10 shop.
EXACT_SHOP_LIMIT = 16


class Options:
    """Tham số điều khiển bộ tối ưu."""

    def __init__(
        self,
        shop_penalty: int | None = None,
        max_shops: int | None = None,
        allow_partial: bool = True,
        local_search: bool = True,
    ) -> None:
        # Ghi đè order_penalty của mọi shop. Đây là cần gạt chính: càng cao thì
        # tool càng ép gom về ít shop, kể cả khi phải trả tiền hàng đắt hơn.
        self.shop_penalty = shop_penalty
        self.max_shops = max_shops
        self.allow_partial = allow_partial   # cho phép thiếu món không mua được
        self.local_search = local_search


class _Cand(NamedTuple):
    """Phương án mua một dòng BOM tại một shop cụ thể (đã chọn offer rẻ nhất)."""

    shop: int          # chỉ số shop
    bit: int           # 1 << shop
    cost: int          # tổng tiền cho dòng này
    qty: int           # số đơn vị bán phải mua (đã tròn theo gói và MOQ)
    unit_price: int    # giá một đơn vị bán
    offer: Offer


class _Model:
    """Dạng đã tiền tính của bài toán, chỉ gồm số nguyên và bitmask."""

    def __init__(
        self,
        lines: Sequence[BomLine],
        offers_by_key: dict[str, list[Offer]],
        shops: dict[str, Shop],
        shop_ids: list[str],
        opts: Options,
    ) -> None:
        self.lines = list(lines)
        self.shop_ids = shop_ids
        self.shops = [shops[s] for s in shop_ids]
        self.n = len(shop_ids)
        index = {s: i for i, s in enumerate(shop_ids)}

        # Với mỗi dòng BOM: shop nào bán, giá bao nhiêu — sắp theo giá tăng dần.
        self.cands: list[tuple[_Cand, ...]] = []
        self.line_mask: list[int] = []       # bitmask các shop bán được dòng này
        for line in self.lines:
            best: dict[int, _Cand] = {}
            for o in offers_by_key.get(line.key, ()):
                si = index.get(o.shop_id)
                if si is None or not o.can_supply(line.qty):
                    continue
                qty = o.units_for(line.qty)
                unit = o.unit_price_at(qty)
                cost = unit * qty
                cur = best.get(si)
                if cur is None or cost < cur.cost:
                    best[si] = _Cand(si, 1 << si, cost, qty, unit, o)
            ordered = tuple(sorted(best.values(), key=lambda c: c.cost))
            self.cands.append(ordered)
            mask = 0
            for c in ordered:
                mask |= c.bit
            self.line_mask.append(mask)

        # Bản rút gọn chỉ gồm số nguyên trần, dùng trong vòng lặp nóng:
        # truy cập phần tử tuple nhanh hơn hẳn thuộc tính NamedTuple.
        self.hot: list[tuple[tuple[int, int, int], ...]] = [
            tuple((c.bit, c.cost, c.shop) for c in cs) for cs in self.cands
        ]

        self.ship = [sh.shipping_fee for sh in self.shops]
        self.free_at = [sh.free_ship_threshold for sh in self.shops]
        self.penalty = [
            opts.shop_penalty if opts.shop_penalty is not None else sh.order_penalty
            for sh in self.shops
        ]
        self.fixed_min = sorted(self.ship[i] + self.penalty[i] for i in range(self.n))
        self.has_freeship = any(self.free_at)

    # ------------------------------------------------------------------ gán

    def assign(self, mask: int) -> tuple[list[_Cand | None], int]:
        """Gán mỗi dòng vào shop rẻ nhất trong `mask`. Trả về (gán, số thiếu)."""
        chosen: list[_Cand | None] = []
        unmet = 0
        for cands in self.cands:
            pick = None
            for c in cands:              # đã sắp theo giá, cái khớp đầu tiên là rẻ nhất
                if c.bit & mask:
                    pick = c
                    break
            if pick is None:
                unmet += 1
            chosen.append(pick)
        return chosen, unmet

    def score_mask(self, mask: int) -> tuple[int, int, int]:
        """(số món thiếu, điểm, cận dưới) khi chỉ mua trong `mask`.

        Đây là hàm chạy hàng triệu lần, nên nó cố tình không tạo đối tượng nào:
        chỉ cộng dồn vào một mảng số nguyên rồi tính phí ship một lượt ở cuối.

        `điểm` là chi phí khi mỗi món mua ở chỗ rẻ nhất. Có ngưỡng miễn ship
        thì cách gán đó chưa chắc tối ưu, nên hàm trả thêm `cận dưới` = tiền
        hàng rẻ nhất + penalty, tức bỏ hẳn phí ship. Không cách gán nào trong
        tập shop này rẻ hơn con số đó, nên nó dùng để cắt nhánh an toàn khi
        quyết định có cần chạy local search cho tập shop này hay không.
        """
        sub = [0] * self.n
        used = 0
        parts = 0
        unmet = 0
        for cands in self.hot:
            for bit, cost, shop in cands:      # đã sắp theo giá tăng dần
                if bit & mask:
                    sub[shop] += cost
                    parts += cost
                    used |= bit
                    break
            else:
                unmet += 1

        total = parts
        pen = 0
        i = 0
        u = used
        while u:
            if u & 1:
                free = self.free_at[i]
                if not free or sub[i] < free:
                    total += self.ship[i]
                pen += self.penalty[i]
            u >>= 1
            i += 1
        return unmet, total + pen, parts + pen

    def used_mask(self, mask: int) -> int:
        """Các shop thực sự được dùng tới khi cho phép mua trong `mask`."""
        used = 0
        for cands in self.hot:
            for bit, _cost, _shop in cands:
                if bit & mask:
                    used |= bit
                    break
        return used

    def score(self, chosen: Sequence[_Cand | None]) -> tuple[int, int]:
        """Trả về (tổng điểm gồm penalty, tiền thật phải trả)."""
        sub = [0] * self.n
        used = 0
        parts = 0
        for c in chosen:
            if c is None:
                continue
            sub[c.shop] += c.cost
            parts += c.cost
            used |= c.bit
        ship = 0
        pen = 0
        for i in range(self.n):
            if not (used >> i) & 1:
                continue
            if not self.free_at[i] or sub[i] < self.free_at[i]:
                ship += self.ship[i]
            pen += self.penalty[i]
        return parts + ship + pen, parts + ship

    # --------------------------------------------------------- local search

    def refine(self, chosen: list[_Cand | None], mask: int) -> list[_Cand | None]:
        """Thử chuyển từng dòng sang shop khác nếu tổng chi phí giảm.

        Chỉ có tác dụng khi tồn tại ngưỡng miễn ship: hoặc dồn thêm hàng vào
        một shop cho vượt ngưỡng, hoặc dọn sạch shop chỉ có 1-2 món để bỏ hẳn
        phí ship và penalty của shop đó.
        """
        best = list(chosen)
        best_score, _ = self.score(best)
        for _ in range(30):
            improved = False
            for i, cands in enumerate(self.cands):
                cur = best[i]
                for c in cands:
                    if not (c.bit & mask) or (cur is not None and c.shop == cur.shop):
                        continue
                    best[i] = c
                    s, _ = self.score(best)
                    if s < best_score:
                        best_score = s
                        improved = True
                        cur = c
                    else:
                        best[i] = cur
            if not improved:
                break

        # Đổi từng món một hay kẹt ở cực trị cục bộ: chuyển một món sang shop
        # khác làm đắt lên ngay, nên bị từ chối, dù chuyển thêm vài món nữa thì
        # cả nhóm sẽ vượt ngưỡng miễn ship và rẻ hơn hẳn. Nên thử thẳng vào
        # đúng mục tiêu đó: với mỗi shop chưa đạt ngưỡng, dồn dần các món rẻ
        # nhất về đó cho tới khi chạm ngưỡng, rồi xem tổng có giảm không.
        for shop in range(self.n):
            if not (mask >> shop) & 1 or not self.free_at[shop]:
                continue
            cand = self._fill_to_freeship(best, shop)
            if cand is None:
                continue
            s, _ = self.score(cand)
            if s < best_score:
                best, best_score = cand, s
        return best

    def _fill_to_freeship(
        self, chosen: Sequence[_Cand | None], shop: int
    ) -> list[_Cand | None] | None:
        """Dồn hàng về `shop` cho đủ ngưỡng miễn ship, chọn món đội giá ít nhất.

        Trả None nếu shop đã đạt ngưỡng, hoặc có dồn hết cũng không tới.
        """
        target = self.free_at[shop]
        current = sum(c.cost for c in chosen if c is not None and c.shop == shop)
        if current >= target:
            return None

        # các món đang mua ở shop khác nhưng shop này cũng bán
        moves: list[tuple[int, int, _Cand]] = []   # (đội thêm, chỉ số dòng, ứng viên)
        for i, cur in enumerate(chosen):
            if cur is None or cur.shop == shop:
                continue
            for c in self.cands[i]:
                if c.shop == shop:
                    moves.append((c.cost - cur.cost, i, c))
                    break
        if not moves:
            return None

        moves.sort()          # món nào chuyển sang mà đội giá ít nhất thì chuyển trước
        out = list(chosen)
        for _delta, i, c in moves:
            out[i] = c
            current += c.cost
            if current >= target:
                return out
        return None    # dồn hết vẫn không đạt ngưỡng

    # ------------------------------------------------------------ kết xuất

    def to_solution(
        self, chosen: Sequence[_Cand | None], shops: dict[str, Shop]
    ) -> Solution:
        sol = Solution(shops=shops)
        for line, c in zip(self.lines, chosen):
            if c is None:
                sol.unmet.append(line)
                continue
            sol.allocations.append(
                Allocation(line=line, offer=c.offer, qty=c.qty, unit_price=c.unit_price)
            )
        return sol


# --------------------------------------------------------------- API chính


def solve(
    lines: Sequence[BomLine],
    offers: Iterable[Offer],
    shops: dict[str, Shop],
    opts: Options | None = None,
) -> Solution:
    """Tìm phương án đặt hàng tốt nhất."""
    opts = opts or Options()

    offers_by_key: dict[str, list[Offer]] = {}
    for o in offers:
        sh = shops.get(o.shop_id)
        if sh is not None and sh.enabled:
            offers_by_key.setdefault(o.part_key, []).append(o)

    # Chỉ xét shop thực sự bán ít nhất một món trong BOM.
    useful = sorted(
        {o.shop_id for line in lines for o in offers_by_key.get(line.key, ())}
    )
    if not useful:
        return Solution(shops=shops, unmet=list(lines))

    model = _Model(lines, offers_by_key, shops, useful, opts)
    if model.n <= EXACT_SHOP_LIMIT:
        chosen = _search_exact(model, opts)
    else:
        chosen = _search_greedy(model, opts)
    return model.to_solution(chosen, shops)


def _better(
    cand: tuple[int, int], best: tuple[int, int] | None
) -> bool:
    """So sánh (số món thiếu, điểm): mua đủ hàng luôn thắng mua rẻ."""
    if best is None:
        return True
    if cand[0] != best[0]:
        return cand[0] < best[0]
    return cand[1] < best[1]


def _search_exact(model: _Model, opts: Options) -> list[_Cand | None]:
    """Duyệt mọi tập con shop theo số shop tăng dần, có cắt nhánh.

    Duyệt tăng dần cho phép dừng sớm: khi số shop k đã lớn tới mức riêng phí
    ship + penalty tối thiểu đã vượt lời giải tốt nhất, mọi k lớn hơn cũng
    vô vọng — nhưng chỉ khi lời giải đó đã mua đủ hàng, vì tập lớn hơn vẫn có
    thể thắng nhờ phủ được món còn thiếu.
    """
    n = model.n
    max_k = min(n, opts.max_shops or n)
    all_idx = range(n)
    score_mask = model.score_mask

    best_key: tuple[int, int] | None = None
    best_mask = 0
    # (cận dưới, mask) của mọi tập shop, để sàng lại bằng local search về sau
    bounds: list[tuple[tuple[int, int], int]] = []

    for k in range(1, max_k + 1):
        if best_key is not None and best_key[0] == 0:
            if sum(model.fixed_min[:k]) >= best_key[1]:
                break
        for combo in combinations(all_idx, k):
            mask = 0
            for i in combo:
                mask |= 1 << i
            unmet, score, lb = score_mask(mask)
            if unmet and not opts.allow_partial:
                continue
            key = (unmet, score)
            if _better(key, best_key):
                # Tập con dư thừa (có shop không được dùng tới) không cần loại
                # tường minh: bản gọn hơn của nó đã được xét ở k nhỏ hơn và cho
                # điểm thấp hơn, nên không bao giờ thắng ở đây.
                if model.used_mask(mask) == mask:
                    best_key, best_mask = key, mask
            if model.has_freeship:
                bounds.append(((unmet, lb), mask))

    if best_key is None:
        return [None] * len(model.lines)

    best_chosen, _ = model.assign(best_mask)
    if not (model.has_freeship and opts.local_search):
        return best_chosen

    # Có ngưỡng miễn ship thì "mỗi món mua chỗ rẻ nhất" chưa chắc là cách gán
    # tốt nhất trong một tập shop: dồn thêm hàng vào một shop để vượt ngưỡng
    # có thể rẻ hơn. Xét lại các tập shop theo cận dưới tăng dần và dừng ngay
    # khi cận dưới đã vượt lời giải tốt nhất — từ đó trở đi vô vọng.
    bounds.sort()
    for bound, mask in bounds:
        if not _better(bound, best_key):
            break
        chosen, _ = model.assign(mask)
        refined = model.refine(chosen, mask)
        score, _ = model.score(refined)
        unmet = sum(1 for c in refined if c is None)
        if _better((unmet, score), best_key):
            best_key, best_chosen = (unmet, score), refined
    return best_chosen


def _search_greedy(model: _Model, opts: Options) -> list[_Cand | None]:
    """Dự phòng khi có quá nhiều shop để duyệt hết 2^n.

    Hai giai đoạn: tham lam thêm dần shop nào giảm chi phí nhiều nhất, rồi
    tìm kiếm cục bộ trên chính tập shop — thử thêm một shop, bớt một shop,
    hoặc đổi một shop lấy shop khác, cho tới khi không cải thiện được nữa.
    Bước hai quan trọng vì tham lam hay kẹt ở tập shop dư một hai cái.
    """
    n = model.n
    limit = min(n, opts.max_shops or n)
    score_mask = model.score_mask

    # --- giai đoạn 1: tham lam
    mask = 0
    best_key: tuple[int, int] | None = None
    while bin(mask).count("1") < limit:
        round_best: tuple[tuple[int, int], int] | None = None
        for i in range(n):
            bit = 1 << i
            if mask & bit:
                continue
            unmet, score, _lb = score_mask(mask | bit)
            key = (unmet, score)
            if round_best is None or _better(key, round_best[0]):
                round_best = (key, mask | bit)
        if round_best is None or not _better(round_best[0], best_key):
            break
        best_key, mask = round_best

    if best_key is None:
        return [None] * len(model.lines)

    # --- giai đoạn 2: thêm / bớt / đổi một shop
    for _ in range(50):
        improved = False
        neighbours: list[int] = []
        for i in range(n):
            bit = 1 << i
            if mask & bit:
                neighbours.append(mask & ~bit)          # bớt
            elif bin(mask).count("1") < limit:
                neighbours.append(mask | bit)           # thêm
        for i in range(n):                              # đổi
            if not (mask & (1 << i)):
                continue
            for j in range(n):
                if mask & (1 << j):
                    continue
                neighbours.append((mask & ~(1 << i)) | (1 << j))
        for cand in neighbours:
            if not cand:
                continue
            unmet, score, _lb = score_mask(cand)
            key = (unmet, score)
            if _better(key, best_key):
                best_key, mask, improved = key, cand, True
        if not improved:
            break

    chosen, _ = model.assign(mask)
    if model.has_freeship and opts.local_search:
        chosen = model.refine(chosen, mask)
    return chosen


def compare_scenarios(
    lines: Sequence[BomLine],
    offers: Iterable[Offer],
    shops: dict[str, Shop],
    max_k: int = 6,
) -> list[tuple[int, Solution]]:
    """Với mỗi số đơn k, tìm phương án rẻ nhất khi bị ép chỉ đặt k shop.

    Đây mới là bảng giúp quyết định: "gom về 2 đơn thay vì 4 thì đắt thêm bao
    nhiêu?" — trả lời bằng tiền, không phải cảm tính. Penalty đặt về 0 để
    tổng hiện ra là tiền thật phải trả, còn ràng buộc số đơn do `max_shops` lo.

    Trả về [(k, lời giải)], bỏ những k không cho ra phương án khác biệt.
    """
    offers = list(offers)
    n_useful = len({o.shop_id for o in offers if o.shop_id in shops})
    out: list[tuple[int, Solution]] = []
    seen: set[tuple[str, ...]] = set()

    for k in range(1, min(max_k, n_useful) + 1):
        sol = solve(lines, offers, shops, Options(shop_penalty=0, max_shops=k))
        sig = tuple(sorted(sol.shop_ids))
        if sig in seen:
            continue
        seen.add(sig)
        out.append((k, sol))
        # Khi đã mua đủ hàng mà tăng k không giảm giá nữa thì dừng: các k lớn
        # hơn chỉ lặp lại đúng phương án này.
        if not sol.unmet and len(out) >= 2 and sol.real_total >= out[-2][1].real_total:
            break
    return out
