"""Dữ liệu khởi tạo cho `lk init`.

QUAN TRỌNG: giá trong catalog mẫu là GIÁ BỊA để chạy thử cho ra bảng đẹp,
không phải giá thật của shop nào cả. Phí ship và thời gian giao cũng là ước
lượng. Trước khi đặt hàng thật, phải thay bằng dữ liệu thật — xem README,
mục "Lấy dữ liệu thật".
"""

from __future__ import annotations

import json
from pathlib import Path

# Các shop linh kiện VN hay được dùng. `platform` cho biết nền tảng web mà
# shop chạy, dùng khi lấy giá tự động; đặt "csv" nghĩa là chỉ đọc từ file.
SHOPS = [
    {
        "id": "thegioiic", "name": "Thế Giới IC", "url": "https://www.thegioiic.com",
        "platform": "csv", "shipping_fee": 30000, "free_ship_threshold": 0,
        "order_penalty": 20000, "prep_days": 2,
        "note": "Nhiều IC, linh kiện rời. Kiểm tra lại phí ship thực tế.",
    },
    {
        "id": "nshop", "name": "Nshop", "url": "https://nshopvn.com",
        "platform": "csv", "shipping_fee": 25000, "free_ship_threshold": 0,
        "order_penalty": 20000, "prep_days": 2,
        "note": "Mạnh về module, cảm biến, Arduino.",
    },
    {
        "id": "hshop", "name": "Hshop", "url": "https://hshop.vn",
        "platform": "csv", "shipping_fee": 30000, "free_ship_threshold": 0,
        "order_penalty": 20000, "prep_days": 2,
        "note": "Module, cảm biến, board phát triển.",
    },
    {
        "id": "lkthuduc", "name": "Linh Kiện Thủ Đức",
        "url": "https://linhkienthuduc.com", "platform": "csv",
        "shipping_fee": 30000, "free_ship_threshold": 0,
        "order_penalty": 20000, "prep_days": 3,
        "note": "Linh kiện rời, giá tốt cho hàng thụ động.",
    },
    {
        "id": "icdayroi", "name": "IC Đây Rồi", "url": "https://icdayroi.com",
        "platform": "csv", "shipping_fee": 30000, "free_ship_threshold": 0,
        "order_penalty": 20000, "prep_days": 3,
    },
    {
        "id": "banlinhkien", "name": "Bán Linh Kiện",
        "url": "https://banlinhkien.com", "platform": "csv",
        "shipping_fee": 30000, "free_ship_threshold": 0,
        "order_penalty": 20000, "prep_days": 3, "note": "Ở Hà Nội.",
    },
]

# Bảng giá VÍ DỤ. Không phải giá thật.
CATALOG = """\
shop,ten,gia,sku,ton,moq,pack,bac_gia,url
thegioiic,IC vi điều khiển STM32F103C8T6 LQFP-48,46000,STM32F103C8T6,50,1,,10:43000,
thegioiic,IC NE555P DIP-8,4500,NE555P,200,1,,10:3800|100:3200,
thegioiic,IC LM358 DIP-8,3500,LM358,150,1,,,
thegioiic,Điện trở 10K 1/4W 5% (100 con),18000,R10K-100,80,1,,,
thegioiic,Tụ gốm 100nF 50V,500,C104,999,10,,100:400,
thegioiic,Thạch anh 8MHz HC-49S,3000,X8M,120,1,,,
thegioiic,LED 5mm đỏ,700,LED5R,999,10,,,
nshop,Vi điều khiển STM32F103C8T6,49000,MCU-STM32,30,1,,,
nshop,Module hạ áp LM2596 3A,22000,LM2596-MOD,100,1,,5:20000,
nshop,Module cảm biến nhiệt độ DHT22,89000,DHT22,40,1,,,
nshop,Module RF NRF24L01+,32000,NRF24,60,1,,,
nshop,IC NE555 DIP-8,5000,NE555,80,1,,,
nshop,Thạch anh 8MHz,3500,XTAL8,90,1,,,
hshop,Mạch giảm áp LM2596 có hiển thị,35000,LM2596-LED,50,1,,,
hshop,Cảm biến DHT22 AM2302,95000,AM2302,25,1,,,
hshop,Module NRF24L01 PA LNA,68000,NRF24-PA,20,1,,,
hshop,Vi điều khiển STM32F103C8T6 LQFP48,52000,STM32-48,15,1,,,
hshop,Điện trở 10K 1/4W (gói 100),22000,R10K,60,1,,,
lkthuduc,Điện trở 10K 0.25W 5%,120,R10K-1,9999,50,,100:100|1000:80,
lkthuduc,Tụ gốm 0.1uF 50V,350,C104-1,9999,50,,100:300,
lkthuduc,Tụ hóa 470uF 25V,1500,C470U,500,10,,,
lkthuduc,IC NE555 DIP,3800,NE555-TD,300,10,,100:3300,
lkthuduc,LED 5mm đỏ siêu sáng,500,LED5,9999,10,,100:400,
lkthuduc,Thạch anh 8MHz,2500,X8,400,1,,,
icdayroi,IC STM32F103C8T6,47000,STM32,20,1,,,
icdayroi,IC LM358N DIP8,3000,LM358N,200,1,,,
icdayroi,Module LM2596 hạ áp,24000,LM2596,70,1,,,
banlinhkien,Điện trở 10K 1/4W,100,R10K-BLK,9999,100,,,
banlinhkien,Tụ gốm 104 50V,300,C104-BLK,9999,100,,,
banlinhkien,IC NE555,4200,NE555-BLK,150,1,,,
banlinhkien,LED 5mm đỏ,450,LED-BLK,9999,50,,,
"""

