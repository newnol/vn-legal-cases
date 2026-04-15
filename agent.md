# Agent Notes

## Mission

Xây repo `vn-legal-cases` theo hướng:

- metadata-first crawl;
- schema ổn định cho `raw/summary/structured`;
- scale dần sau khi xác minh thêm legal + kỹ thuật.

## Current Roadmap

1. Metadata-first crawler ổn định cho `home + detail`.
2. Frontier queue bền vững để tránh fetch trùng và hỗ trợ incremental runs.
3. Reverse-engineer listing/search của ASP.NET WebForms để mở rộng coverage.
4. Tách PDF ra job riêng, download song song và extract markdown.
5. Chỉ sau đó mới xét OCR, checksum/versioning, hoặc sync automation.

## Working Rules

- Public-by-default chỉ nên chứa metadata và source links.
- Không mặc định republish full raw text từ nguồn công bố chính thức.
- Dừng hoặc back off khi gặp `429` / `503`.
- Ưu tiên `requests + BeautifulSoup`; không nhảy sang Playwright nếu chưa cần.

## Implemented So Far

- Repo skeleton, schema, samples, README, NOTICE.
- MVP parser cho trang detail.
- Frontier queue bằng SQLite tại `.runtime/state/frontier.db`.
- `fetch_mvp.py` đã có seed, claim, retry, checkpoint cơ bản và `--workers`.
- `pdf_job.py` tải PDF riêng và sinh `pdf.md`.

## Near-Term Next Steps

1. Bổ sung audit/status command riêng cho frontier.
2. Thêm checksum/versioning để detect case updates.
3. Nghiên cứu pagination listing sâu hơn với cửa sổ ngày hẹp hoặc page selector postback.
