"""Nạp cấu hình shop.

Cấu hình để ở JSON (stdlib đọc được, không cần cài thêm gì) tại
`data/shops.json`. Người dùng sửa file này để thêm shop, chỉnh phí ship, hoặc
chỉnh "chi phí phiền" theo cảm nhận riêng của mình về việc phải chờ thêm đơn.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Shop

DEFAULT_SHIPPING = 30_000
DEFAULT_PENALTY = 20_000


def package_root() -> Path:
    return Path(__file__).resolve().parent


def project_root() -> Path:
    return package_root().parent.parent


def default_shops_path() -> Path:
    return project_root() / "data" / "shops.json"


def default_catalog_path() -> Path:
    return project_root() / "data" / "catalog.csv"


def load_shops(path: str | Path | None = None) -> dict[str, Shop]:
    p = Path(path) if path else default_shops_path()
    if not p.exists():
        raise FileNotFoundError(
            f"Không tìm thấy cấu hình shop: {p}\n"
            f"Chạy `lk init` để tạo file mẫu."
        )
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw["shops"] if isinstance(raw, dict) and "shops" in raw else raw

    shops: dict[str, Shop] = {}
    for it in items:
        sid = it["id"]
        shops[sid] = Shop(
            id=sid,
            name=it.get("name", sid),
            url=it.get("url", ""),
            platform=it.get("platform", "csv"),
            shipping_fee=int(it.get("shipping_fee", DEFAULT_SHIPPING)),
            free_ship_threshold=int(it.get("free_ship_threshold", 0)),
            order_penalty=int(it.get("order_penalty", DEFAULT_PENALTY)),
            prep_days=int(it.get("prep_days", 2)),
            enabled=bool(it.get("enabled", True)),
            note=it.get("note", ""),
            no_image_means_out_of_stock=bool(
                it.get("no_image_means_out_of_stock", False)),
        )
    return shops


def save_shops(shops: dict[str, Shop], path: str | Path | None = None) -> Path:
    p = Path(path) if path else default_shops_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "shops": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "platform": s.platform,
                "shipping_fee": s.shipping_fee,
                "free_ship_threshold": s.free_ship_threshold,
                "order_penalty": s.order_penalty,
                "prep_days": s.prep_days,
                "enabled": s.enabled,
                "note": s.note,
                **({"no_image_means_out_of_stock": True}
                   if s.no_image_means_out_of_stock else {}),
            }
            for s in shops.values()
        ]
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
