"""Giao diện dòng lệnh.

    lk order  bom.txt          -> phương án đặt hàng tốt nhất
    lk compare bom.txt         -> gom về ít shop hơn thì đắt thêm bao nhiêu
    lk match  bom.txt          -> soi lại việc khớp tên, tìm chỗ khớp sai
    lk shops                   -> danh sách shop đang cấu hình
    lk init                    -> tạo file cấu hình + dữ liệu mẫu
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import bom as bom_mod
from . import config, report
from .catalog import DEFAULT_MIN_SCORE, Catalog, load_offers_csv, resolve
from .optimizer import Options, compare_scenarios, solve


def _setup_console() -> None:
    """Windows mặc định là cp1252, in tiếng Việt sẽ vỡ. Ép UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _load(args: argparse.Namespace) -> tuple[list, Catalog, dict]:
    shops = config.load_shops(args.shops)
    cat = Catalog()
    paths = args.catalog or [config.default_catalog_path()]
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise SystemExit(f"Không thấy bảng giá: {p}\nChạy `lk init` để tạo mẫu.")
        cat.add_all(load_offers_csv(p))
    lines = bom_mod.load(args.bom)
    if not lines:
        raise SystemExit(f"BOM rỗng hoặc không đọc được: {args.bom}")
    return lines, cat, shops


# ------------------------------------------------------------------ lệnh


def cmd_order(args: argparse.Namespace) -> int:
    lines, cat, shops = _load(args)
    offers, _ = resolve(lines, cat, args.min_score)
    sol = solve(
        lines, offers, shops,
        Options(shop_penalty=args.penalty, max_shops=args.max_shops),
    )
    print(report.render(sol, show_url=args.urls))

    if args.csv:
        Path(args.csv).write_text(report.to_csv(sol), encoding="utf-8-sig")
        print(f"\n→ Đã lưu CSV: {args.csv}")
    if args.md:
        Path(args.md).write_text(report.to_markdown(sol), encoding="utf-8")
        print(f"→ Đã lưu Markdown: {args.md}")
    return 1 if sol.unmet else 0


