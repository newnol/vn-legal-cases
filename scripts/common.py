from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

from requests.utils import requote_uri


REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_root(env_name: str, default_root: Path) -> Path:
    configured = os.environ.get(env_name)
    if not configured:
        return default_root
    return Path(configured).expanduser().resolve()


CASES_ROOT = _resolve_root("VN_LEGAL_CASES_ROOT", REPO_ROOT)
DATA_DIR = CASES_ROOT / "data"
RUNTIME_DIR = CASES_ROOT / ".runtime"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_DIR = RUNTIME_DIR / "state"
FRONTIER_DB_PATH = STATE_DIR / "frontier.db"

BASE_URL = "https://congbobanan.toaan.gov.vn"
HOME_URL = f"{BASE_URL}/"
LISTING_URL = f"{BASE_URL}/0tat1cvn/ban-an-quyet-dinh"
DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 60
USER_AGENT = "vn-legal-cases-mvp/0.1 (+metadata-only-public-by-default)"

DOMAIN_BY_CASE_TYPE = {
    "Hình sự": "hinh-su",
    "Dân sự": "dan-su",
    "Hành chính": "hanh-chinh",
    "Kinh doanh thương mại": "kinh-doanh-thuong-mai",
    "Lao động": "lao-dong",
    "Hôn nhân và gia đình": "hon-nhan-gia-dinh",
    "Quyết định áp dụng biện pháp xử lý hành chính": "quyet-dinh-ap-dung-bien-phap-xu-ly-hanh-chinh",
}

LAW_ID_HINTS = {
    "Bộ luật Dân sự 2015": "91978",
}


def ensure_dirs(output_root: Path | None = None) -> None:
    target_root = output_root or DATA_DIR
    target_root.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def clean_inline_whitespace(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\r", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def clean_multiline_text(text: str) -> str:
    lines: list[str] = []
    blank_pending = False

    for raw_line in text.splitlines():
        line = clean_inline_whitespace(raw_line)
        if not line:
            if lines:
                blank_pending = True
            continue
        if blank_pending:
            lines.append("")
            blank_pending = False
        lines.append(line)

    return "\n".join(lines).strip()


def slugify(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "case"


def normalize_portal_date(value: str | None) -> str | None:
    if not value:
        return None

    candidate = clean_inline_whitespace(value).replace(".", "/")
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def derive_case_year(*, decision_date: str | None, publication_date: str | None) -> str:
    for value in (decision_date, publication_date):
        if value and len(value) >= 4 and value[:4].isdigit():
            return value[:4]
    return "unknown-year"


def today_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def absolute_url(url_or_path: str | None) -> str | None:
    if not url_or_path:
        return None
    return requote_uri(urljoin(BASE_URL, url_or_path))


def domain_slug(case_type: str | None) -> str:
    if not case_type:
        return "khac"
    cleaned = clean_inline_whitespace(case_type)
    return DOMAIN_BY_CASE_TYPE.get(cleaned, slugify(cleaned))


def case_output_dir(root: Path, *, year: str, domain: str, case_slug: str) -> Path:
    return root / year / domain / case_slug


def extract_source_case_id(source_url: str) -> str:
    match = re.search(r"/2ta(\d+)t1cvn/chi-tiet-ban-an", source_url)
    if match:
        return match.group(1)
    return slugify(source_url)


def build_case_id(source_case_id: str) -> str:
    return f"ta-{source_case_id}"


def build_case_slug(*, source_case_id: str, document_kind: str | None, case_number: str | None, decision_date: str | None, court: str | None) -> str:
    parts = [
        document_kind or "case",
        case_number or source_case_id,
        decision_date or "",
        court or "",
    ]
    base = slugify(" ".join(part for part in parts if part))
    return f"{base}--{source_case_id}" if source_case_id not in base else base


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def extract_keywords(*, title: str | None, case_type: str | None, summary_text: str | None) -> list[str]:
    candidates: list[str] = []
    if case_type:
        candidates.append(clean_inline_whitespace(case_type))
    if title and " - " in title:
        candidates.extend(clean_inline_whitespace(part) for part in title.split(" - "))
    if summary_text:
        for match in re.findall(r"tội “([^”]+)”", summary_text, flags=re.IGNORECASE):
            candidates.append(clean_inline_whitespace(match))

    cleaned = [candidate for candidate in candidates if candidate]
    return dedupe_preserve_order(cleaned)[:8]


def extract_laws_cited(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []

    patterns = [
        r"(Điều\s+\d+[^\n.;]*(?:BLHS|Bộ luật Hình sự|Bộ luật Dân sự|Bộ luật Tố tụng hình sự|Luật sửa đổi, bổ sung một số điều của BLHS năm 2015))",
        r"(Bộ luật Dân sự\s+2015)",
        r"(Bộ luật Tố tụng hình sự)",
        r"(Bộ luật Hình sự)",
    ]

    extracted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in patterns:
        for raw_match in re.findall(pattern, text, flags=re.IGNORECASE):
            label = clean_inline_whitespace(raw_match)
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)

            article_match = re.search(r"(Điều\s+\d+)", label, flags=re.IGNORECASE)
            extracted.append(
                {
                    "label": label,
                    "law_id": LAW_ID_HINTS.get(label),
                    "article": article_match.group(1) if article_match else None,
                    "doc_num": None,
                    "confidence": "low" if LAW_ID_HINTS.get(label) is None else "high",
                }
            )

    return extracted
