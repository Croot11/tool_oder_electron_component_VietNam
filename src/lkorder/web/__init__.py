"""Giao diện web chạy tại máy.

    python -m lkorder web

Mở http://127.0.0.1:8765 trên trình duyệt. Dùng `http.server` của thư viện
chuẩn nên không phải cài gì thêm.

Chỉ lắng nghe trên 127.0.0.1, tức chỉ máy này vào được. Đây là công cụ cá
nhân, không có đăng nhập, nên đừng mở ra mạng ngoài.
"""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .. import bom as bom_mod
from .. import config
from ..catalog import DEFAULT_MIN_SCORE, Catalog, load_offers_csv
from ..catalog import resolve as resolve_bom
from ..models import BomLine, Shop, Solution
from ..optimizer import Options, compare_scenarios, solve

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------- chuyển sang JSON


def solution_json(sol: Solution) -> dict:
    orders = []
    by_shop = sol.by_shop()
    for sid in sorted(by_shop, key=lambda s: -sol.subtotal_of(s)):
        shop = sol.shops[sid]
        sub = sol.subtotal_of(sid)
        ship = shop.ship_cost(sub)
        orders.append({
            "shop_id": sid,
            "shop": shop.name,
            "url": shop.url,
            "prep_days": shop.prep_days,
            "subtotal": sub,
            "shipping": ship,
            "total": sub + ship,
            "free_ship_threshold": shop.free_ship_threshold,
            "items": [
                {
                    "need": a.line.raw or a.line.key,
                    "need_qty": a.line.qty,
                    "title": a.offer.title,
                    "sku": a.offer.sku,
                    "url": a.offer.url,
                    "qty": a.qty,
                    "qty_label": a.qty_label(),
                    "pieces": a.pieces,
                    "surplus": a.surplus,
                    "unit_price": a.unit_price,
                    "subtotal": a.subtotal,
                }
                for a in by_shop[sid]
            ],
        })
    return {
        "orders": orders,
        "unmet": [{"need": ln.raw or ln.key, "qty": ln.qty} for ln in sol.unmet],
        "parts_cost": sol.parts_cost,
        "shipping_cost": sol.shipping_cost,
        "total": sol.real_total,
        "n_shops": sol.n_shops,
        "prep_days": sol.max_prep_days,
    }


def shop_json(s: Shop) -> dict:
    return {
        "id": s.id, "name": s.name, "url": s.url, "platform": s.platform,
        "shipping_fee": s.shipping_fee, "free_ship_threshold": s.free_ship_threshold,
        "order_penalty": s.order_penalty, "prep_days": s.prep_days,
        "enabled": s.enabled, "note": s.note,
        "no_image_means_out_of_stock": s.no_image_means_out_of_stock,
    }


# ------------------------------------------------------- tác vụ bỏ giỏ nền

# Nhóm kết quả hiển thị cho người dùng
GROUP_OF = {
    "added": "added",           # đã thêm vào giỏ
    "not_found": "not_found",   # không thấy
    "out_of_stock": "out_of_stock",   # hết hàng: không bỏ giỏ, không đổi shop
    "uncertain": "check",       # cần kiểm tra
    "error": "check",
}