BOM = """\
# Danh sách linh kiện cần mua.
# Mỗi dòng một món. Số lượng ghi kiểu "x10", "10 x", hoặc cách bằng dấu phẩy.
# Dòng bắt đầu bằng # bị bỏ qua.

STM32F103C8T6 x2
NE555 x5
LM358 x3
Điện trở 10K 1/4W x100
Tụ gốm 100nF x50
Thạch anh 8MHz x2
LED 5mm đỏ x20
LM2596 x3
DHT22 x1
NRF24L01 x2
"""

README = """\
# lkorder — gom đơn linh kiện

Tìm linh kiện ở các shop VN rồi **gom về ít đơn nhất có thể**, thay vì mỗi
con một shop.

## Chạy

**Có giao diện** — nhấn đúp vào `mo-giao-dien.cmd`, hoặc:

```
python -m lkorder web
```

Trình duyệt tự mở http://127.0.0.1:8765. Bốn thẻ:

| Thẻ | Làm gì |
|---|---|
| **Đặt hàng** | dán danh sách cần mua, kéo thanh trượt "ngại đặt nhiều đơn", xem kết quả chia đơn kèm nút copy từng đơn |
| **Kiểm tra khớp** | xem tool hiểu tên linh kiện của bạn thành gì — xem trước khi đặt tiền thật |
| **Shop** | sửa phí ship, mức freeship, phí phiền, bật/tắt từng shop |
| **Bảng giá** | dán/sửa CSV giá ngay trên trình duyệt |

Giao diện chỉ mở cho máy của bạn (127.0.0.1), không có đăng nhập nên đừng
mở ra mạng ngoài.

### Hoặc dùng dòng lệnh

```
python -m lkorder order data/bom_mau.txt      # phương án tốt nhất
python -m lkorder compare data/bom_mau.txt    # ít đơn hơn thì đắt thêm bao nhiêu
python -m lkorder match data/bom_mau.txt      # soi lại việc khớp tên
python -m lkorder fetch data/bom_mau.txt      # tra giá tự động từ web shop
python -m lkorder shops                       # danh sách shop
```

Vài tuỳ chọn hay dùng:

```
lk order bom.txt --penalty 50000   # ngại đơn lẻ hơn -> gom mạnh hơn
lk order bom.txt --max-shops 2     # ép đúng 2 đơn, xem giá thế nào
lk order bom.txt --md don.md       # xuất ra file để copy đi đặt
```

Không cần cài gì thêm — chỉ dùng thư viện chuẩn của Python.

## Ý tưởng

Đặt 5 shop cho 10 con linh kiện thì tiền ship và công chờ hàng thường vượt xa
khoản chênh giá tiết kiệm được. Tool quy chuyện đó thành một con số duy nhất:

```
tổng = tiền hàng + tiền ship + (số đơn × chi phí phiền)
```

`chi phí phiền` là mức bạn sẵn sàng trả để bớt được một đơn. Chỉnh bằng
`--penalty`; mặc định 20.000đ/đơn.

Bài toán này là Set Cover có trọng số. Vì số shop nhỏ (dưới 16), tool **duyệt
hết mọi tổ hợp shop** thay vì đoán mò.

Chất lượng lời giải, nói cho đúng:

| Tình huống | Kết quả |
|---|---|
| Không shop nào có mức freeship, ≤ 16 shop | tối ưu tuyệt đối |
| Có mức freeship | gần tối ưu — đối chiếu vét cạn trên 1598 bài ngẫu nhiên: 1597 bài trùng khớp, 1 bài đắt hơn 0,97% |
| Trên 16 shop | heuristic (tham lam + tìm kiếm cục bộ) |

Mức freeship làm các linh kiện phụ thuộc lẫn nhau — dồn thêm hàng vào một shop
có thể được miễn ship — nên phần này phải dùng tìm kiếm cục bộ.

## Dữ liệu

- `data/shops.json` — phí ship, mức freeship, chi phí phiền của từng shop.
  **Sửa cho khớp thực tế của bạn.**
- `data/catalog.csv` — bảng giá. Cột bắt buộc: `shop`, `ten`, `gia`.
  Cột thêm: `sku`, `ton`, `moq`, `pack`, `bac_gia`, `url`.

  | Cột | Nghĩa |
  |---|---|
  | `gia` | giá của **một đơn vị bán** — một gói, nếu shop bán theo gói |
  | `pack` | số linh kiện trong một đơn vị bán. "Điện trở 10K (100 con)" → `100` |
  | `moq` | số đơn vị bán tối thiểu |
  | `bac_gia` | giá theo số lượng, dạng `10:4000` + `100:3500` ngăn bởi dấu `|` |

  Bỏ trống `pack` thì tool tự đoán từ tên sản phẩm ("gói 100", "50 con"…).
  Nó chỉ đoán khi thấy từ chỉ đơn vị đếm, nên `50V` hay `5mm` không bị hiểu
  nhầm — nhưng vẫn nên kiểm tra bằng `match`.

> ⚠ **Giá trong `data/catalog.csv` là giá bịa để chạy thử.** Phải thay bằng
> giá thật trước khi dùng để đặt hàng.

## Lấy dữ liệu thật

Ba cách, từ ít việc tới nhiều việc:

1. **Chép tay** giá của những con hay mua vào `catalog.csv`. Với một BOM vài
   chục món thì đây vẫn là cách nhanh và chắc nhất.
2. **Xuất từ giỏ hàng**: bỏ hàng vào giỏ ở từng shop rồi chép giá sang CSV.
3. **Lấy tự động** bằng `lk fetch`. Sửa `platform` của shop trong
   `shops.json` từ `"csv"` sang một trong:

   | platform | Dùng khi | Cách lấy |
   |---|---|---|
   | `haravan` | shop chạy Haravan / Sapo / Shopify | `/search?q=…&view=json` |
   | `woo` | shop chạy WooCommerce | Store API `/wp-json/wc/store/v1/products` |
   | `jsonld` | site khác, nhưng có nhúng Schema.org | đọc `<script type="application/ld+json">` |

   Rồi chạy `lk fetch bom.txt --dry-run` để xem shop nào thực sự trả dữ liệu,
   trước khi ghi đè `catalog.csv`.

   > ⚠ Ba cách trên là khuôn chung của từng nền tảng, **chưa được kiểm chứng
   > với website thật của các shop trong danh sách mẫu**. Shop hoàn toàn có
   > thể tắt endpoint, đổi đường dẫn, hoặc chặn truy cập tự động. Nếu `fetch`
   > không ra gì thì đó là chuyện bình thường — quay lại cách 1.

   Tool tự tôn trọng `robots.txt`, nghỉ 1,5 giây giữa hai lượt gọi cùng một
   shop, và lưu cache một ngày để chạy lại không nện thêm lượt nào. Bạn vẫn
   nên tự xem điều khoản sử dụng của shop trước khi dùng.

## Khớp tên linh kiện

Chỗ dễ sai nhất. `"Vi điều khiển STM32F103C8T6 LQFP-48 chính hãng"` và
`"IC MCU STM32F103C8T6 (SMD)"` phải được hiểu là cùng một con, nếu không tool
sẽ tưởng phải đặt hai shop.

Tool xử lý: bỏ dấu tiếng Việt, lọc từ quảng cáo, tách kiểu chân ra khỏi mã, và
quy đổi đơn vị nên `0.1uF` = `100nF`, `1/4W` = `0.25W`, `4K7` = `4.7k`.

**Luôn chạy `match` trước khi đặt** để xem tool hiểu đúng ý bạn chưa.
"""


def write_seed_files(root: Path) -> list[Path]:
    """Tạo file cấu hình + dữ liệu mẫu. Không ghi đè file đã có."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    targets = [
        (data / "shops.json", json.dumps({"shops": SHOPS}, ensure_ascii=False,
                                         indent=2) + "\n"),
        (data / "catalog.csv", CATALOG),
        (data / "bom_mau.txt", BOM),
        (root / "README.md", README),
    ]
    for path, content in targets:
        if path.exists():
            continue
        path.write_text(content, encoding="utf-8")
        created.append(path.relative_to(root) if path.is_relative_to(root) else path)
    return created
