"""Bộ nhớ đệm cho các trang đã tải, dựa trên SQLite.

Có hai lý do, và lý do thứ hai mới là chính:

1. Chạy lại `fetch` không phải tải lại từ đầu.
2. Giảm số lần gõ vào máy chủ của shop. Tra một BOM 50 món trên 6 shop là
   300 lượt truy vấn; không có cache thì mỗi lần chỉnh BOM lại nện thêm 300
   lượt nữa, vừa bất lịch sự vừa dễ bị chặn IP.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DEFAULT_TTL = 24 * 3600      # giá linh kiện không đổi theo giờ, một ngày là hợp lý


class Cache:
    def __init__(self, path: str | Path, ttl: int = DEFAULT_TTL) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.db = sqlite3.connect(str(self.path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS pages ("
            " url TEXT PRIMARY KEY,"
            " body TEXT NOT NULL,"
            " fetched_at REAL NOT NULL)"
        )
        self.db.commit()

    def get(self, url: str) -> str | None:
        row = self.db.execute(
            "SELECT body, fetched_at FROM pages WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        body, fetched_at = row
        if self.ttl and time.time() - fetched_at > self.ttl:
            return None
        return body

    def put(self, url: str, body: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO pages (url, body, fetched_at) VALUES (?, ?, ?)",
            (url, body, time.time()),
        )
        self.db.commit()

    def clear(self) -> int:
        n = self.db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        self.db.execute("DELETE FROM pages")
        self.db.commit()
        return n

    def close(self) -> None:
        self.db.close()