def shop_base_url(link: str) -> str:
    """'https://shop.vn/collections/ic?x=1' -> 'https://shop.vn'. Sai thì ''."""
    parts = urlsplit((link or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def shop_url_ok(link: str) -> bool:
    return bool(shop_base_url(link))


def _host(url: str) -> str:
    h = urlsplit(url or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def line_result_json(res: Any) -> dict:
    """LineResult (browser.cart) -> dict. Đọc kiểu duck-typing cho dễ test."""
    prod = getattr(res, "product", None)
    status = getattr(res, "status", "error")
    return {
        "status": status,
        "group": GROUP_OF.get(status, "check"),
        "title": getattr(prod, "title", "") if prod else "",
        "url": getattr(prod, "url", "") if prod else "",
        "price": getattr(prod, "price", 0) if prod else 0,
        "units": getattr(res, "units", 0),
        "pieces": getattr(res, "pieces", 0),
        "query": getattr(res, "query", ""),
        "message": getattr(res, "message", ""),
        "candidates": [
            {"title": c.title, "url": c.url, "price": c.price,
             "score": round(c.score, 2)}
            for c in (getattr(res, "candidates", None) or [])[:3]
        ],
    }


class CartJob:
    """Trạng thái một lượt bỏ giỏ chạy nền. Mọi thao tác đều có khoá."""

    def __init__(self, lines: list[BomLine], shop_url: str, filename: str = "") -> None:
        self.id = uuid.uuid4().hex[:10]
        self.lines = lines
        self.shop_url = shop_url
        self.filename = filename
        self.status = "running"          # running / done / cancelled / error
        self.error = ""
        self.log_lines: list[str] = []
        self.started = time.time()
        self.finished: float | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.items: list[dict] = [
            {"i": i, "need": ln.raw or ln.key, "qty": ln.qty,
             "status": "pending", "group": ""}
            for i, ln in enumerate(lines)
        ]

    # --- runner gọi
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def log(self, msg: str) -> None:
        with self._lock:
            self.log_lines.append(str(msg))
            del self.log_lines[:-50]

    def start_line(self, i: int) -> None:
        with self._lock:
            self.items[i]["status"] = "running"

    def finish_line(self, i: int, res: Any) -> None:
        data = line_result_json(res)
        with self._lock:
            self.items[i].update(data)

    def finish(self, status: str = "done", error: str = "") -> None:
        with self._lock:
            # dòng chưa chạy tới (bị dừng / lỗi) đánh dấu bỏ qua
            for it in self.items:
                if it["status"] in ("pending", "running"):
                    it["status"] = "skipped"
                    it["group"] = "check"
                    it.setdefault("message", "")
                    it["message"] = it["message"] or "chưa chạy tới"
            self.status, self.error = status, error
            self.finished = time.time()

    # --- giao diện đọc
    def to_json(self) -> dict:
        with self._lock:
            items = [dict(it) for it in self.items]
            counts = {"added": 0, "not_found": 0, "out_of_stock": 0, "check": 0}
            done = 0
            for it in items:
                if it["status"] not in ("pending", "running"):
                    done += 1
                if it.get("group") in counts:
                    counts[it["group"]] += 1
            return {
                "id": self.id,
                "status": self.status,
                "error": self.error,
                "shop_url": self.shop_url,
                "filename": self.filename,
                "total": len(items),
                "done": done,
                "counts": counts,
                "items": items,
                "log": list(self.log_lines[-8:]),
                "elapsed": round((self.finished or time.time()) - self.started, 1),
            }


# runner(lines, shop_url, job, shops) — chạy đồng bộ trong luồng nền,
# báo tiến độ qua job.start_line / job.finish_line, dừng sớm khi job.cancelled.
CartRunner = Callable[[list[BomLine], str, CartJob, dict], None]


def browser_cart_runner(lines: list[BomLine], shop_url: str, job: CartJob,
                        shops: dict[str, Shop]) -> None:
    """Runner thật: mở Chrome bằng profile đã đăng nhập, bỏ từng dòng vào giỏ.

    KHÔNG thanh toán — CartFiller từ chối mọi nút kiểu "thanh toán".
    """
    from ..browser import BrowserSession
    from ..browser.session import login_options_for
    from ..browser.cart import DEFAULT_DELAY, CartFiller, shop_option

    base = shop_base_url(shop_url)
    host = _host(base)
    platform = next((s.platform for s in shops.values()
                     if s.url and _host(s.url) == host), "")
    job.log("Đang mở trình duyệt…")
    with BrowserSession() as session:
        page = session.open_shop(shop_url, notify=job.log,
                                 **login_options_for(base))
        job.log("Đã vào shop, bắt đầu tìm từng linh kiện.")
        filler = CartFiller(page, base, platform=platform,
                            no_image_out_of_stock=shop_option(
                                base, "no_image_means_out_of_stock", shops,
                                default=None))
        for i, line in enumerate(lines):
            if job.cancelled:
                return
            if i:
                lo, hi = DEFAULT_DELAY
                time.sleep(random.uniform(lo, hi))
            job.start_line(i)
            job.finish_line(i, filler.add_line(line))


# ------------------------------------------------------------------ xử lý


class App:
    """Giữ cấu hình và catalog, nạp lại khi file trên đĩa đổi."""

    def __init__(self, shops_path: Path | None, catalog_path: Path | None,
                 cart_runner: CartRunner | None = None) -> None:
        self.shops_path = Path(shops_path) if shops_path else config.default_shops_path()
        self.catalog_path = (
            Path(catalog_path) if catalog_path else config.default_catalog_path()
        )
        self._lock = threading.Lock()
        self.cart_runner: CartRunner = cart_runner or browser_cart_runner
        self._jobs: dict[str, CartJob] = {}
        self._last_job: CartJob | None = None
        self._threads: dict[str, threading.Thread] = {}

    # --- bỏ giỏ nền

    def start_cart(self, payload: dict) -> dict:
        lines = bom_mod.parse_any(payload.get("bom") or "")
        if not lines:
            return {"error": "File BOM không có linh kiện nào đọc được."}
        shop_url = str(payload.get("shop_url") or "").strip()
        if not shop_url_ok(shop_url):
            return {"error": "Link shop phải bắt đầu bằng http:// hoặc https://"}
        with self._lock:
            cur = self._last_job
            if cur is not None and cur.status == "running":
                return {"error": "Đang chạy một lượt khác, chờ xong hoặc bấm Dừng."}
            job = CartJob(lines, shop_url, str(payload.get("filename") or ""))
            self._jobs = {job.id: job}        # chỉ giữ lượt gần nhất
            self._last_job = job
        shops = self.shops()

        def work() -> None:
            try:
                self.cart_runner(lines, shop_url, job, shops)
            except Exception as e:                       # noqa: BLE001
                job.finish("error", f"{type(e).__name__}: {e}")
                return
            job.finish("cancelled" if job.cancelled else "done")

        t = threading.Thread(target=work, name=f"cart-{job.id}", daemon=True)
        self._threads = {job.id: t}
        t.start()
        return {"ok": True, "id": job.id, "total": len(lines)}

    def job(self, job_id: str = "") -> dict:
        job = self._jobs.get(job_id) if job_id else self._last_job
        if job is None:
            return {"job": None} if not job_id else {"error": "không có lượt chạy này"}
        return {"job": job.to_json()}

    def stop_cart(self, payload: dict) -> dict:
        job_id = str(payload.get("id") or "")
        job = self._jobs.get(job_id) if job_id else self._last_job
        if job is None:
            return {"error": "không có lượt chạy này"}
        job.cancel()
        job.log("Đã yêu cầu dừng, xong linh kiện đang làm sẽ dừng.")
        return {"ok": True}

    def wait_job(self, job_id: str, timeout: float = 10.0) -> None:
        """Chờ luồng nền chạy xong (dùng trong test)."""
        t = self._threads.get(job_id)
        if t is not None:
            t.join(timeout)

    def shops(self) -> dict[str, Shop]:
        return config.load_shops(self.shops_path)

    def catalog(self) -> Catalog:
        cat = Catalog()
        if self.catalog_path.exists():
            cat.add_all(load_offers_csv(self.catalog_path))
        return cat

    # --- các thao tác

    def state(self) -> dict:
        cat = self.catalog()
        return {
            "shops": [shop_json(s) for s in self.shops().values()],
            "catalog": {
                "path": str(self.catalog_path),
                "n_offers": len(cat),
                "n_keys": len(cat.keys),
            },
            "bom_path": str(config.project_root() / "data" / "bom_mau.txt"),
        }

    def solve(self, payload: dict) -> dict:
        text = payload.get("bom") or ""
        lines = bom_mod.parse_any(text)
        if not lines:
            return {"error": "Chưa nhập linh kiện nào."}

        shops = self.shops()
        cat = self.catalog()
        if not len(cat):
            return {"error": f"Bảng giá trống: {self.catalog_path}"}

        min_score = float(payload.get("min_score") or DEFAULT_MIN_SCORE)
        offers, report = resolve_bom(lines, cat, min_score)

        penalty = payload.get("penalty")
        penalty = int(penalty) if penalty not in (None, "") else None
        max_shops = payload.get("max_shops")
        max_shops = int(max_shops) if max_shops else None

        sol = solve(lines, offers, shops,
                    Options(shop_penalty=penalty, max_shops=max_shops))

        scenarios = [
            {
                "k": s.n_shops,
                "shops": [s.shops[i].name for i in s.shop_ids],
                "total": s.real_total,
                "unmet": len(s.unmet),
            }
            for _k, s in compare_scenarios(lines, offers, shops)
        ]

        # Cảnh báo khớp yếu: đây là chỗ tool dễ hiểu sai ý người dùng nhất,
        # nên phải đẩy lên mặt giao diện chứ không giấu đi.
        warnings = []
        for line in lines:
            ms = report.get(line.key, [])
            if not ms:
                continue
            best = ms[0]
            if best.score < 0.9:
                warnings.append({
                    "need": line.raw or line.key,
                    "matched": best.offer.title,
                    "shop": shops[best.offer.shop_id].name
                    if best.offer.shop_id in shops else best.offer.shop_id,
                    "score": round(best.score, 2),
                    "how": best.how,
                })

        return {
            "solution": solution_json(sol),
            "scenarios": scenarios,
            "warnings": warnings,
            "n_lines": len(lines),
        }

    def matches(self, payload: dict) -> dict:
        text = payload.get("bom") or ""
        lines = bom_mod.parse_any(text)
        if not lines:
            return {"rows": []}
        shops = self.shops()
        cat = self.catalog()
        min_score = float(payload.get("min_score") or DEFAULT_MIN_SCORE)
        _, report = resolve_bom(lines, cat, min_score)
        rows = []
        for line in lines:
            ms = report.get(line.key, [])
            rows.append({
                "need": line.raw or line.key,
                "qty": line.qty,
                "key": line.key,
                "found": [
                    {
                        "shop": shops[m.offer.shop_id].name
                        if m.offer.shop_id in shops else m.offer.shop_id,
                        "title": m.offer.title,
                        "price": m.offer.unit_price,
                        "pack": m.offer.pack,
                        "url": m.offer.url,
                        "score": round(m.score, 2),
                        "how": m.how,
                    }
                    for m in ms[:6]
                ],
            })
        return {"rows": rows}

    def save_shops(self, payload: dict) -> dict:
        items = payload.get("shops") or []
        shops: dict[str, Shop] = {}
        for it in items:
            sid = str(it.get("id") or "").strip()
            if not sid:
                continue
            shops[sid] = Shop(
                id=sid,
                name=str(it.get("name") or sid),
                url=str(it.get("url") or ""),
                platform=str(it.get("platform") or "csv"),
                shipping_fee=max(0, int(it.get("shipping_fee") or 0)),
                free_ship_threshold=max(0, int(it.get("free_ship_threshold") or 0)),
                order_penalty=max(0, int(it.get("order_penalty") or 0)),
                prep_days=max(0, int(it.get("prep_days") or 0)),
                enabled=bool(it.get("enabled", True)),
                note=str(it.get("note") or ""),
                no_image_means_out_of_stock=bool(
                    it.get("no_image_means_out_of_stock", False)),
            )
        if not shops:
            return {"error": "Phải có ít nhất một shop."}
        with self._lock:
            config.save_shops(shops, self.shops_path)
        return {"ok": True, "n": len(shops)}

    def save_catalog(self, payload: dict) -> dict:
        text = payload.get("csv") or ""
        if not text.strip():
            return {"error": "Nội dung trống."}
        with self._lock:
            self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            self.catalog_path.write_text(text, encoding="utf-8")
        return {"ok": True, "n_offers": len(self.catalog())}

    def read_catalog(self) -> dict:
        if not self.catalog_path.exists():
            return {"csv": ""}
        return {"csv": self.catalog_path.read_text(encoding="utf-8-sig")}


# ------------------------------------------------------------------ server


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lkorder"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: dict, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                html = (HERE / "index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(app.state())
            elif path == "/api/catalog":
                self._json(app.read_catalog())
            elif path == "/api/job":
                q = parse_qs(urlsplit(self.path).query)
                self._json(app.job((q.get("id") or [""])[0]))
            else:
                self._json({"error": "không có trang này"}, 404)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "dữ liệu gửi lên không hợp lệ"}, 400)
                return

            routes = {
                "/api/solve": app.solve,
                "/api/match": app.matches,
                "/api/shops": app.save_shops,
                "/api/catalog": app.save_catalog,
                "/api/start": app.start_cart,
                "/api/stop": app.stop_cart,
            }
            fn = routes.get(path)
            if fn is None:
                self._json({"error": "không có trang này"}, 404)
                return
            try:
                self._json(fn(payload))
            except Exception as e:                       # noqa: BLE001
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def log_message(self, *args) -> None:
            pass      # không rác terminal

    return Handler


def serve(port: int = 8765, open_browser: bool = True,
          shops_path: Path | None = None, catalog_path: Path | None = None) -> None:
    app = App(shops_path, catalog_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}"
    print(f"Giao diện đang chạy: {url}")
    print("Nhấn Ctrl+C để dừng.\n")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    finally:
        httpd.server_close()
