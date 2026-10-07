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
