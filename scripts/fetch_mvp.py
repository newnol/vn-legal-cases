from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import shutil
import time
import subprocess
import tempfile
from dataclasses import dataclass
from urllib.parse import urlencode
from pathlib import Path
from typing import Any
import uuid

import requests
import urllib3
from bs4 import BeautifulSoup

from common import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    FRONTIER_DB_PATH,
    HOME_URL,
    LISTING_URL,
    USER_AGENT,
    absolute_url,
    build_case_slug,
    clean_inline_whitespace,
    clean_multiline_text,
    dedupe_preserve_order,
    derive_case_year,
    domain_slug,
    case_output_dir,
    ensure_dirs,
    extract_keywords,
    extract_source_case_id,
    normalize_portal_date,
    today_utc_iso,
    write_json,
    write_text,
    yaml_scalar,
)
from frontier import FrontierStore
from listing_search import (
    CASE_TYPE_VALUE_BY_DOMAIN,
    DOCUMENT_KIND_VALUE,
    build_listing_page_form,
    build_listing_search_form,
    parse_listing_page,
)


LOGGER = logging.getLogger("fetch_mvp")


@dataclass(slots=True)
class ResponseLike:
    status_code: int
    url: str
    text: str

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error for url: {self.url}")


class PortalClient:
    def __init__(self, *, delay_seconds: float, timeout_seconds: float, verify: bool | str) -> None:
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.verify = verify
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "vi,en;q=0.8",
            }
        )
        self._last_request_at = 0.0
        self._use_curl_fallback = False
        self._cookie_jar_path = Path(tempfile.gettempdir()) / f"vn-legal-cases-cookies-{os.getpid()}-{uuid.uuid4().hex}.txt"

    def _respect_delay(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if self._last_request_at and elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def get(self, url: str) -> ResponseLike:
        return self._request("GET", url)

    def post(self, url: str, *, data: dict[str, str]) -> ResponseLike:
        return self._request("POST", url, data=data)

    def _request(self, method: str, url: str, *, data: dict[str, str] | None = None) -> ResponseLike:
        self._respect_delay()
        try:
            if self._use_curl_fallback:
                response = self._request_with_curl(method, url, data=data)
            else:
                response = self.session.request(
                    method,
                    url,
                    data=data,
                    timeout=self.timeout_seconds,
                    verify=self.verify,
                )
        except requests.exceptions.SSLError as exc:
            if shutil.which("curl"):
                LOGGER.warning("Switching to curl transport after TLS failure on %s %s", method, url)
                self._use_curl_fallback = True
                response = self._request_with_curl(method, url, data=data)
            else:
                verify_mode = "disabled (`--insecure`)" if self.verify is False else "enabled"
                raise RuntimeError(
                    "TLS handshake failed while talking to congbobanan.toaan.gov.vn. "
                    f"Current verification mode: {verify_mode}. "
                    "Try `--insecure` for a local spike, or pass `--ca-bundle /path/to/cacert.pem` "
                    "if your environment has a custom trust store."
                ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"HTTP request failed for {method} {url}: {exc}") from exc
        self._last_request_at = time.monotonic()

        if response.status_code in {429, 503}:
            raise RuntimeError(f"Portal returned {response.status_code}; stopping to avoid overload.")

        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response

    def _request_with_curl(self, method: str, url: str, *, data: dict[str, str] | None = None) -> ResponseLike:
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("curl is not available in this environment.")

        with tempfile.TemporaryDirectory(prefix="vn-legal-cases-curl-") as tmpdir:
            body_path = Path(tmpdir) / "body.txt"
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
                "--cookie",
                str(self._cookie_jar_path),
                "--cookie-jar",
                str(self._cookie_jar_path),
                "--output",
                str(body_path),
                "--write-out",
                "%{http_code}\\n%{url_effective}\\n",
                "--max-time",
                str(int(self.timeout_seconds)),
            ]

            if self.verify is False:
                base_command.append("--insecure")
            elif isinstance(self.verify, str):
                base_command.extend(["--cacert", self.verify])

            if method.upper() == "POST":
                base_command.extend(["--request", "POST", "--data-binary", "@-"])
                payload = urlencode(data or {})
            else:
                base_command.extend(["--request", "GET"])
                payload = None

            profiles: list[tuple[str, list[str]]] = [
                ("default", []),
                ("tlsv1.2", ["--tlsv1.2"]),
                ("tlsv1.2+lowsec", ["--tlsv1.2", "--ciphers", "DEFAULT:@SECLEVEL=1"]),
            ]
            errors: list[str] = []

            for profile_name, extra_flags in profiles:
                command = base_command + extra_flags + [url]
                result = subprocess.run(
                    command,
                    input=payload,
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

                    body_text = body_path.read_text(encoding="utf-8", errors="replace")
                    if http_code >= 400:
                        raise RuntimeError(f"curl returned HTTP {http_code} for {method} {url}")
                    if profile_name != "default":
                        LOGGER.warning(
                            "curl succeeded for %s %s using TLS profile %s",
                            method,
                            url,
                            profile_name,
                        )
                    return ResponseLike(status_code=http_code, url=effective_url, text=body_text)

                stderr = (result.stderr or result.stdout or "").strip()
                errors.append(f"{profile_name}: {stderr or f'exit code {result.returncode}'}")
                LOGGER.debug("curl profile %s failed for %s %s: %s", profile_name, method, url, stderr)

            raise RuntimeError(
                f"curl request failed for {method} {url} after trying TLS profiles: "
                + " | ".join(errors)
            )

    def fetch_seed_detail_urls(self) -> list[str]:
        response = self.get(HOME_URL)
        soup = BeautifulSoup(response.text, "lxml")
        hrefs = [
            absolute_url(anchor.get("href"))
            for anchor in soup.select('a[href*="/chi-tiet-ban-an"]')
            if anchor.get("href")
        ]
        return [href for href in dedupe_preserve_order(hrefs) if href]

    def fetch_listing_results(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
        case_type_value: str | None,
        document_kind_value: str | None,
        page_limit: int,
        keyword: str = "",
    ) -> list[str]:
        initial = self.get(LISTING_URL)
        form = build_listing_search_form(
            initial.text,
            date_from=date_from,
            date_to=date_to,
            case_type_value=case_type_value,
            document_kind_value=document_kind_value,
            keyword=keyword,
        )
        search_response = self.post(LISTING_URL, data=form)
        search_page = parse_listing_page(search_response.text)
        detail_urls = [result.detail_url for result in search_page.results if result.detail_url]

        max_page = min(page_limit, search_page.total_pages or page_limit)
        current_html = search_response.text
        for page_number in range(2, max_page + 1):
            page_form = build_listing_page_form(current_html, page=page_number)
            page_response = self.post(LISTING_URL, data=page_form)
            current_html = page_response.text
            parsed = parse_listing_page(current_html)
            detail_urls.extend(result.detail_url for result in parsed.results if result.detail_url)

        return dedupe_preserve_order(detail_urls)

    def fetch_case(self, detail_url: str) -> dict[str, Any]:
        response = self.get(detail_url)
        return parse_detail_page(response.text, source_url=str(response.url))


def parse_detail_page(html: str, *, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    source_case_id = extract_source_case_id(source_url)

    metadata_panel = soup.select_one("div.panel.panel-blue")
    if metadata_panel is None:
        raise ValueError(f"Could not find metadata panel in {source_url}")

    heading_text = clean_inline_whitespace(metadata_panel.select_one(".panel-heading strong").get_text(" ", strip=True))
    heading_match = None
    if heading_text:
        import re

        heading_match = re.search(r"(.+?)\s+ngày\s+(\d{2}/\d{2}/\d{4})", heading_text)

    field_map: dict[str, str] = {}
    for item in metadata_panel.select("ul.list-group > li.list-group-item"):
        label = item.select_one("label")
        if label is None:
            continue
        label_text = clean_inline_whitespace(label.get_text(" ", strip=True)).rstrip(":")
        full_text = clean_multiline_text(item.get_text("\n", strip=True)).replace("\n", " ")
        value = full_text.replace(label_text, "", 1).lstrip(": ").strip()
        field_map[label_text] = clean_inline_whitespace(value)

    raw_title = field_map.get("Tên bản án", "")
    publication_date = None
    if raw_title.endswith(")"):
        import re

        publication_match = re.search(r"\((\d{2}[./]\d{2}[./]\d{4})\)\s*$", raw_title)
        if publication_match:
            publication_date = normalize_portal_date(publication_match.group(1))
            raw_title = raw_title[: publication_match.start()].rstrip(" -")

    document_kind = "Quyết định" if raw_title.lower().startswith("quyết định") else "Bản án"
    case_number = None
    decision_date = None
    if heading_match:
        import re

        raw_case_number = clean_inline_whitespace(heading_match.group(1))
        number_match = re.search(r"(?:Bản án|Quyết định)\s+số:?\s*(.+)", raw_case_number, flags=re.IGNORECASE)
        case_number = clean_inline_whitespace(number_match.group(1)) if number_match else raw_case_number
        decision_date = normalize_portal_date(heading_match.group(2))

    pdf_anchor = soup.select_one('a[href$=".pdf"]')
    iframe = soup.select_one("iframe#iframe_pub")
    related_urls = [
        absolute_url(anchor.get("href"))
        for anchor in soup.select('a[href*="/chi-tiet-ban-an"]')
        if anchor.get("href")
    ]
    related_urls = [
        url
        for url in dedupe_preserve_order(related_urls)
        if url and url != source_url
    ]

    summary_text = field_map.get("Thông tin về vụ án")
    case_type = field_map.get("Loại án")
    keywords = extract_keywords(title=raw_title, case_type=case_type, summary_text=summary_text)
    year = derive_case_year(decision_date=decision_date, publication_date=publication_date)

    case_slug = build_case_slug(
        source_case_id=source_case_id,
        document_kind=document_kind,
        case_number=case_number,
        decision_date=decision_date,
        court=field_map.get("Tòa án xét xử"),
    )

    return {
        "ids": {
            "source_case_id": source_case_id,
            "case_slug": case_slug,
            "case_id": f"ta-{source_case_id}",
        },
        "source": {
            "name": "congbobanan.toaan.gov.vn",
            "detail_url": source_url,
            "pdf_url": absolute_url(pdf_anchor.get("href")) if pdf_anchor else None,
            "viewer_url": absolute_url(iframe.get("src")) if iframe else None,
            "seed_source": HOME_URL,
            "fetched_at": today_utc_iso(),
            "user_agent": USER_AGENT,
            "redistribution_notice": (
                "Portal footer indicates reposting or reissuing information/data "
                "requires written permission from the Supreme People's Court of Vietnam."
            ),
        },
        "metadata": {
            "document_kind": document_kind,
            "case_number": case_number,
            "decision_date": decision_date,
            "publication_date": publication_date,
            "year": year,
            "title": raw_title or heading_text,
            "case_type": case_type,
            "domain": domain_slug(case_type),
            "proceeding_stage": field_map.get("Cấp xét xử"),
            "court": field_map.get("Tòa án xét xử"),
            "applied_precedent": field_map.get("Áp dụng án lệ"),
            "correction_count": field_map.get("Đính chính"),
            "summary_text": summary_text,
            "keywords": keywords,
            "related_detail_urls": related_urls,
        },
    }


def build_provisional_raw_markdown(case_meta: dict[str, Any]) -> str:
    ids = case_meta["ids"]
    source = case_meta["source"]
    metadata = case_meta["metadata"]
    lines = [
        "---",
        f'case_id: {yaml_scalar(ids["case_id"])}',
        f'source_case_id: {yaml_scalar(ids["source_case_id"])}',
        f'slug: {yaml_scalar(ids["case_slug"])}',
        f'title: {yaml_scalar(metadata.get("title"))}',
        f'document_kind: {yaml_scalar(metadata.get("document_kind"))}',
        f'case_number: {yaml_scalar(metadata.get("case_number"))}',
        f'decision_date: {yaml_scalar(metadata.get("decision_date"))}',
        f'publication_date: {yaml_scalar(metadata.get("publication_date"))}',
        f'year: {yaml_scalar(metadata.get("year"))}',
        f'case_type: {yaml_scalar(metadata.get("case_type"))}',
        f'domain: {yaml_scalar(metadata.get("domain"))}',
        f'proceeding_stage: {yaml_scalar(metadata.get("proceeding_stage"))}',
        f'court: {yaml_scalar(metadata.get("court"))}',
        f'source: {yaml_scalar(source.get("name"))}',
        f'source_url: {yaml_scalar(source.get("detail_url"))}',
        f'pdf_url: {yaml_scalar(source.get("pdf_url"))}',
        f'viewer_url: {yaml_scalar(source.get("viewer_url"))}',
        'status: "raw"',
        'visibility: "restricted-source"',
        'language: "vi"',
        "---",
        "",
        "# Raw Capture",
        "",
        "## Source Summary",
        "",
        metadata.get("summary_text") or "MVP mới thu được metadata HTML và link PDF; full text extraction còn để bước sau.",
        "",
        "## Retrieval Notes",
        "",
        f"- Captured from: {source.get('detail_url')}",
        "- Public redistribution should be reviewed before committing raw full text.",
        "- This MVP does not OCR or extract full text from the linked PDF.",
        "",
    ]
    return "\n".join(lines)


def fetch_case_worker(
    detail_url: str,
    *,
    delay_seconds: float,
    timeout_seconds: float,
    verify: bool | str,
) -> dict[str, Any]:
    client = PortalClient(delay_seconds=delay_seconds, timeout_seconds=timeout_seconds, verify=verify)
    return client.fetch_case(detail_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a minimal set of case records from congbobanan.toaan.gov.vn.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of cases to fetch.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delay between requests in seconds.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout in seconds.")
    parser.add_argument("--out-dir", default=str(Path.cwd() / "data"), help="Output root for case folders.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse but do not write files.")
    parser.add_argument(
        "--detail-url",
        action="append",
        default=[],
        help="Optional explicit detail URL. Repeat to seed additional URLs.",
    )
    parser.add_argument(
        "--queue-db",
        default=str(FRONTIER_DB_PATH),
        help="Path to the SQLite frontier database.",
    )
    parser.add_argument(
        "--seed-home",
        action="store_true",
        help="Seed the frontier from the homepage before fetching.",
    )
    parser.add_argument(
        "--reset-fetching",
        action="store_true",
        help="Move stuck `fetching` items back to `discovered` before claiming new work.",
    )
    parser.add_argument(
        "--fetching-stale-after-seconds",
        type=int,
        default=900,
        help="Auto-reclaim `fetching` items older than this many seconds. Use 0 to disable.",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=int,
        default=900,
        help="Delay before a failed URL becomes eligible again.",
    )
    parser.add_argument(
        "--queue-stats",
        action="store_true",
        help="Print frontier counts and exit.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent fetch workers for detail pages. Use 1 to disable concurrency.",
    )
    parser.add_argument(
        "--seed-listing",
        action="store_true",
        help="Seed the frontier from the listing/search form.",
    )
    parser.add_argument(
        "--listing-date-from",
        default=None,
        help="Listing filter `Từ ngày` in dd/MM/yyyy.",
    )
    parser.add_argument(
        "--listing-date-to",
        default=None,
        help="Listing filter `Đến ngày` in dd/MM/yyyy.",
    )
    parser.add_argument(
        "--listing-domain",
        default="hinh-su",
        help="Domain slug used to choose the listing case type filter.",
    )
    parser.add_argument(
        "--listing-document-kind",
        default="ban-an",
        choices=["ban-an", "quyet-dinh"],
        help="Listing document kind filter.",
    )
    parser.add_argument(
        "--listing-pages",
        type=int,
        default=1,
        help="Maximum number of listing pages to seed per query.",
    )
    parser.add_argument(
        "--listing-keyword",
        default="",
        help="Optional keyword for listing search.",
    )
    parser.add_argument(
        "--ca-bundle",
        default=None,
        help="Path to a CA bundle file for TLS verification.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for local spike/debug use only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.limit <= 0:
        raise SystemExit("--limit must be > 0")
    if args.workers <= 0:
        raise SystemExit("--workers must be > 0")

    output_root = Path(args.out_dir).expanduser().resolve()
    ensure_dirs(output_root)
    frontier = FrontierStore(Path(args.queue_db).expanduser().resolve())

    verify: bool | str = True
    if args.ca_bundle:
        verify = str(Path(args.ca_bundle).expanduser().resolve())
    elif args.insecure:
        verify = False
        LOGGER.warning("TLS verification is disabled for this run (`--insecure`).")
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    client = PortalClient(delay_seconds=args.delay, timeout_seconds=args.timeout, verify=verify)

    if args.reset_fetching:
        reset_count = frontier.reset_fetching()
        LOGGER.info("Reset %s stuck `fetching` item(s).", reset_count)
    elif args.fetching_stale_after_seconds > 0:
        reclaimed = frontier.reclaim_stale_fetching(
            stale_after_seconds=args.fetching_stale_after_seconds
        )
        if reclaimed:
            LOGGER.info(
                "Reclaimed %s stale `fetching` item(s) older than %ss.",
                reclaimed,
                args.fetching_stale_after_seconds,
            )

    explicit_seed_urls = dedupe_preserve_order([url for url in args.detail_url if url])
    if explicit_seed_urls:
        frontier.upsert_urls(explicit_seed_urls, discovery_source="cli", priority=10)

    if args.seed_home or (not explicit_seed_urls and not args.queue_stats and not args.seed_listing):
        LOGGER.info("Fetching seed detail URLs from homepage...")
        home_seed_urls = client.fetch_seed_detail_urls()
        frontier.upsert_urls(home_seed_urls, discovery_source="home", priority=20)

    if args.seed_listing:
        case_type_value = CASE_TYPE_VALUE_BY_DOMAIN.get(args.listing_domain, "")
        document_kind_value = DOCUMENT_KIND_VALUE.get(args.listing_document_kind, "")
        LOGGER.info(
            "Fetching listing seeds domain=%s kind=%s pages=%s range=%s..%s",
            args.listing_domain,
            args.listing_document_kind,
            args.listing_pages,
            args.listing_date_from,
            args.listing_date_to,
        )
        listing_urls = client.fetch_listing_results(
            date_from=args.listing_date_from,
            date_to=args.listing_date_to,
            case_type_value=case_type_value,
            document_kind_value=document_kind_value,
            page_limit=max(args.listing_pages, 1),
            keyword=args.listing_keyword,
        )
        frontier.upsert_urls(listing_urls, discovery_source="listing", priority=30)
        LOGGER.info("Seeded %s detail URL(s) from listing.", len(listing_urls))

    if args.queue_stats:
        counts = frontier.counts()
        for key in sorted(counts):
            LOGGER.info("%s=%s", key, counts[key])
        frontier.close()
        return 0

    claimed_items = frontier.claim_batch(args.limit)
    if not claimed_items:
        LOGGER.info("No eligible items found in frontier.")
        counts = frontier.counts()
        for key in sorted(counts):
            LOGGER.info("%s=%s", key, counts[key])
        frontier.close()
        return 0

    processed = 0

    def _persist_case(case_meta: dict[str, Any]) -> None:
        metadata = case_meta["metadata"]
        ids = case_meta["ids"]
        year = metadata.get("year") or "unknown-year"
        case_dir = case_output_dir(output_root, year=str(year), domain=metadata["domain"], case_slug=ids["case_slug"])

        if args.dry_run:
            LOGGER.info(
                "[dry-run] %s | %s | %s",
                ids["source_case_id"],
                metadata.get("case_type"),
                metadata.get("title"),
            )
        else:
            write_json(case_dir / "meta.json", case_meta)
            write_text(case_dir / "raw.md", build_provisional_raw_markdown(case_meta))
            LOGGER.info("Wrote %s", case_dir)

        related_urls = metadata.get("related_detail_urls") or []
        if related_urls:
            frontier.upsert_urls(
                related_urls,
                discovery_source=f"related:{ids['source_case_id']}",
                priority=80,
            )

    if args.workers == 1 or len(claimed_items) == 1:
        for item in claimed_items:
            detail_url = item.detail_url
            LOGGER.info("Fetching %s", detail_url)
            try:
                case_meta = client.fetch_case(detail_url)
                _persist_case(case_meta)
                frontier.mark_fetched(detail_url)
                processed += 1
            except Exception as exc:
                frontier.mark_failed(detail_url, str(exc), retry_delay_seconds=args.retry_delay_seconds)
                LOGGER.error("Failed %s: %s", detail_url, exc)
                continue
    else:
        futures: dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for item in claimed_items:
                LOGGER.info("Fetching %s", item.detail_url)
                future = executor.submit(
                    fetch_case_worker,
                    item.detail_url,
                    delay_seconds=args.delay,
                    timeout_seconds=args.timeout,
                    verify=verify,
                )
                futures[future] = item.detail_url

            for future in as_completed(futures):
                detail_url = futures[future]
                try:
                    case_meta = future.result()
                    _persist_case(case_meta)
                    frontier.mark_fetched(detail_url)
                    processed += 1
                except Exception as exc:
                    frontier.mark_failed(detail_url, str(exc), retry_delay_seconds=args.retry_delay_seconds)
                    LOGGER.error("Failed %s: %s", detail_url, exc)

    counts = frontier.counts()
    LOGGER.info("Processed %s case(s).", processed)
    for key in sorted(counts):
        LOGGER.info("%s=%s", key, counts[key])
    frontier.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
