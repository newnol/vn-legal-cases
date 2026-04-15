from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import urllib3

from common import (
    DATA_DIR,
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    clean_multiline_text,
    derive_case_year,
    ensure_dirs,
    read_json,
    write_text,
    yaml_scalar,
)
from normalize_case import normalize_record


LOGGER = logging.getLogger("pdf_job")


@dataclass(slots=True)
class PdfWorkItem:
    case_dir: Path
    meta_path: Path
    record: dict[str, Any]


@dataclass(slots=True)
class PdfResult:
    case_dir: Path
    pdf_path: Path | None
    pdf_md_path: Path | None
    status: str
    message: str | None = None


class PortalBinaryClient:
    def __init__(self, *, timeout_seconds: float, verify: bool | str) -> None:
        self.timeout_seconds = timeout_seconds
        self.verify = verify
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "vi,en;q=0.8",
                "Accept": "application/pdf,*/*;q=0.9",
            }
        )
        self._cookie_jar_path = Path(tempfile.gettempdir()) / f"vn-legal-cases-pdf-{os.getpid()}-{uuid.uuid4().hex}.txt"

    def _request_with_requests(self, url: str, *, referer: str | None, output_path: Path) -> tuple[int, str]:
        headers = {}
        if referer:
            headers["Referer"] = referer

        with self.session.get(
            url,
            timeout=self.timeout_seconds,
            verify=self.verify,
            stream=True,
            headers=headers or None,
        ) as response:
            if response.status_code in {429, 503}:
                raise RuntimeError(f"Portal returned {response.status_code}; stopping to avoid overload.")
            response.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        handle.write(chunk)
        return response.status_code, response.url

    def _request_with_curl(self, url: str, *, referer: str | None, output_path: Path) -> tuple[int, str]:
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("curl is not available in this environment.")

        base_command = [
            curl,
            "--silent",
            "--show-error",
            "--location",
            "--compressed",
            "--http1.1",
            "--user-agent",
            USER_AGENT,
            "--header",
            "Accept-Language: vi,en;q=0.8",
            "--header",
            "Accept: application/pdf,*/*;q=0.9",
            "--cookie",
            str(self._cookie_jar_path),
            "--cookie-jar",
            str(self._cookie_jar_path),
            "--output",
            str(output_path),
            "--write-out",
            "%{http_code}\\n%{url_effective}\\n",
            "--max-time",
            str(int(self.timeout_seconds)),
        ]
        if referer:
            base_command.extend(["--referer", referer])

        if self.verify is False:
            base_command.append("--insecure")
        elif isinstance(self.verify, str):
            base_command.extend(["--cacert", self.verify])

        profiles: list[tuple[str, list[str]]] = [
            ("default", []),
            ("tlsv1.2", ["--tlsv1.2"]),
            ("tlsv1.2+lowsec", ["--tlsv1.2", "--ciphers", "DEFAULT:@SECLEVEL=1"]),
        ]
        errors: list[str] = []

        for profile_name, extra_flags in profiles:
            result = subprocess.run(
                base_command + extra_flags + [url],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                http_code = 200
                effective_url = url
                if len(lines) >= 2 and lines[-2].isdigit():
                    http_code = int(lines[-2])
                    effective_url = lines[-1]
                if http_code >= 400:
                    raise RuntimeError(f"curl returned HTTP {http_code} for {url}")
                if profile_name != "default":
                    LOGGER.warning("curl succeeded for %s using TLS profile %s", url, profile_name)
                return http_code, effective_url

            stderr = (result.stderr or result.stdout or "").strip()
            errors.append(f"{profile_name}: {stderr or f'exit code {result.returncode}'}")
            LOGGER.debug("curl profile %s failed for %s: %s", profile_name, url, stderr)

        raise RuntimeError("curl download failed after trying TLS profiles: " + " | ".join(errors))

    def download(self, url: str, *, referer: str | None, output_path: Path) -> tuple[int, str]:
        try:
            return self._request_with_requests(url, referer=referer, output_path=output_path)
        except requests.exceptions.SSLError:
            if not shutil.which("curl"):
                raise
            LOGGER.warning("Switching to curl transport after TLS failure on %s", url)
            return self._request_with_curl(url, referer=referer, output_path=output_path)


def discover_case_items(root: Path, years: set[str] | None) -> list[PdfWorkItem]:
    items: list[PdfWorkItem] = []
    for meta_path in root.rglob("meta.json"):
        case_dir = meta_path.parent
        meta = read_json(meta_path)
        record = normalize_record(meta)
        year = str(record.get("year") or derive_case_year(
            decision_date=record.get("decision_date"),
            publication_date=record.get("publication_date"),
        ))
        if years and year not in years:
            continue
        items.append(PdfWorkItem(case_dir=case_dir, meta_path=meta_path, record=record))

    items.sort(
        key=lambda item: (
            str(item.record.get("year") or "unknown-year"),
            str(item.record.get("domain") or "khac"),
            str(item.record.get("slug") or item.case_dir.name),
        )
    )
    return items


def build_pdf_markdown(record: dict[str, Any], *, page_count: int, pages: list[str], source_pdf: Path, source_url: str) -> str:
    lines = [
        "---",
        f'case_id: {yaml_scalar(record.get("case_id"))}',
        f'source_case_id: {yaml_scalar(record.get("source_case_id"))}',
        f'slug: {yaml_scalar(record.get("slug"))}',
        f'title: {yaml_scalar(record.get("title"))}',
        f'document_kind: {yaml_scalar(record.get("document_kind"))}',
        f'case_number: {yaml_scalar(record.get("case_number"))}',
        f'decision_date: {yaml_scalar(record.get("decision_date"))}',
        f'publication_date: {yaml_scalar(record.get("publication_date"))}',
        f'year: {yaml_scalar(record.get("year"))}',
        f'case_type: {yaml_scalar(record.get("case_type"))}',
        f'domain: {yaml_scalar(record.get("domain"))}',
        f'court: {yaml_scalar(record.get("court"))}',
        f'source: {yaml_scalar(record.get("source"))}',
        f'source_url: {yaml_scalar(source_url)}',
        f'pdf_url: {yaml_scalar(record.get("pdf_url"))}',
        f'viewer_url: {yaml_scalar(record.get("viewer_url"))}',
        'status: "pdf-extracted"',
        'visibility: "restricted-source"',
        'language: "vi"',
        f'pdf_page_count: {yaml_scalar(page_count)}',
        f'pdf_source_path: {yaml_scalar(str(source_pdf))}',
        "---",
        "",
        "# PDF Extraction",
        "",
        "## Extracted Text",
        "",
    ]
    if pages:
        for idx, page_text in enumerate(pages, start=1):
            lines.append(f"### Page {idx}")
            lines.append("")
            lines.append(page_text or "_No extractable text on this page._")
            lines.append("")
    else:
        lines.append("_No extractable text found in the PDF._")
        lines.append("")

    return "\n".join(lines)


def build_pdf_error_markdown(record: dict[str, Any], *, source_pdf: Path, source_url: str, error: str) -> str:
    return "\n".join(
        [
            "---",
            f'case_id: {yaml_scalar(record.get("case_id"))}',
            f'source_case_id: {yaml_scalar(record.get("source_case_id"))}',
            f'slug: {yaml_scalar(record.get("slug"))}',
            f'title: {yaml_scalar(record.get("title"))}',
            f'year: {yaml_scalar(record.get("year"))}',
            f'source_url: {yaml_scalar(source_url)}',
            f'pdf_url: {yaml_scalar(record.get("pdf_url"))}',
            f'pdf_source_path: {yaml_scalar(str(source_pdf))}',
            'status: "pdf-error"',
            'visibility: "restricted-source"',
            'language: "vi"',
            "---",
            "",
            "# PDF Extraction",
            "",
            "## Error",
            "",
            error,
            "",
        ]
    )


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError(
            "pypdf is not installed. Run `pip install -r scripts/requirements.txt` first."
        ) from exc

    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on source PDF
            raise RuntimeError(f"Failed to decrypt PDF: {exc}") from exc

    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(clean_multiline_text(text))
    return pages


def process_item(item: PdfWorkItem, *, force: bool, timeout_seconds: float, verify: bool | str) -> PdfResult:
    record = item.record
    pdf_url = record.get("pdf_url")
    if not pdf_url:
        return PdfResult(case_dir=item.case_dir, pdf_path=None, pdf_md_path=None, status="skipped", message="No pdf_url present")

    pdf_path = item.case_dir / "files" / "source.pdf"
    pdf_md_path = item.case_dir / "pdf.md"
    if force:
        if pdf_md_path.exists():
            pdf_md_path.unlink()
        if pdf_path.exists():
            pdf_path.unlink()

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    client = PortalBinaryClient(timeout_seconds=timeout_seconds, verify=verify)
    source_url = str(item.record.get("source_url") or item.meta_path.as_uri())

    try:
        if force or not pdf_path.exists():
            download_path = pdf_path.with_suffix(".part")
            if download_path.exists():
                download_path.unlink()

            client.download(str(pdf_url), referer=source_url, output_path=download_path)
            download_path.replace(pdf_path)

        if pdf_path.exists():
            return PdfResult(case_dir=item.case_dir, pdf_path=pdf_path, pdf_md_path=pdf_md_path if pdf_md_path.exists() else None, status="ok")

        return PdfResult(case_dir=item.case_dir, pdf_path=None, pdf_md_path=None, status="skipped", message="PDF not downloaded")
    except Exception as exc:
        return PdfResult(case_dir=item.case_dir, pdf_path=pdf_path if pdf_path.exists() else None, pdf_md_path=pdf_md_path if pdf_md_path.exists() else None, status="error", message=str(exc))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and cache PDF judgments.")
    parser.add_argument("--root", default=str(DATA_DIR), help="Root directory containing case meta.json files.")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent PDF workers.")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of cases to process. 0 means all.")
    parser.add_argument("--year", action="append", default=[], help="Optional year filter. Repeat to process specific years only.")
    parser.add_argument("--force", action="store_true", help="Re-download cached PDFs even if source.pdf already exists.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Network timeout in seconds.")
    parser.add_argument("--ca-bundle", default=None, help="Path to a CA bundle file for TLS verification.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for local spike/debug use only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.workers <= 0:
        raise SystemExit("--workers must be > 0")
    if args.limit < 0:
        raise SystemExit("--limit must be >= 0")

    root = Path(args.root).expanduser().resolve()
    ensure_dirs(root)

    verify: bool | str = True
    if args.ca_bundle:
        verify = str(Path(args.ca_bundle).expanduser().resolve())
    elif args.insecure:
        verify = False
        LOGGER.warning("TLS verification is disabled for this run (`--insecure`).")
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    requested_years = {year.strip() for year in args.year if year.strip()} or None
    items = discover_case_items(root, requested_years)
    if args.limit:
        items = items[: args.limit]

    if not items:
        LOGGER.warning("No case folders with meta.json were found under %s", root)
        return 0

    queued: list[PdfWorkItem] = []
    for item in items:
        pdf_url = item.record.get("pdf_url")
        if not pdf_url:
            LOGGER.info("Skipping %s because no pdf_url is exposed.", item.case_dir)
            continue
        pdf_path = item.case_dir / "files" / "source.pdf"
        pdf_md_path = item.case_dir / "pdf.md"
        if not args.force and pdf_path.exists() and pdf_md_path.exists():
            LOGGER.info("Skipping %s because PDF markdown already exists.", item.case_dir)
            continue
        queued.append(item)

    if not queued:
        LOGGER.info("No PDF work left to do under %s", root)
        return 0

    LOGGER.info("Queued %s case(s) for PDF download/extraction.", len(queued))

    processed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(process_item, item, force=args.force, timeout_seconds=args.timeout, verify=verify): item
            for item in queued
        }
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                result = future.result()
                if result.status == "ok":
                    processed += 1
                    LOGGER.info("Extracted %s", item.case_dir)
                elif result.status == "skipped":
                    LOGGER.info("Skipped %s: %s", item.case_dir, result.message or "")
                else:
                    LOGGER.warning("Partial/failed %s: %s", item.case_dir, result.message or "")
            except Exception as exc:  # pragma: no cover - defensive guard
                LOGGER.error("Failed %s: %s", item.case_dir, exc)

    LOGGER.info("Completed %s PDF extraction job(s).", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
