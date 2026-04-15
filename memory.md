# Memory

## Snapshot

- Date: `2026-04-15`
- Repo state: initial scaffold completed, no git repo initialized yet.
- Default source under spike: `https://congbobanan.toaan.gov.vn/`

## Key Findings

- Trang chủ và trang chi tiết là SSR, đủ để crawl metadata bằng `requests`.
- Trang listing/search là ASP.NET WebForms; kết quả không nằm sẵn trong HTML ban đầu.
- Footer portal hiện nêu rằng đăng tải/phát hành lại dữ liệu cần sự đồng ý bằng văn bản của TANDTC.
- `requests` trên máy local có thể gặp lỗi TLS verify với portal; tạm hỗ trợ `--ca-bundle` và `--insecure`.

## Decisions

- Public strategy mặc định: metadata + source URL + PDF URL, không mặc định public raw full text.
- Crawl strategy mặc định: metadata-first, incremental, dedupe bằng frontier queue.
- Queue backend: SQLite file `.runtime/state/frontier.db`.
- Resume strategy: tự reclaim item `fetching` bị stale; vẫn có `--reset-fetching` để ép thu hồi thủ công.

## Latest Verification

- `python3 -m compileall scripts`: passed.
- Smoke test with frontier queue to `/tmp`:
  - fetched `1` case successfully;
  - frontier grew to `39` URLs total;
  - status snapshot after run: `fetched=1`, `discovered=38`.
- `normalize_case.py` produced `meta.json`, `raw.md`, `summary.md`, `structured.md` as expected for the smoke case.
- Listing discovery verification:
  - reverse-engineered POST search on `ban-an-quyet-dinh`;
  - page 1 listing seed worked with `--seed-listing`;
  - exact test run seeded `20` detail URLs into frontier;
  - listing result pages can be large because the page selector renders all page options in HTML.

## Commands To Remember

```bash
python3 scripts/fetch_mvp.py --seed-home --limit 10 --dry-run --insecure
python3 scripts/fetch_mvp.py --seed-home --limit 10 --out-dir /tmp/vn-legal-cases-smoke --insecure
python3 scripts/fetch_mvp.py --queue-stats
python3 scripts/normalize_case.py --root /tmp/vn-legal-cases-smoke
```

## Open Questions

1. Có endpoint/postback nào cho listing cho phép discovery coverage tốt hơn mà không cần browser automation?
2. Có nên lưu metadata crawl trung gian vào repo hay chỉ để trong storage/runtime?
3. Khi nào cần phase tải PDF riêng, và phase đó có được commit public hay chỉ private?
