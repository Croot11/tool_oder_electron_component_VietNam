"""Lấy giá tự động từ website shop.

    from lkorder.shops import fetch_bom
    offers, log = fetch_bom(lines, shops, cache_path="data/cache.sqlite")

Shop nào có `platform` là "csv" (mặc định) thì bỏ qua — đó là shop chỉ đọc giá
từ file, không tra mạng.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from ..cache import Cache
from ..models import BomLine, Offer, Shop
from ..catalog import relevance
from .base import Fetcher, FetchError, HttpClient, dedupe
from .platforms import FETCHERS, HaravanFetcher, JsonLdFetcher, WooFetcher

__all__ = [
    "Fetcher", "FetchError", "HttpClient", "FETCHERS",
    "HaravanFetcher", "WooFetcher", "JsonLdFetcher",
    "make_fetcher", "fetch_bom",
]

# Kết quả tìm kiếm của shop hay trả về cả hàng chẳng liên quan. Giữ lại thứ đủ
# giống tên cần tìm, còn lại vứt, nếu không catalog sẽ đầy rác.
MIN_RELEVANCE = 0.35


def make_fetcher(shop: Shop, client: HttpClient) -> Fetcher | None:
    cls = FETCHERS.get(shop.platform)
    if cls is None or not shop.url:
        return None
    return cls(shop, client)


def fetch_bom(
    lines: Sequence[BomLine],
    shops: dict[str, Shop],
    cache_path: str | Path | None = None,
    delay: float = 1.5,
    min_relevance: float = MIN_RELEVANCE,
    on_progress=None,
) -> tuple[list[Offer], list[str]]:
    """Tra từng dòng BOM ở từng shop có hỗ trợ lấy tự động.

    Trả về (offer tìm được, nhật ký). Nhật ký quan trọng không kém dữ liệu:
    nó cho biết shop nào im lặng, để còn biết mà nhập tay.
    """
    cache = Cache(cache_path) if cache_path else None
    client = HttpClient(cache=cache, delay=delay)
    log: list[str] = []
    found: list[Offer] = []

    targets = [s for s in shops.values() if s.enabled and s.platform != "csv"]
    if not targets:
        log.append("Không shop nào bật lấy giá tự động "
                   "(đặt 'platform' khác 'csv' trong shops.json).")
        return [], log

    for shop in targets:
        fetcher = make_fetcher(shop, client)
        if fetcher is None:
            log.append(f"{shop.name}: không rõ nền tảng '{shop.platform}', bỏ qua.")
            continue

        got = 0
        for line in lines:
            query = line.raw or line.key
            if on_progress:
                on_progress(shop, line)
            try:
                results = fetcher.search(query)
            except Exception as e:                     # noqa: BLE001
                log.append(f"{shop.name}: lỗi khi tìm '{query}': {e}")
                continue
            keep = [
                o for o in results
                if relevance(query, o.title) >= min_relevance
            ]
            found.extend(keep)
            got += len(keep)
        log.append(f"{shop.name}: lấy được {got} sản phẩm.")

    if cache is not None:
        cache.close()
    return dedupe(found), log


def offers_to_csv(offers: Iterable[Offer]) -> str:
    """Xuất ra đúng khuôn mà `catalog.csv` đọc được."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["shop", "ten", "gia", "sku", "ton", "moq", "pack", "bac_gia", "url"])
    for o in offers:
        w.writerow([o.shop_id, o.title, o.unit_price, o.sku,
                    "" if o.stock is None else o.stock,
                    o.moq, o.pack if o.pack > 1 else "", "", o.url])
    return buf.getvalue()
