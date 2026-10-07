"""Kho sản phẩm của các shop, và việc khớp nó với BOM.

Đây là chỗ dễ sai nhất của cả tool. Bộ tối ưu chỉ tốt bằng dữ liệu khớp đầu
vào: nếu "NE555" ở shop A không được nhận ra là cùng con hàng với "IC NE555P
DIP-8" ở shop B thì tool sẽ tưởng phải đặt hai shop.

Vì vậy việc khớp đi theo bốn mức, mức sau lỏng hơn mức trước, và mỗi kết quả
đều mang theo điểm tin cậy để người dùng soi lại bằng `lk match`:

    1.0   khoá chuẩn hoá trùng khít
    0.9   trùng một mã thay thế đã khai báo trong BOM
    0.75  một mã là tiền tố của mã kia (NE555 <-> NE555P)
    <0.75 điểm tương đồng token, phải vượt ngưỡng mới nhận
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .models import BomLine, Offer, PriceBreak
from .normalize import MPN_RE, normalize, parse, similarity, strip_accents

# Dưới ngưỡng này thì coi như không phải cùng một linh kiện.
DEFAULT_MIN_SCORE = 0.55

# Khớp theo tiền tố chỉ áp dụng cho mã đủ dài, nếu không "LM7805" sẽ khớp
# nhầm với "LM78" và tương tự.
MIN_PREFIX_LEN = 5


@dataclass
class Match:
    """Một offer được cho là ứng với một dòng BOM."""

    offer: Offer
    score: float
    how: str        # exact / alt / prefix / fuzzy


def _parse_price(text: str) -> int:
    """'45.000 đ' -> 45000 ; '45,000' -> 45000 ; '45000.0' -> 45000."""
    s = "".join(ch for ch in str(text) if ch.isdigit() or ch in ".,")
    if not s:
        return 0
    # Giá VN dùng dấu chấm/phẩy làm phân cách hàng nghìn. Phần thập phân của
    # tiền VND gần như không tồn tại, nên bỏ hết dấu phân cách là an toàn.
    if s.count(".") == 1 and len(s.split(".")[1]) == 2 and s.count(",") == 0:
        return int(round(float(s)))          # "45000.00"
    return int(s.replace(".", "").replace(",", "") or 0)


def _parse_int(text: str, default: int | None = None) -> int | None:
    s = str(text).strip()
    if not s:
        return default
    try:
        return int(float(s.replace(".", "").replace(",", "")))
    except ValueError:
        return default


def _parse_breaks(text: str) -> tuple[PriceBreak, ...]:
    """'10:4000|100:3500' -> hai bậc giá."""
    out: list[PriceBreak] = []
    for chunk in str(text).split("|"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        qty_s, price_s = chunk.split(":", 1)
        qty = _parse_int(qty_s, 0) or 0
        price = _parse_price(price_s)
        if qty > 0 and price > 0:
            out.append(PriceBreak(qty, price))
    return tuple(sorted(out, key=lambda b: b.min_qty))


# Shop VN hay bán hàng thụ động theo gói và chỉ ghi trong tên sản phẩm:
# "Điện trở 10K 1/4W (100 con)", "Combo 50 tụ gốm", "Túi 20 LED".
# Chỉ nhận khi có hẳn từ chỉ đơn vị đếm, để "50V" hay "5mm" không bị hiểu nhầm.
_PACK_AFTER = re.compile(r"\b(\d{2,4})\s*(con|cai|chiec|pcs|pc|chan)\b")
_PACK_BEFORE = re.compile(r"\b(goi|bich|tui|vi|set|combo|lo|xau)\s*(\d{1,4})\b")


def _guess_pack(title: str) -> int:
    """Đoán số linh kiện trong một đơn vị bán, từ tên sản phẩm. 1 nếu không rõ."""
    t = strip_accents(title).lower()
    m = _PACK_AFTER.search(t)
    if m:
        return int(m.group(1))
    m = _PACK_BEFORE.search(t)
    if m:
        n = int(m.group(2))
        if n > 1:
            return n
    return 1


def load_offers_csv(path: str | Path, shop_id: str | None = None) -> list[Offer]:
    """Đọc bảng giá từ CSV.

    Cột nhận biết: shop, ten/title, gia/price, sku, url, ton/stock, moq,
    bac_gia/price_breaks. Chỉ `ten` và `gia` là bắt buộc.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    cols = {c.strip().lower().replace(" ", "_"): c for c in reader.fieldnames}

    def col(row: dict[str, str], *names: str, default: str = "") -> str:
        for n in names:
            c = cols.get(n)
            if c and (row.get(c) or "").strip():
                return row[c].strip()
        return default

    out: list[Offer] = []
    for row in reader:
        title = col(row, "ten", "title", "name", "ten_san_pham", "product")
        if not title:
            continue
        price = _parse_price(col(row, "gia", "price", "don_gia", "unit_price"))
        if price <= 0:
            continue
        sid = shop_id or col(row, "shop", "shop_id", "cua_hang")
        if not sid:
            continue
        key = col(row, "key", "part_key", "ma_chuan") or normalize(title)
        out.append(
            Offer(
                shop_id=sid,
                title=title,
                unit_price=price,
                part_key=key,
                sku=col(row, "sku", "ma", "ma_sp"),
                url=col(row, "url", "link"),
                stock=_parse_int(col(row, "ton", "stock", "ton_kho", "con_lai"), None),
                moq=_parse_int(col(row, "moq", "toi_thieu"), 1) or 1,
                pack=_parse_int(col(row, "pack", "goi", "so_con", "sl_goi"), 0)
                or _guess_pack(title),
                price_breaks=_parse_breaks(col(row, "bac_gia", "price_breaks")),
                updated_at=col(row, "cap_nhat", "updated_at"),
            )
        )
    return out


