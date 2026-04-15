# Tech Spike: `congbobanan.toaan.gov.vn`

Checked on: `2026-04-15`

## Questions

1. Trang có trả HTML SSR hay phụ thuộc JS?
2. Có JSON/API public rõ ràng cho listing hay không?
3. MVP nên dùng `requests + BeautifulSoup` hay Playwright?

## Findings

### 1. Trang chủ

- URL: `https://congbobanan.toaan.gov.vn/`
- Kết quả: trả HTML đầy đủ qua GET.
- Trong HTML có sẵn các link detail dạng:
  - `/2ta2099429t1cvn/chi-tiet-ban-an`
  - `/2ta2099428t1cvn/chi-tiet-ban-an`
- Tại thời điểm spike, trang chủ lộ 24 link detail từ các block nổi bật.

### 2. Trang chi tiết

- Ví dụ: `https://congbobanan.toaan.gov.vn/2ta2099429t1cvn/chi-tiet-ban-an`
- Kết quả: trả HTML đầy đủ qua GET.
- Metadata chính xuất hiện trực tiếp trong HTML:
  - số bản án / ngày;
  - tên bản án;
  - cấp xét xử;
  - loại án;
  - tòa án xét xử;
  - mô tả vụ án;
  - link PDF tải trực tiếp;
  - block "bản án cùng tội danh".
- Kết luận: trang detail crawl tốt bằng `requests + BeautifulSoup`.

### 3. Trang listing tìm kiếm

- URL: `https://congbobanan.toaan.gov.vn/0tat1cvn/ban-an-quyet-dinh`
- HTML đầu vào chứa form ASP.NET WebForms với:
  - `__VIEWSTATE`
  - `__EVENTTARGET`
  - `__EVENTARGUMENT`
  - các control lọc `Drop_Levels`, `Ra_Drop_Courts`, `Drop_CASES_STYLES_SEARCH`, ...
- `div#List_group_pub` trong HTML ban đầu để trống.
- Không thấy JSON endpoint công khai ngay trong HTML tĩnh.
- Đã xác minh được một POST search hợp lệ bằng form submit thường:
  - submit `cmd_search_banner`;
  - set các field như `Drop_STATUS_JUDGMENT_SEARCH_top`, `Drop_CASES_STYLES_SEARCH_top`, `Rad_DATE_FROM_top`, `Rad_DATE_TO_top`;
  - response trả về HTML đầy đủ với `List_group_pub`, `lbl_count_record_top`, `LbShowtotal`.
- Điều này cho thấy listing/search có thể khai thác bằng `requests`, chưa có dấu hiệu bắt buộc phải dùng browser automation.

### 3.1 Ghi chú hiệu năng

- Listing response có thể rất lớn vì dropdown `DropPages` render toàn bộ số trang vào HTML.
- Với truy vấn rộng, response có thể vượt hơn 1 MB và pagination trở nên chậm.
- Chiến lược thực tế hơn là:
  - dùng cửa sổ ngày hẹp;
  - giới hạn page nhỏ;
  - dùng listing để discovery, không dùng như nguồn chính cho crawl sâu toàn kho trong một lần chạy.

### 4. Robots / footer / vận hành

- `https://congbobanan.toaan.gov.vn/robots.txt` trả `404`.
- Trong môi trường local hiện tại, `requests` có thể gặp lỗi xác thực chứng chỉ TLS nếu trust store không đủ chuỗi CA; script MVP vì vậy hỗ trợ `--ca-bundle` và `--insecure` cho bước spike.
- Footer trang chủ ghi notice bản quyền và yêu cầu đồng ý bằng văn bản nếu đăng tải/phát hành lại dữ liệu từ cổng.
- Vì vậy, spike kỹ thuật phải đi cùng kiểm tra pháp lý trước khi scale.

## Decision

### Chọn cho MVP

`requests + BeautifulSoup`

Lý do:

- trang chủ và trang detail là SSR;
- metadata quan trọng đã có trong HTML;
- link PDF tải trực tiếp đã lộ;
- chưa có bằng chứng cần render JS bằng trình duyệt thật.

### Chưa làm ở MVP

- reverse-engineer đầy đủ postback của listing search;
- crawl phân trang diện rộng;
- Playwright-based automation.

## Operational guardrails

- User-Agent rõ ràng.
- Delay mặc định giữa request.
- Dừng hoặc fail-fast nếu gặp `429` / `503`.
- Không chạy song song mạnh.
- QA thủ công 3-5 hồ sơ trước khi tăng quy mô.
