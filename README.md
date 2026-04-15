# vn-legal-cases

Repo corpus riêng cho bản án, quyết định và vụ việc của Việt Nam, tách biệt khỏi corpus văn bản quy phạm pháp luật. Mục tiêu của repo này là chuẩn hóa metadata, duy trì layout `raw/summary/structured`, và thử nghiệm pipeline crawl tối thiểu trước khi scale.

## Trạng thái hiện tại

- Repo này đang ở giai đoạn MVP.
- Nguồn mặc định cho spike/crawl là [Cổng Công bố bản án của TANDTC](https://congbobanan.toaan.gov.vn/).
- Hướng kỹ thuật hiện tại là `requests + BeautifulSoup`, chưa dùng Playwright ở MVP.
- Public publication nên mặc định nghiêng về `metadata + source_url + pdf_url`, không mặc định republish toàn văn.

## Phạm vi pháp lý và tái phân phối

Phần footer của cổng công bố bản án hiện nêu rằng việc đăng tải hoặc phát hành lại thông tin, dữ liệu từ trang cần có sự đồng ý bằng văn bản của Tòa án nhân dân tối cao. Vì vậy:

- Code, schema, sample giả lập và metadata do repo này tự tạo có thể public.
- Dữ liệu crawl từ nguồn công bố chính thức cần được xem là dữ liệu có hạn chế tái phân phối cho đến khi có xác nhận rõ hơn.
- Khuyến nghị vận hành public repo theo mặc định:
  - commit `meta.json`, `source_url`, `pdf_url`, ghi chú trích dẫn;
  - chỉ commit `raw.md` chứa toàn văn khi đã xác minh quyền tái phân phối;
  - nếu cần chia sẻ công khai sớm, dùng sample giả lập hoặc excerpt ngắn đã rà soát thủ công.

Chi tiết hơn nằm trong [NOTICE](NOTICE) và [docs/tech_spike.md](docs/tech_spike.md).

## Cấu trúc repo

```text
vn-legal-cases/
├── README.md
├── agent.md
├── memory.md
├── LICENSE
├── NOTICE
├── docs/
│   └── tech_spike.md
├── schema/
│   └── case_schema.md
├── samples/
│   └── sample_case/
│       ├── raw.md
│       ├── summary.md
│       └── structured.md
├── data/
│   ├── dan-su/
│   ├── hinh-su/
│   └── hanh-chinh/
└── scripts/
    ├── common.py
    ├── fetch_mvp.py
    ├── normalize_case.py
    └── requirements.txt
```

## Workflow MVP

1. Fetch seed URLs từ trang chủ của cổng công bố.
2. Tải trang chi tiết, lấy metadata và link PDF, rồi lưu `meta.json` + `raw.md`.
3. Chạy chuẩn hóa để dựng frontmatter thống nhất và tạo template `summary.md` / `structured.md`.
4. QA thủ công một số hồ sơ trước khi mở rộng phạm vi crawl.

## Frontier Queue

Crawler hiện dùng SQLite frontier tại `.runtime/state/frontier.db` để:

- lưu `detail_url` đã phát hiện;
- tránh fetch trùng;
- hỗ trợ retry/backoff;
- cho phép crawl incremental qua nhiều lần chạy.
- cho phép resume sau khi tiến trình bị dừng/hủy.

Hành vi resume hiện tại:

- item đã `fetched` sẽ không bị crawl lại ở lần chạy sau;
- item `failed` sẽ quay lại hàng đợi sau `--retry-delay-seconds`;
- item đang `fetching` nhưng bị kẹt do tiến trình bị hủy sẽ tự được thu hồi về `discovered` nếu đã quá `--fetching-stale-after-seconds` (mặc định `900` giây);
- nếu muốn reset ngay toàn bộ item `fetching`, dùng `--reset-fetching`.

Ví dụ:

```bash
python3 scripts/fetch_mvp.py --seed-home --limit 10 --dry-run --insecure
python3 scripts/fetch_mvp.py --queue-stats
python3 scripts/fetch_mvp.py --seed-listing --listing-date-from 15/04/2026 --listing-date-to 15/04/2026 --listing-domain hinh-su --listing-pages 1 --queue-stats --insecure
python3 scripts/fetch_mvp.py --reset-fetching --queue-stats
```

## Cài đặt

```bash
cd /Users/newnol/workspace/vn-legal-cases
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

## Chạy MVP fetch

Dry run:

```bash
python3 scripts/fetch_mvp.py --limit 5 --dry-run
```

Nếu `requests` báo lỗi xác thực chứng chỉ TLS với portal, có thể tạm dùng một trong hai cách sau trong lúc spike:

```bash
python3 scripts/fetch_mvp.py --limit 5 --dry-run --ca-bundle /path/to/cacert.pem
python3 scripts/fetch_mvp.py --limit 5 --dry-run --insecure
```

`--insecure` chỉ nên dùng cho kiểm tra kỹ thuật cục bộ, không nên là mặc định khi scale.

Nếu bạn thấy lỗi kiểu `SSLEOFError` hoặc `certificate verify failed`, chạy lại với `--insecure` gần như luôn là cách thử đầu tiên trên môi trường worker/Linux.

Nếu `requests` vẫn bị TLS EOF trên worker, crawler sẽ tự thử lại bằng `curl` khi có sẵn trên máy; nếu profile mặc định vẫn lỗi, nó sẽ thử thêm `--tlsv1.2` và `--tlsv1.2 --ciphers DEFAULT:@SECLEVEL=1` trước khi dừng.

Chạy với frontier + seed từ trang chủ:

```bash
python3 scripts/fetch_mvp.py --seed-home --limit 10 --out-dir ./tmp-cases --insecure
```

Chạy với listing discovery hẹp theo cửa sổ ngày:

```bash
python3 scripts/fetch_mvp.py \
  --seed-listing \
  --listing-date-from 15/04/2026 \
  --listing-date-to 15/04/2026 \
  --listing-domain hinh-su \
  --listing-document-kind ban-an \
  --listing-pages 1 \
  --queue-stats \
  --insecure
```

Khuyến nghị giữ `--listing-pages` nhỏ và dùng cửa sổ ngày hẹp, vì response listing có thể rất lớn khi query rộng.

Ghi ra thư mục chỉ định:

```bash
python3 scripts/fetch_mvp.py --limit 5 --out-dir ./tmp-cases
```

Chuẩn hóa dữ liệu vừa fetch:

```bash
python3 scripts/normalize_case.py --root ./tmp-cases
```

## Quy ước dữ liệu

- `raw.md`: capture gần nguồn nhất ở mức MVP; hiện có thể chỉ chứa metadata + tóm tắt HTML + link PDF.
- `summary.md`: tóm tắt do người hoặc model tạo sau.
- `structured.md`: facts / issues / reasoning / judgment ở dạng có cấu trúc.
- `laws_cited[].law_id`: quy ước dùng VBPL `id` làm khóa canonical khi nối với repo luật.

Chi tiết schema ở [schema/case_schema.md](schema/case_schema.md).

## Hướng kỹ thuật đã chốt trong spike

- Trang chủ và trang chi tiết trả HTML SSR ổn định qua GET.
- Trang chi tiết đã chứa metadata chính và link PDF tải trực tiếp.
- Trang listing tìm kiếm dùng ASP.NET WebForms với `__VIEWSTATE` và postback; chưa thấy JSON API công khai trong HTML tĩnh.
- Đã reverse-engineer được một POST search hợp lệ cho listing, parse được `List_group_pub`, `lbl_count_record_top`, `LbShowtotal`, và link detail của page kết quả.
- Crawler đã có frontier SQLite để incremental metadata crawl.
- Kết luận MVP: dùng `requests + BeautifulSoup`; chưa cần Playwright.

## Chưa làm ở MVP

- OCR/PDF-to-text diện rộng.
- Crawl listing có phân trang đầy đủ.
- Cron/CI sync tự động.
- Public release toàn văn từ nguồn công bố chính thức.
