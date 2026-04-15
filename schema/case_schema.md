# Case Schema

Schema này áp dụng cho ba file `raw.md`, `summary.md`, `structured.md` của cùng một hồ sơ. `raw.md` là nguồn gốc chuẩn để các bước sau kế thừa metadata.

## Mục tiêu

- Chuẩn hóa metadata cho RAG và indexing.
- Cho phép nối chéo sang repo VBPL qua `laws_cited[].law_id`.
- Phân biệt rõ dữ liệu gốc, tóm tắt, và structured extraction.

## Quy ước file

- `raw.md`: bắt buộc có frontmatter đầy đủ; body có thể chỉ là placeholder nếu full text còn nằm ở PDF.
- `summary.md`: cùng `case_id`, có thể ở trạng thái draft.
- `structured.md`: cùng `case_id`, dùng section cố định `Facts / Issues / Reasoning / Judgment`.

## Frontmatter chuẩn

### Bắt buộc

| Field | Type | Mô tả |
| --- | --- | --- |
| `case_id` | string | ID ổn định trong repo, ví dụ `ta-2099429` |
| `source_case_id` | string | ID/slug từ nguồn công bố |
| `slug` | string | slug dùng cho thư mục hồ sơ |
| `title` | string | tiêu đề/caption của vụ việc |
| `document_kind` | string | `Bản án` hoặc `Quyết định` |
| `case_number` | string | số bản án/quyết định |
| `decision_date` | date or null | ngày ra bản án |
| `publication_date` | date or null | ngày công bố trên cổng |
| `year` | string | năm dùng để sắp xếp thư mục case |
| `case_type` | string | ví dụ `Hình sự`, `Dân sự`, `Hành chính` |
| `domain` | string | slug thư mục, ví dụ `hinh-su` |
| `proceeding_stage` | string or null | ví dụ `Sơ thẩm`, `Phúc thẩm` |
| `court` | string | tên tòa án xét xử |
| `source` | string | tên nguồn, ví dụ `congbobanan.toaan.gov.vn` |
| `source_url` | string | URL trang chi tiết |
| `status` | string | `raw`, `summary-draft`, `structured-draft`, ... |
| `visibility` | string | `metadata-only-public`, `restricted-source`, ... |
| `language` | string | mặc định `vi` |

### Khuyến nghị

| Field | Type | Mô tả |
| --- | --- | --- |
| `pdf_url` | string or null | link file PDF từ nguồn |
| `viewer_url` | string or null | link viewer/PDF.js nếu có |
| `court_level` | string or null | nếu có quy ước riêng |
| `summary_text` | string or null | mô tả ngắn từ detail page |
| `parties` | list[string] | đương sự/bị cáo/nguyên đơn nếu parse được |
| `keywords` | list[string] | từ khóa nội bộ |
| `source_restrictions` | string or null | ghi chú pháp lý ngắn |
| `related_case_ids` | list[string] | link nội bộ tới case khác nếu có |
| `notes` | string or null | ghi chú QA / curation |

### `laws_cited`

`laws_cited` là danh sách object. Dùng `law_id` làm khóa canonical để nối sang repo VBPL.

Quy ước đề xuất:

- `law_id`: VBPL `id` dạng chuỗi số, ví dụ `"91978"`.
- `label`: text nguyên trạng hoặc đã chuẩn hóa từ bản án.
- `article`: điều/khoản nếu parse được.
- `doc_num`: số hiệu văn bản nếu biết.
- `confidence`: `high|medium|low` cho bước map tự động.

Ví dụ:

```yaml
laws_cited:
  - label: "Bộ luật Dân sự 2015"
    law_id: "91978"
    article: "Điều 357"
    doc_num: "91/2015/QH13"
    confidence: "high"
  - label: "Điều 175 Luật sửa đổi, bổ sung một số điều của BLHS năm 2015"
    law_id: null
    article: "Điều 175"
    doc_num: null
    confidence: "low"
```

## YAML example

```yaml
---
case_id: "ta-2099429"
source_case_id: "2099429"
slug: "ban-an-88-ngay-06-04-2026-tand-tinh-dak-lak--2099429"
title: "Bị cáo Lê Thanh P phạm tội lạm dụng tín nhiệm chiếm đoạt tài sản"
document_kind: "Bản án"
case_number: "88"
decision_date: "2026-04-06"
publication_date: "2026-04-15"
year: "2026"
case_type: "Hình sự"
domain: "hinh-su"
proceeding_stage: "Phúc thẩm"
court: "TAND tỉnh Đắk Lắk"
court_level: null
source: "congbobanan.toaan.gov.vn"
source_url: "https://congbobanan.toaan.gov.vn/2ta2099429t1cvn/chi-tiet-ban-an"
pdf_url: "https://congbobanan.toaan.gov.vn/5ta2099429t1cvn/sample%20(1)%2015-04-2026%2001-52-08.pdf"
viewer_url: "https://congbobanan.toaan.gov.vn/Resources/pdfjs/web/viewer.html?file=%2F3ta2099429t1cvn/"
status: "raw"
visibility: "restricted-source"
language: "vi"
summary_text: "Chấp nhận kháng cáo của bị cáo và sửa bản án sơ thẩm về hình phạt."
keywords:
  - "lạm dụng tín nhiệm chiếm đoạt tài sản"
laws_cited:
  - label: "Điều 175 Luật sửa đổi, bổ sung một số điều của BLHS năm 2015"
    law_id: null
    article: "Điều 175"
    doc_num: null
    confidence: "low"
source_restrictions: "Redistribution should be reviewed before public publication."
related_case_ids: []
notes: null
---
```

## Body conventions

### `raw.md`

```md
# Raw Capture

## Source Summary
...

## Retrieval Notes
...
```

### `summary.md`

```md
# Summary

## Holding
...

## Why It Matters
...
```

### `structured.md`

```md
# Structured Extraction

## Facts
...

## Issues
...

## Reasoning
...

## Judgment
...
```

### `pdf.md`

- Sidecar riêng cho job PDF.
- Dùng để chứa markdown trích xuất từ `files/source.pdf`.
- Không bắt buộc trong MVP metadata-only, nhưng khuyến nghị tạo khi PDF text extract được.
