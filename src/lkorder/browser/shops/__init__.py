"""Bộ xử lý riêng cho từng shop (khi khuôn chung của `cart` đoán không chuẩn).

Mỗi mục trong `ADAPTERS` là (hàm nhận diện link shop, lớp CartFiller riêng).
`CartFiller(page, base_url, ...)` tự tra bảng này, nên CLI và giao diện web
không cần biết shop nào có bộ xử lý riêng.
"""

from __future__ import annotations

from typing import Callable

from .cxtvn import CxtCartFiller, is_cxt_url

ADAPTERS: list[tuple[Callable[[str], bool], type]] = [
    (is_cxt_url, CxtCartFiller),
]


def filler_class_for(base_url: str) -> type | None:
    """Lớp CartFiller riêng cho link shop này, không có thì None."""
    for match, cls in ADAPTERS:
        if match(base_url or ""):
            return cls
    return None


__all__ = ["ADAPTERS", "CxtCartFiller", "filler_class_for", "is_cxt_url"]