class Catalog:
    """Tập hợp offer của mọi shop, có đánh chỉ mục để khớp nhanh."""

    def __init__(self, offers: list[Offer] | None = None) -> None:
        self.offers: list[Offer] = []
        self._by_key: dict[str, list[Offer]] = {}
        if offers:
            self.add_all(offers)

    def add(self, offer: Offer) -> None:
        if not offer.part_key:
            offer.part_key = normalize(offer.title)
        if not offer.part_key:
            return
        self.offers.append(offer)
        self._by_key.setdefault(offer.part_key, []).append(offer)

    def add_all(self, offers: list[Offer]) -> None:
        for o in offers:
            self.add(o)

    def __len__(self) -> int:
        return len(self.offers)

    @property
    def keys(self) -> list[str]:
        return list(self._by_key)

    # ------------------------------------------------------------- khớp

    def match(self, line: BomLine, min_score: float = DEFAULT_MIN_SCORE) -> list[Match]:
        """Mọi offer được cho là ứng với dòng BOM này, tốt nhất đứng đầu."""
        found: dict[int, Match] = {}    # id(offer) -> Match, giữ điểm cao nhất

        def note(offer: Offer, score: float, how: str) -> None:
            cur = found.get(id(offer))
            if cur is None or score > cur.score:
                found[id(offer)] = Match(offer, score, how)

        for o in self._by_key.get(line.key, ()):
            note(o, 1.0, "exact")
        for alt in line.alt_keys:
            for o in self._by_key.get(alt, ()):
                note(o, 0.9, "alt")

        is_mpn = parse(line.raw or line.key).kind == "mpn"
        for key, offers in self._by_key.items():
            if key == line.key:
                continue
            score, how = key_relevance(line.key, key, is_mpn)
            if score >= min_score:
                for o in offers:
                    note(o, round(score, 3), how)

        return sorted(found.values(), key=lambda m: (-m.score, m.offer.unit_price))


def key_relevance(bom_key: str, offer_key: str, is_mpn: bool) -> tuple[float, str]:
    """Hai khoá này có phải cùng một linh kiện không, và tin được bao nhiêu.

    Đây là luật khớp DUY NHẤT của cả tool. Lúc lấy giá về (lọc kết quả tìm
    kiếm của shop) và lúc ghép BOM với catalog đều phải gọi hàm này — trước
    đây hai chỗ dùng hai luật khác nhau, nên khâu lấy giá vứt mất đúng những
    sản phẩm mà khâu ghép sau đó lại chấp nhận (NE555 với NE555P).
    """
    if bom_key and bom_key == offer_key:
        return 1.0, "exact"
    if is_mpn and _prefix_match(bom_key, offer_key):
        return 0.75, "prefix"
    return similarity(bom_key, offer_key), "fuzzy"


def relevance(query: str, title: str) -> float:
    """Mức liên quan giữa câu người dùng gõ và tên sản phẩm của shop."""
    q, t = normalize(query), normalize(title)
    score, _ = key_relevance(q, t, parse(query).kind == "mpn")
    return max(score, similarity(query, title))


def _prefix_match(a: str, b: str) -> bool:
    """'ne555' vs 'ne555p' -> True. 'lm78' vs 'lm7805' -> False (quá ngắn)."""
    if len(a) < MIN_PREFIX_LEN or len(b) < MIN_PREFIX_LEN:
        return False
    if not (MPN_RE.match(a) and MPN_RE.match(b)):
        return False
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    # phần đuôi thêm vào phải ngắn — hậu tố kiểu chân/nhiệt độ, không phải mã khác
    return hi.startswith(lo) and len(hi) - len(lo) <= 3


def resolve(
    lines: list[BomLine],
    catalog: Catalog,
    min_score: float = DEFAULT_MIN_SCORE,
) -> tuple[list[Offer], dict[str, list[Match]]]:
    """Khớp toàn bộ BOM với catalog.

    Trả về (offer đã gán khoá theo BOM, bảng khớp để soi lại). Offer được sao
    chép với `part_key` đổi thành khoá của dòng BOM, nhờ vậy bộ tối ưu chỉ cần
    so khoá bằng nhau, không phải biết gì về chuyện khớp mờ.
    """
    matched: list[Offer] = []
    report: dict[str, list[Match]] = {}
    for line in lines:
        ms = catalog.match(line, min_score)
        report[line.key] = ms
        for m in ms:
            matched.append(replace(m.offer, part_key=line.key))
    return matched, report
