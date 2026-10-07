"""Nền chung để lấy giá từ website shop.

Nguyên tắc khi tự động truy vấn web của người khác, đã cài sẵn ở đây:

* Tôn trọng `robots.txt`. Đường dẫn nào bị cấm thì không đụng tới.
* Có khoảng nghỉ giữa hai lượt truy vấn cùng một shop (mặc định 1,5 giây).
* Có cache, để chạy lại không nện thêm lượt nào.
* Tự khai danh tính trong User-Agent, không giả làm trình duyệt.

Phần bóc tách dữ liệu (`parse_*`) được tách hẳn khỏi phần tải mạng, nhờ vậy
kiểm thử được bằng dữ liệu mẫu lưu sẵn, không cần mạng.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Iterable

from ..models import Offer, Shop

USER_AGENT = (
    "lkorder/0.1 (cong cu gom don linh kien ca nhan; "
    "ton trong robots.txt; lien he: nguoi dung cuoi)"
)

DEFAULT_DELAY = 1.5      # giây giữa hai lượt gọi cùng một shop
DEFAULT_TIMEOUT = 15


class FetchError(Exception):
    pass


class HttpClient:
    """Tải trang, có nghỉ giữa các lượt, có cache, có kiểm tra robots.txt."""

    def __init__(self, cache=None, delay: float = DEFAULT_DELAY,
                 timeout: int = DEFAULT_TIMEOUT, respect_robots: bool = True) -> None:
        self.cache = cache
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self._last_call: dict[str, float] = {}     # host -> lúc gọi gần nhất
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # ------------------------------------------------------------ robots

    def _robots_for(self, url: str):
        host = urllib.parse.urlsplit(url).netloc
        if host in self._robots:
            return self._robots[host]
        parts = urllib.parse.urlsplit(url)
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{parts.scheme}://{host}/robots.txt")
        try:
            rp.read()
        except Exception:
            # Không đọc được robots.txt thì coi như không có quy định riêng.
            rp = None
        self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(USER_AGENT, url)

    # -------------------------------------------------------------- tải

    def get(self, url: str) -> str:
        if self.cache is not None:
            hit = self.cache.get(url)
            if hit is not None:
                return hit

        if not self.allowed(url):
            raise FetchError(f"robots.txt của shop không cho phép: {url}")

        host = urllib.parse.urlsplit(url).netloc
        wait = self.delay - (time.monotonic() - self._last_call.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)

        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.9",
            "Accept-Language": "vi,en;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as e:
            raise FetchError(f"HTTP {e.code} khi tải {url}") from e
        except Exception as e:
            raise FetchError(f"Không tải được {url}: {e}") from e
        finally:
            self._last_call[host] = time.monotonic()

        body = raw.decode(charset, errors="replace")
        if self.cache is not None:
            self.cache.put(url, body)
        return body

    def get_json(self, url: str):
        body = self.get(url)
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise FetchError(f"{url} không trả về JSON hợp lệ") from e


class Fetcher:
    """Lớp cơ sở cho một cách lấy giá.

    Lớp con phải cài `search_urls()` (đi tìm ở đâu) và `parse()` (bóc gì ra từ
    nội dung tải về). Tách đôi như vậy để `parse()` kiểm thử được offline.
    """

    platform = ""

    def __init__(self, shop: Shop, client: HttpClient) -> None:
        self.shop = shop
        self.client = client

    # --- lớp con cài đặt

    def search_urls(self, query: str) -> list[str]:
        raise NotImplementedError

    def parse(self, body: str, query: str) -> list[Offer]:
        raise NotImplementedError

    # --- dùng chung

    def search(self, query: str) -> list[Offer]:
        """Tìm `query` ở shop này. Lỗi mạng không làm hỏng cả lượt chạy."""
        out: list[Offer] = []
        for url in self.search_urls(query):
            try:
                body = self.client.get(url)
            except FetchError:
                continue
            try:
                found = self.parse(body, query)
            except Exception:
                continue
            if found:
                out.extend(found)
                break     # URL đầu tiên có kết quả là đủ
        return out

    # --- tiện ích cho lớp con

    def abs_url(self, path: str) -> str:
        return urllib.parse.urljoin(self.shop.url.rstrip("/") + "/", path.lstrip("/"))

    def make_offer(self, title: str, price: int, **kw) -> Offer:
        return Offer(shop_id=self.shop.id, title=title, unit_price=price, **kw)


def dedupe(offers: Iterable[Offer]) -> list[Offer]:
    """Bỏ trùng theo (shop, sku hoặc tên), giữ bản rẻ nhất."""
    best: dict[tuple[str, str], Offer] = {}
    for o in offers:
        k = (o.shop_id, o.sku or o.title.lower())
        cur = best.get(k)
        if cur is None or o.unit_price < cur.unit_price:
            best[k] = o
    return list(best.values())
