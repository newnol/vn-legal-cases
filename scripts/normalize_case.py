from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from common import (
    DATA_DIR,
    build_case_id,
    clean_inline_whitespace,
    dedupe_preserve_order,
    domain_slug,
    derive_case_year,
    ensure_dirs,
    extract_keywords,
    extract_laws_cited,
    extract_source_case_id,
    read_json,
    write_text,
    yaml_scalar,
)


LOGGER = logging.getLogger("normalize_case")


SIMPLE_FIELD_ORDER = [
    "case_id",
    "source_case_id",
    "slug",
    "title",
    "document_kind",
    "case_number",
    "decision_date",
    "publication_date",
    "year",
    "case_type",
    "domain",
    "proceeding_stage",
    "court",
    "court_level",
    "source",
    "source_url",
    "pdf_url",
    "viewer_url",
    "status",
    "visibility",
    "language",
    "summary_text",
    "source_restrictions",
    "notes",
]


def append_scalar(lines: list[str], key: str, value: Any) -> None:
    lines.append(f"{key}: {yaml_scalar(value)}")


def append_list(lines: list[str], key: str, values: list[str]) -> None:
    if not values:
        lines.append(f"{key}: []")
        return
    lines.append(f"{key}:")
    for value in values:
        lines.append(f"  - {yaml_scalar(value)}")


def append_laws(lines: list[str], laws: list[dict[str, Any]]) -> None:
    if not laws:
        lines.append("laws_cited: []")
        return

    lines.append("laws_cited:")
    for law in laws:
        lines.append(f"  - label: {yaml_scalar(law.get('label'))}")
        lines.append(f"    law_id: {yaml_scalar(law.get('law_id'))}")
        lines.append(f"    article: {yaml_scalar(law.get('article'))}")
        lines.append(f"    doc_num: {yaml_scalar(law.get('doc_num'))}")
        lines.append(f"    confidence: {yaml_scalar(law.get('confidence'))}")


def build_frontmatter(record: dict[str, Any]) -> str:
    lines = ["---"]
    for key in SIMPLE_FIELD_ORDER:
        append_scalar(lines, key, record.get(key))
    append_list(lines, "keywords", record.get("keywords") or [])
    append_laws(lines, record.get("laws_cited") or [])
    append_list(lines, "related_case_ids", record.get("related_case_ids") or [])
    lines.append("---")
    return "\n".join(lines)


def normalize_record(meta: dict[str, Any]) -> dict[str, Any]:
    ids = meta.get("ids") or {}
    source = meta.get("source") or {}
    metadata = meta.get("metadata") or {}

    source_url = source.get("detail_url")
    source_case_id = str(ids.get("source_case_id") or extract_source_case_id(source_url or ""))
    summary_text = clean_inline_whitespace(metadata.get("summary_text") or "")
    title = clean_inline_whitespace(metadata.get("title") or "")
    case_type = metadata.get("case_type")
    year = metadata.get("year") or derive_case_year(
        decision_date=metadata.get("decision_date"),
        publication_date=metadata.get("publication_date"),
    )
    keywords = dedupe_preserve_order(
        (metadata.get("keywords") or []) + extract_keywords(title=title, case_type=case_type, summary_text=summary_text)
    )
    laws_cited = extract_laws_cited("\n".join(part for part in [title, summary_text] if part))
    related_case_ids = [
        build_case_id(extract_source_case_id(url))
        for url in metadata.get("related_detail_urls") or []
        if url
    ]

    return {
        "case_id": str(ids.get("case_id") or build_case_id(source_case_id)),
        "source_case_id": source_case_id,
        "slug": ids.get("case_slug"),
        "title": title or None,
        "document_kind": metadata.get("document_kind"),
        "case_number": metadata.get("case_number"),
        "decision_date": metadata.get("decision_date"),
        "publication_date": metadata.get("publication_date"),
        "year": year,
        "case_type": case_type,
        "domain": metadata.get("domain") or domain_slug(case_type),
        "proceeding_stage": metadata.get("proceeding_stage"),
        "court": metadata.get("court"),
        "court_level": None,
        "source": source.get("name"),
        "source_url": source_url,
        "pdf_url": source.get("pdf_url"),
        "viewer_url": source.get("viewer_url"),
        "status": "raw",
        "visibility": "restricted-source",
        "language": "vi",
        "summary_text": summary_text or None,
        "keywords": keywords,
        "laws_cited": laws_cited,
        "source_restrictions": source.get("redistribution_notice"),
        "related_case_ids": dedupe_preserve_order(related_case_ids),
        "notes": None,
    }


def build_raw_markdown(record: dict[str, Any]) -> str:
    parts = [
        build_frontmatter(record),
        "",
        "# Raw Capture",
        "",
        "## Source Summary",
        "",
        record.get("summary_text") or "MVP currently stores metadata and source links only.",
        "",
        "## Retrieval Notes",
        "",
        f"- Source detail page: {record.get('source_url')}",
        f"- Source PDF: {record.get('pdf_url') or 'Not exposed in this capture.'}",
        "- This MVP does not perform OCR or full PDF-to-text extraction.",
        "- Review source restrictions before publishing raw captures.",
        "",
    ]
    return "\n".join(parts)


def build_summary_markdown(record: dict[str, Any]) -> str:
    summary_record = dict(record)
    summary_record["status"] = "summary-draft"
    return "\n".join(
        [
            build_frontmatter(summary_record),
            "",
            "# Summary",
            "",
            "## Holding",
            "",
            "TODO: Add a concise holding after manual review or controlled generation.",
            "",
            "## Why It Matters",
            "",
            "TODO: Explain the practical significance, cited rules, and retrieval value.",
            "",
        ]
    )


def build_structured_markdown(record: dict[str, Any]) -> str:
    structured_record = dict(record)
    structured_record["status"] = "structured-draft"
    return "\n".join(
        [
            build_frontmatter(structured_record),
            "",
            "# Structured Extraction",
            "",
            "## Facts",
            "",
            "TODO",
            "",
            "## Issues",
            "",
            "TODO",
            "",
            "## Reasoning",
            "",
            "TODO",
            "",
            "## Judgment",
            "",
            "TODO",
            "",
        ]
    )


def discover_case_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.rglob("meta.json"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize fetched case metadata into repo markdown files.")
    parser.add_argument("--root", default=str(DATA_DIR), help="Root directory to scan for case folders.")
    parser.add_argument("--case-dir", action="append", default=[], help="Specific case folder(s) to normalize.")
    parser.add_argument("--force", action="store_true", help="Overwrite summary.md and structured.md if they exist.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    root = Path(args.root).expanduser().resolve()
    ensure_dirs(root)

    if args.case_dir:
        case_dirs = [Path(path).expanduser().resolve() for path in args.case_dir]
    else:
        case_dirs = discover_case_dirs(root)

    if not case_dirs:
        LOGGER.warning("No case directories with meta.json were found under %s", root)
        return 0

    for case_dir in case_dirs:
        meta_path = case_dir / "meta.json"
        if not meta_path.exists():
            LOGGER.warning("Skipping %s because meta.json is missing", case_dir)
            continue

        meta = read_json(meta_path)
        record = normalize_record(meta)
        write_text(case_dir / "raw.md", build_raw_markdown(record))

        summary_path = case_dir / "summary.md"
        if args.force or not summary_path.exists():
            write_text(summary_path, build_summary_markdown(record))

        structured_path = case_dir / "structured.md"
        if args.force or not structured_path.exists():
            write_text(structured_path, build_structured_markdown(record))

        LOGGER.info("Normalized %s", case_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
