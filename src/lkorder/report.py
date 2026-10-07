"""Trình bày kết quả: bảng cho terminal, và file để lưu lại.

Bảng tự canh theo bề rộng thật của chữ tiếng Việt — dấu tổ hợp không chiếm ô
nào trên màn hình nên không được tính vào độ rộng, nếu không cột sẽ lệch.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from typing import Sequence

from .models import Solution


def vnd(n: int) -> str:
    return f"{n:,}".replace(",", ".") + "đ"


def width(s: str) -> int:
    """Bề rộng hiển thị: bỏ qua dấu tổ hợp, ký tự Đông Á tính 2 ô."""
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(s: str, n: int, align: str = "<") -> str:
    fill = max(0, n - width(s))
    if align == ">":
        return " " * fill + s
    return s + " " * fill


def trunc(s: str, n: int) -> str:
    if width(s) <= n:
        return s
    out = ""
    for ch in s:
        if width(out + ch) > n - 1:
            break
        out += ch
    return out + "…"


def table(rows: Sequence[Sequence[str]], headers: Sequence[str],
          aligns: Sequence[str] | None = None, indent: str = "") -> str:
    aligns = list(aligns or ["<"] * len(headers))
    cols = [max(width(headers[i]), *(width(r[i]) for r in rows)) if rows
            else width(headers[i]) for i in range(len(headers))]
    out = [indent + "  ".join(pad(h, cols[i], aligns[i])
                              for i, h in enumerate(headers)).rstrip()]
    out.append(indent + "  ".join("─" * c for c in cols))
    for r in rows:
        out.append(indent + "  ".join(pad(r[i], cols[i], aligns[i])
                                      for i in range(len(headers))).rstrip())
    return "\n".join(out)


def render(sol: Solution, show_url: bool = False) -> str:
    """Bản kê chi tiết: mua gì, ở đâu, hết bao nhiêu."""
    if not sol.allocations and not sol.unmet:
        return "Không có gì để đặt."

    parts: list[str] = []
    by_shop = sol.by_shop()
    order = sorted(by_shop, key=lambda s: -sol.subtotal_of(s))

    for i, sid in enumerate(order, 1):
        shop = sol.shops[sid]
        allocs = by_shop[sid]
        sub = sol.subtotal_of(sid)
        ship = shop.ship_cost(sub)

        head = f"┌─ ĐƠN {i}/{len(order)}: {shop.name}"
        if shop.url:
            head += f"  ({shop.url})"
        parts.append(head)

        rows = [
            [
                trunc(a.line.raw or a.line.key, 30),
                trunc(a.offer.title, 34),
                a.qty_label(),
                vnd(a.unit_price),
                vnd(a.subtotal),
            ] + ([a.offer.url or "-"] if show_url else [])
            for a in allocs
        ]
        headers = ["Cần", "Sản phẩm", "Mua", "Đơn giá", "Thành tiền"] + (
            ["Link"] if show_url else []
        )
        aligns = ["<", "<", ">", ">", ">"] + ([">"] if show_url else [])
        parts.append(table(rows, headers, aligns, indent="│  "))

        extra = [a for a in allocs if a.surplus > 0]
        if extra:
            parts.append("│")
            for a in extra:
                parts.append(
                    f"│  ↳ {trunc(a.line.raw or a.line.key, 28)}: cần {a.line.qty}, "
                    f"mua nguyên gói nên nhận {a.pieces} (dư {a.surplus})"
                )

        line = f"│  Tiền hàng {vnd(sub)}"
        if ship:
            line += f" + ship {vnd(ship)}"
        else:
            line += f" + ship 0đ (đã đạt mức freeship {vnd(shop.free_ship_threshold)})"
        parts.append("│")
        parts.append(line + f"  =  {vnd(sub + ship)}")
        parts.append("└" + "─" * 60)
        parts.append("")

    if sol.unmet:
        parts.append("⚠ KHÔNG TÌM THẤY (phải tự tìm chỗ khác):")
        for ln in sol.unmet:
            parts.append(f"   • {ln.raw or ln.key}  × {ln.qty}")
        parts.append("")

    parts.append("═" * 62)
    parts.append(
        f"TỔNG: {vnd(sol.real_total)}   "
        f"({vnd(sol.parts_cost)} tiền hàng + {vnd(sol.shipping_cost)} ship)"
    )
    parts.append(
        f"Số đơn phải đặt: {sol.n_shops}"
        + (f"   ·   Dự kiến chờ ~{sol.max_prep_days} ngày" if sol.allocations else "")
    )
    return "\n".join(parts)


def render_scenarios(scenarios: list[tuple[int, Solution]]) -> str:
    """Bảng đánh đổi: gom về ít shop hơn thì đắt thêm bao nhiêu."""
    if not scenarios:
        return "Không có phương án nào."

    # sắp theo số shop tăng dần để đọc từ "gọn nhất" tới "rẻ nhất"
    ordered = sorted(scenarios, key=lambda t: (t[1].n_shops, t[1].real_total))
    full = [s for _, s in ordered if not s.unmet]
    # Chỉ so tiền giữa các phương án MUA ĐỦ. Một phương án thiếu hàng đương
    # nhiên rẻ hơn, nhưng nó rẻ vì thiếu đồ chứ không phải vì tiết kiệm.
    cheapest = min((s.real_total for s in full), default=0)

    rows = []
    for _k, s in ordered:
        if s.unmet:
            extra = "n/a"
            note = f"THIẾU {len(s.unmet)} món — không đặt đủ"
        else:
            d = s.real_total - cheapest
            extra = "rẻ nhất" if d <= 0 else f"+{vnd(d)}"
            note = "mua đủ"
        rows.append([
            str(s.n_shops),
            ", ".join(s.shops[i].name for i in s.shop_ids),
            vnd(s.real_total),
            extra,
            note,
        ])
    return table(
        rows,
        ["Đơn", "Đặt ở", "Tổng trả", "So với rẻ nhất", "Ghi chú"],
        [">", "<", ">", ">", "<"],
    )


def to_csv(sol: Solution) -> str:
    buf = io.StringIO()
    # Đặt lineterminator thành LF để khi ghi ra file, Windows chỉ đổi
    # sang CRLF đúng một lần. Để mặc định (CRLF) thì thành CR CR LF, và
    # Excel hiện một dòng trống xen giữa mỗi dòng dữ liệu.
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["shop", "linh_kien", "can", "san_pham", "sku", "mua_so_dv",
                "so_con_nhan", "don_gia", "thanh_tien", "url"])
    for sid in sol.shop_ids:
        for a in sol.by_shop()[sid]:
            w.writerow([sol.shops[sid].name, a.line.raw or a.line.key, a.line.qty,
                        a.offer.title, a.offer.sku, a.qty, a.pieces,
                        a.unit_price, a.subtotal, a.offer.url])
    for ln in sol.unmet:
        w.writerow(["(không tìm thấy)", ln.raw or ln.key, ln.qty,
                    "", "", "", "", "", "", ""])
    w.writerow([])
    w.writerow(["", "", "", "", "", "Tiền hàng", sol.parts_cost, ""])
    w.writerow(["", "", "", "", "", "Ship", sol.shipping_cost, ""])
    w.writerow(["", "", "", "", "", "Tổng", sol.real_total, ""])
    return buf.getvalue()


def to_markdown(sol: Solution) -> str:
    out = ["# Đơn đặt linh kiện", ""]
    out.append(f"**Tổng: {vnd(sol.real_total)}** · {sol.n_shops} đơn "
               f"({vnd(sol.parts_cost)} hàng + {vnd(sol.shipping_cost)} ship)")
    out.append("")
    for i, sid in enumerate(sol.shop_ids, 1):
        shop = sol.shops[sid]
        sub = sol.subtotal_of(sid)
        out.append(f"## {i}. {shop.name}" + (f" — {shop.url}" if shop.url else ""))
        out.append("")
        out.append("| Cần | Sản phẩm | Mua | Đơn giá | Thành tiền |")
        out.append("|---|---|---:|---:|---:|")
        for a in sol.by_shop()[sid]:
            link = f"[{a.offer.title}]({a.offer.url})" if a.offer.url else a.offer.title
            need = f"{a.line.raw or a.line.key} ×{a.line.qty}"
            out.append(f"| {need} | {link} | {a.qty_label()} | "
                       f"{vnd(a.unit_price)} | {vnd(a.subtotal)} |")
        out.append("")
        out.append(f"Tiền hàng {vnd(sub)} + ship {vnd(shop.ship_cost(sub))} "
                   f"= **{vnd(sub + shop.ship_cost(sub))}**")
        out.append("")
    if sol.unmet:
        out.append("## Không tìm thấy")
        out.append("")
        for ln in sol.unmet:
            out.append(f"- {ln.raw or ln.key} × {ln.qty}")
        out.append("")
    return "\n".join(out)