def cmd_compare(args: argparse.Namespace) -> int:
    lines, cat, shops = _load(args)
    offers, _ = resolve(lines, cat, args.min_score)
    scenarios = compare_scenarios(lines, offers, shops)
    print("Ép đặt tối đa k đơn thì rẻ nhất là bao nhiêu:\n")
    print(report.render_scenarios(scenarios))
    print(
        "\nMỗi dòng là phương án rẻ nhất khi chỉ được đặt bấy nhiêu đơn."
        "\nChênh lệch giữa hai dòng chính là cái giá của việc bớt một đơn hàng."
    )
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    """Soi lại việc khớp tên — bước kiểm tra quan trọng nhất trước khi đặt."""
    lines, cat, shops = _load(args)
    _, rep = resolve(lines, cat, args.min_score)
    problems = 0
    for line in lines:
        ms = rep.get(line.key, [])
        head = f"{line.raw or line.key}  × {line.qty}"
        if not ms:
            print(f"\n✗ {head}\n    KHÔNG tìm thấy ở shop nào (khoá: {line.key})")
            problems += 1
            continue
        weak = all(m.score < 0.9 for m in ms)
        print(f"\n{'?' if weak else '✓'} {head}   (khoá: {line.key})")
        problems += 1 if weak else 0
        rows = [
            [
                shops[m.offer.shop_id].name if m.offer.shop_id in shops
                else m.offer.shop_id,
                report.trunc(m.offer.title, 40),
                report.vnd(m.offer.unit_price),
                f"{m.score:.2f}",
                m.how,
            ]
            for m in ms[: args.limit]
        ]
        print(report.table(rows, ["Shop", "Sản phẩm", "Giá", "Điểm", "Kiểu"],
                           ["<", "<", ">", ">", "<"], indent="    "))
    print(f"\n{len(lines)} dòng BOM, {problems} dòng cần xem lại bằng mắt.")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Tra giá tự động ở các shop có bật lấy dữ liệu web."""
    from .shops import fetch_bom, offers_to_csv

    shops = config.load_shops(args.shops)
    lines = bom_mod.load(args.bom)
    if not lines:
        raise SystemExit(f"BOM rỗng hoặc không đọc được: {args.bom}")

    live = [s for s in shops.values() if s.enabled and s.platform != "csv"]
    if not live:
        print("Chưa shop nào bật lấy giá tự động.")
        print("Sửa data/shops.json: đổi \"platform\" từ \"csv\" sang "
              "\"haravan\", \"woo\" hoặc \"jsonld\".")
        return 1

    print(f"Tra {len(lines)} linh kiện ở {len(live)} shop "
          f"(nghỉ {args.delay}s giữa mỗi lượt)…\n")

    seen: set[str] = set()

    def progress(shop, line):
        if shop.id not in seen:
            seen.add(shop.id)
            print(f"  → {shop.name} ({shop.platform})")

    offers, log = fetch_bom(
        lines, shops,
        cache_path=None if args.no_cache else config.project_root() / "data"
        / "cache.sqlite",
        delay=args.delay,
        on_progress=progress,
    )

    print()
    for msg in log:
        print(f"  {msg}")

    if not offers:
        print("\nKhông lấy được gì. Có thể shop chặn truy cập tự động, hoặc "
              "\nnền tảng khai trong shops.json chưa đúng. Cứ nhập tay vào "
              "\ndata/catalog.csv là chắc ăn nhất.")
        return 1

    print(f"\nLấy được {len(offers)} sản phẩm.")
    if args.dry_run:
        print("(--dry-run: không ghi file)")
        return 0

    out = Path(args.out) if args.out else config.default_catalog_path()
    csv_text = offers_to_csv(offers)
    if args.append and out.exists():
        old = out.read_text(encoding="utf-8-sig").rstrip("\n")
        body = csv_text.split("\n", 1)[1]        # bỏ dòng tiêu đề
        out.write_text(old + "\n" + body, encoding="utf-8")
        print(f"→ Đã nối vào {out}")
    else:
        out.write_text(csv_text, encoding="utf-8")
        print(f"→ Đã ghi {out}")
    print("\nGiờ chạy `match` để kiểm tra tool hiểu đúng tên linh kiện chưa.")
    return 0


def cmd_shops(args: argparse.Namespace) -> int:
    shops = config.load_shops(args.shops)
    rows = [
        [
            s.id,
            s.name,
            report.vnd(s.shipping_fee),
            report.vnd(s.free_ship_threshold) if s.free_ship_threshold else "-",
            report.vnd(s.order_penalty),
            f"{s.prep_days}n",
            "on" if s.enabled else "OFF",
        ]
        for s in shops.values()
    ]
    print(report.table(
        rows,
        ["id", "Tên", "Ship", "Freeship từ", "Phí phiền", "Chờ", ""],
        ["<", "<", ">", ">", ">", ">", "<"],
    ))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web import serve

    serve(
        port=args.port,
        open_browser=not args.no_open,
        shops_path=Path(args.shops) if args.shops else None,
        catalog_path=Path(args.catalog) if args.catalog else None,
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from .seed import write_seed_files

    created = write_seed_files(Path(args.dir) if args.dir else config.project_root())
    for p in created:
        print(f"  tạo  {p}")
    print("\nXong. Thử ngay:\n  python -m lkorder order data/bom_mau.txt")
    return 0


# ------------------------------------------------------------------ vào


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lk",
        description="Tìm linh kiện ở shop VN và gom về ít đơn nhất có thể.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, need_bom: bool = True) -> None:
        if need_bom:
            sp.add_argument("bom", help="file danh sách linh kiện cần mua")
            sp.add_argument("-c", "--catalog", action="append",
                            help="file bảng giá CSV (lặp lại được)")
            sp.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                            help=f"ngưỡng khớp tên (mặc định {DEFAULT_MIN_SCORE})")
        sp.add_argument("-s", "--shops", help="file cấu hình shop (JSON)")

    sp = sub.add_parser("order", help="tìm phương án đặt hàng tốt nhất")
    common(sp)
    sp.add_argument("-p", "--penalty", type=int, default=None,
                    help="chi phí phiền cho mỗi đơn tách thêm (VND). "
                         "Càng cao càng gom về ít shop.")
    sp.add_argument("-m", "--max-shops", type=int, default=None,
                    help="ép đặt tối đa bao nhiêu shop")
    sp.add_argument("--urls", action="store_true", help="in kèm link sản phẩm")
    sp.add_argument("--csv", help="lưu kết quả ra file CSV")
    sp.add_argument("--md", help="lưu kết quả ra file Markdown")
    sp.set_defaults(func=cmd_order)

    sp = sub.add_parser("compare", help="so sánh: ít đơn hơn thì đắt thêm bao nhiêu")
    common(sp)
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("match", help="soi lại việc khớp tên linh kiện")
    common(sp)
    sp.add_argument("-n", "--limit", type=int, default=5,
                    help="số kết quả hiện mỗi dòng BOM")
    sp.set_defaults(func=cmd_match)

    sp = sub.add_parser("fetch", help="tra giá tự động từ website shop")
    sp.add_argument("bom", help="file danh sách linh kiện cần mua")
    sp.add_argument("-s", "--shops", help="file cấu hình shop (JSON)")
    sp.add_argument("-o", "--out", help="file CSV để ghi (mặc định data/catalog.csv)")
    sp.add_argument("-a", "--append", action="store_true",
                    help="nối thêm thay vì ghi đè")
    sp.add_argument("--delay", type=float, default=1.5,
                    help="giây nghỉ giữa hai lượt gọi cùng shop (mặc định 1.5)")
    sp.add_argument("--dry-run", action="store_true",
                    help="chỉ xem shop nào trả dữ liệu, không ghi file")
    sp.add_argument("--no-cache", action="store_true", help="bỏ qua cache")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("shops", help="liệt kê shop đang cấu hình")
    common(sp, need_bom=False)
    sp.set_defaults(func=cmd_shops)

    sp = sub.add_parser("web", help="mở giao diện trên trình duyệt")
    sp.add_argument("-p", "--port", type=int, default=8765)
    sp.add_argument("-s", "--shops", help="file cấu hình shop (JSON)")
    sp.add_argument("-c", "--catalog", help="file bảng giá CSV")
    sp.add_argument("--no-open", action="store_true",
                    help="không tự mở trình duyệt")
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser("init", help="tạo cấu hình và dữ liệu mẫu")
    sp.add_argument("-d", "--dir", help="thư mục dự án (mặc định: thư mục hiện tại)")
    sp.set_defaults(func=cmd_init)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
