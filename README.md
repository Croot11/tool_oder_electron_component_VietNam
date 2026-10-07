# lkorder — BOM + link shop → giỏ hàng

Đưa vào **file BOM** và **link một shop linh kiện**, tool mở Chrome bằng tài
khoản của bạn, tìm từng linh kiện trên shop đó và **bỏ vào giỏ hàng**. Bạn chỉ
việc mở giỏ, xem lại và tự thanh toán.

> Tool **không bao giờ thanh toán**. Mọi nút có chữ "thanh toán", "mua ngay",
> "đặt hàng", "checkout"… đều bị chặn, không bấm.

## Cài đặt

Cần Python 3.10 trở lên. Phần bỏ giỏ cần thêm Playwright:

```
python -m pip install playwright
python -m playwright install chrome
```

hoặc cài cả gói: `pip install -e .[browser]` rồi `playwright install chrome`.

Không cài Playwright vẫn dùng được phần gom đơn / bảng giá (chỉ thư viện chuẩn).
`mo-giao-dien.cmd` tự kiểm tra Playwright và hỏi có muốn cài luôn không.

## Dùng

### Giao diện

Nhấn đúp `mo-giao-dien.cmd` (hoặc `python -m lkorder web`). Trình duyệt mở
http://127.0.0.1:8765, thẻ **Bỏ vào giỏ**:

1. **Chọn file BOM** — kéo thả `.csv`, `.txt`, `.tsv` (xuất từ Excel, KiCad,
   Altium đều được).
2. **Dán link shop** — vd `https://tenshop.vn`.
3. Bấm **Bắt đầu**. Một cửa sổ Chrome hiện ra và tool chạy từng dòng, có
   tiến độ và nút **Dừng**.

Kết quả chia ba nhóm:

| Nhóm | Nghĩa |
|---|---|
| **Đã thêm vào giỏ** | khớp chắc chắn, đã bấm thêm và thấy giỏ tăng |
| **Không thấy trên shop** | tìm không ra món nào đủ giống |
| **Cần kiểm tra bằng mắt** | có ứng viên nhưng chưa chắc, hoặc không xác nhận được giỏ đã tăng — tool **không** bỏ giỏ, tự xử lý tay |

Giao diện chỉ mở cho máy của bạn (127.0.0.1), không có đăng nhập, đừng mở ra
mạng ngoài.

### Dòng lệnh

```
lk cart bom.txt --shop https://tenshop.vn            # bỏ vào giỏ
lk cart bom.txt --shop https://tenshop.vn --dry-run  # chỉ tìm + khớp, không bấm
lk cart bom.txt --shop https://tenshop.vn --md kq.md # lưu báo cáo Markdown
```

Chưa cài gói thì dùng `lk.cmd cart …` hoặc `python -m lkorder cart …`.

Tuỳ chọn khác: `--platform` (haravan, shopify, sapo, woo — mặc định đoán từ
`data/shops.json`), `--delay-min/--delay-max` (giây nghỉ giữa hai dòng, mặc
định 2–5), `--login-timeout` (mặc định 300 giây), `--profile`, `--no-wait`.

**Nên chạy `--dry-run` trước** với shop mới để xem tool khớp có đúng không.

## Đăng nhập một lần

- Lần đầu, Chrome mở trang shop và tool chờ (tối đa 5 phút) cho bạn **tự đăng
  nhập trong cửa sổ đó**. Tool không hỏi, không đọc, không lưu mật khẩu.
- Cookie/phiên đăng nhập được lưu ở `data/browser_profile/`, nên các lần sau
  vào thẳng, không phải đăng nhập lại (trừ khi shop tự đăng xuất).
- Thư mục này chứa phiên đăng nhập của bạn: **không chia sẻ, không commit**
  (đã có trong `.gitignore`). Xoá thư mục để đăng xuất hết.
- Tool nhận biết "đã đăng nhập" bằng cách dò nút "Đăng xuất", "Tài khoản của
  tôi"… Shop nào giao diện lạ thì có thể nó không nhận ra — đăng nhập xong mà
  tool vẫn chờ thì cứ để nó hết giờ và báo lại.

## File BOM

Mỗi dòng một món, số lượng ghi `x10`, `10 x` hoặc cách bằng dấu phẩy; dòng bắt
đầu bằng `#` bị bỏ qua. Xem `data/bom_mau.txt`:

