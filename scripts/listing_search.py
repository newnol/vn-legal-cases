from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from common import LISTING_URL, absolute_url, clean_inline_whitespace, normalize_portal_date


CASE_TYPE_VALUE_BY_DOMAIN = {
    "hinh-su": "50",
    "dan-su": "0",
    "hon-nhan-gia-dinh": "1",
    "kinh-doanh-thuong-mai": "2",
    "hanh-chinh": "4",
    "lao-dong": "3",
    "quyet-dinh-tuyen-bo-pha-san": "5",
    "quyet-dinh-ap-dung-bien-phap-xu-ly-hanh-chinh": "11",
}

DOCUMENT_KIND_VALUE = {
    "ban-an": "0",
    "quyet-dinh": "1",
}


@dataclass(slots=True)
class ListingResult:
    detail_url: str
    heading: str
    publication_date: str | None
    case_type: str | None
    proceeding_stage: str | None
    summary_text: str | None
    document_kind: str | None


@dataclass(slots=True)
class ListingSearchPage:
    html: str
    results: list[ListingResult]
    total_records: int | None
    total_pages: int | None
    current_page: int | None


def _selected_or_empty(select) -> str:
    selected = select.find("option", selected=True)
    return selected.get("value", "") if selected else ""


def build_listing_search_form(
    html: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    case_type_value: str | None = None,
    document_kind_value: str | None = None,
    keyword: str = "",
) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    form: dict[str, str] = {}

    for tag in soup.select("input[name], textarea[name], select[name]"):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            form[name] = _selected_or_empty(tag)
        elif tag.get("type") in {"checkbox", "radio"}:
            if tag.has_attr("checked"):
                form[name] = tag.get("value", "on")
        else:
            form[name] = tag.get("value", "")

    form.update(
        {
            "ctl00$Content_home_Public$ctl00$txtKeyword_top": keyword,
            "ctl00$Content_home_Public$ctl00$Drop_STATUS_JUDGMENT_SEARCH_top": document_kind_value or "",
            "ctl00$Content_home_Public$ctl00$Drop_CASES_STYLES_SEARCH_top": case_type_value or "",
            "ctl00$Content_home_Public$ctl00$Rad_DATE_FROM_top": date_from or "",
            "ctl00$Content_home_Public$ctl00$Rad_DATE_TO_top": date_to or "",
            "ctl00$Content_home_Public$ctl00$cmd_search_banner": "Tìm kiếm",
        }
    )
    return form


def build_listing_page_form(html: str, *, page: int) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    form: dict[str, str] = {}

    for tag in soup.select("input[name], textarea[name], select[name]"):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            form[name] = _selected_or_empty(tag)
        elif tag.get("type") in {"checkbox", "radio"}:
            if tag.has_attr("checked"):
                form[name] = tag.get("value", "on")
        else:
            form[name] = tag.get("value", "")

    form["__EVENTTARGET"] = "ctl00$Content_home_Public$ctl00$DropPages"
    form["__EVENTARGUMENT"] = ""
    form["ctl00$Content_home_Public$ctl00$DropPages"] = str(page)
    return form


def parse_listing_page(html: str) -> ListingSearchPage:
    soup = BeautifulSoup(html, "html.parser")
    results: list[ListingResult] = []

    for card in soup.select("#List_group_pub > .list-group-item"):
        link = card.select_one('a[href*="/chi-tiet-ban-an"]')
        if link is None or not link.get("href"):
            continue

        heading_text = clean_inline_whitespace(link.get_text(" ", strip=True))
        case_type = None
        proceeding_stage = None
        document_kind = None
        summary_text = None
        publication_date = None

        labels = {}
        for label in card.select("label"):
            key = clean_inline_whitespace(label.get_text(" ", strip=True)).rstrip(":")
            parent = label.find_parent(["div", "p", "h4"])
            if parent is None:
                continue
            text = clean_inline_whitespace(parent.get_text(" ", strip=True))
            value = text.replace(key, "", 1).lstrip(": ").strip()
            labels[key] = value

        case_type = labels.get("Loại án")
        proceeding_stage = labels.get("Cấp xét xử")
        summary_text = labels.get("Thông tin về vụ án")
        document_kind = "Quyết định" if heading_text.lower().startswith("quyết định") else "Bản án"

        time_node = card.select_one("time")
        if time_node:
            publication_date = normalize_portal_date(time_node.get_text(" ", strip=True))

        results.append(
            ListingResult(
                detail_url=absolute_url(link.get("href")) or link.get("href"),
                heading=heading_text,
                publication_date=publication_date,
                case_type=case_type,
                proceeding_stage=proceeding_stage,
                summary_text=summary_text,
                document_kind=document_kind,
            )
        )

    total_records = None
    total_pages = None
    current_page = None

    total_node = soup.find(id="ctl00_Content_home_Public_ctl00_lbl_count_record_top")
    if total_node:
        text = clean_inline_whitespace(total_node.get_text(" ", strip=True)).replace(".", "")
        if text.isdigit():
            total_records = int(text)

    pages_node = soup.find(id="ctl00_Content_home_Public_ctl00_LbShowtotal")
    if pages_node:
        text = clean_inline_whitespace(pages_node.get_text(" ", strip=True)).replace(".", "")
        if text.isdigit():
            total_pages = int(text)

    drop_pages = soup.find("select", id="ctl00_Content_home_Public_ctl00_DropPages")
    if drop_pages:
        selected = drop_pages.find("option", selected=True)
        if selected and (selected.get("value") or "").isdigit():
            current_page = int(selected.get("value"))

    return ListingSearchPage(
        html=html,
        results=results,
        total_records=total_records,
        total_pages=total_pages,
        current_page=current_page,
    )