```
STM32F103C8T6 x2
Điện trở 10K 1/4W x100
Tụ gốm 100nF x50
```

File CSV/TSV thì tool tự nhận cột tên, số lượng, ký hiệu (designator), ghi chú,
mã thay thế.

Số lượng được quy theo cách shop bán: shop bán điện trở gói 100 con mà BOM cần
150 thì đặt 2 gói. Tool cũng tôn trọng số lượng tối thiểu của shop.

## Khớp tên linh kiện

Chỗ dễ sai nhất. Tool bỏ dấu tiếng Việt, lọc từ quảng cáo, tách kiểu chân ra
khỏi mã, quy đổi đơn vị (`0.1uF` = `100nF`, `1/4W` = `0.25W`, `4K7` = `4.7k`),
rồi chấm điểm từng kết quả tìm kiếm.

Nguyên tắc: **thà báo "chưa chắc" còn hơn bỏ nhầm hàng vào giỏ.** Sai giá trị
linh kiện thụ động (10K với 100K, 100nF với 10nF) là loại thẳng.

## Giới hạn đã biết

- **Mỗi lượt chỉ một shop.** Muốn chia BOM ra nhiều shop thì dùng phần gom đơn
  (bên dưới) để quyết định trước, rồi chạy `cart` cho từng shop.
- **Shop được hỗ trợ tốt nhất:** web chạy Haravan, Sapo, Shopify,
  WooCommerce. Shop khác tool vẫn thử (ô tìm kiếm, URL `/search?q=`) nhưng có
  thể không đọc được kết quả. **Chưa hỗ trợ sàn TMĐT** (Shopee, Lazada, Tiki…).
- **Chưa kiểm chứng trên mọi shop thật.** Giao diện mỗi shop một kiểu; nút
  "Thêm vào giỏ", ô số lượng, biểu tượng giỏ có thể không được nhận ra. Khi đó
  dòng đó rơi vào "Cần kiểm tra bằng mắt", không bị bỏ nhầm.
- **Biến thể sản phẩm** (chọn màu, kiểu chân, giá trị trong một trang) chưa
  được chọn tự động — tool bấm thêm với lựa chọn mặc định của trang, nên với
  các món này hãy xem lại trong giỏ.
- **Captcha, OTP, chống bot**: tool không vượt qua. Gặp thì tự xử lý trong cửa
  sổ Chrome hoặc chạy lại sau.
- **Không kiểm tra giỏ có sẵn.** Hàng cũ trong giỏ vẫn nằm đó; chạy hai lần là
  thêm hai lần. Nên dọn giỏ trước khi chạy.
- **Cần Chrome** cài trên máy (Playwright dùng Chrome thật, hiện cửa sổ, không
  chạy ngầm). Chỉ chạy một lượt cùng lúc.
- Tool nghỉ ngẫu nhiên 2–5 giây giữa hai dòng để không dồn dập lên shop; BOM
  vài chục dòng mất vài phút. Bạn vẫn nên tự xem điều khoản sử dụng của shop.

## Phần cũ: gom đơn nhiều shop

Trong giao diện là các thẻ **Gom đơn**, **Kiểm tra khớp**, **Shop**, **Bảng
giá**. Dựa trên bảng giá `data/catalog.csv` và cấu hình `data/shops.json`, tool
chọn tổ hợp shop sao cho

```
tổng = tiền hàng + tiền ship + (số đơn × chi phí phiền)
```

nhỏ nhất (Set Cover có trọng số, duyệt hết tổ hợp khi ≤ 16 shop).

```
lk order bom.txt                  # phương án tốt nhất
lk order bom.txt --penalty 50000  # ngại đơn lẻ hơn -> gom mạnh hơn
lk compare bom.txt                # ít đơn hơn thì đắt thêm bao nhiêu
lk match bom.txt                  # soi lại việc khớp tên
lk fetch bom.txt --dry-run        # thử tra giá tự động (haravan/woo/jsonld)
lk shops                          # danh sách shop
```

> ⚠ Giá trong `data/catalog.csv` là **giá bịa để chạy thử**. Thay bằng giá
> thật trước khi dùng để ra quyết định.
