from __future__ import annotations

import base64
import logging
import re
from datetime import datetime

import httpx
from lxml import html as lxml_html

from src.config import get_settings
from src.models.metadata import (
    CollectionItem,
    GenreItem,
    GuidItem,
    ImageItem,
    MetadataItem,
    RoleItem,
)
from src.scrapers.base import BaseScraper
from src.utils.guid import build_guid, build_rating_key
from src.utils.text import normalize_whitespace, strip_diacritics

logger = logging.getLogger(__name__)

BASE_URL = "https://gay-hotfile.errio.net"
SEARCH_URL = BASE_URL + "/index.php?do=search"
MAX_RESULTS = 40


def _clean_search_string(title: str) -> str:
    value = title.replace(" - ", ": ").lower().strip()
    value = strip_diacritics(value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value)
    return normalize_whitespace(value)


def _to_absolute_url(value: str) -> str:
    if not value:
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return BASE_URL + value
    return value


def _encode_source_id(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_source_id(source_id: str) -> str:
    padding = "=" * ((4 - (len(source_id) % 4)) % 4)
    return base64.urlsafe_b64decode(source_id + padding).decode("utf-8")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = normalize_whitespace(item)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _parse_release_date(main_lines: list[str]) -> datetime | None:
    """Extract release date from 'Release Year: DD-MM-YYYY' pattern in content lines."""
    for line in main_lines:
        if "Release Year:" in line:
            raw = line.replace("Release Year:", "").strip()
            # Try dd-mm-yyyy
            for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
            # Try just a year
            yr_match = re.search(r"(\d{4})", raw)
            if yr_match:
                try:
                    return datetime(int(yr_match.group(1)), 12, 31)
                except ValueError:
                    pass
    return None


def _parse_post_date(lines: list[str]) -> datetime | None:
    """Extract post date from lines like '17-01-2026, 18:34'."""
    for line in lines:
        match = re.search(r"\b(\d{2}-\d{2}-\d{4})(?:,\s*\d{1,2}:\d{2})?\b", line)
        if not match:
            continue
        raw_date = match.group(1)
        try:
            return datetime.strptime(raw_date, "%d-%m-%Y")
        except ValueError:
            continue
    return None


def _parse_duration_ms(main_lines: list[str]) -> int | None:
    """Extract duration from 'Duration: HH:MM:SS' pattern in content lines."""
    for i, line in enumerate(main_lines):
        if line.strip() == "Duration:" and i + 1 < len(main_lines):
            raw = main_lines[i + 1].strip()
            if ":" in raw:
                parts = raw.split(":")
                try:
                    if len(parts) == 3:
                        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                        return (h * 3600 + m * 60 + s) * 1000
                    if len(parts) == 2:
                        m, s = int(parts[0]), int(parts[1])
                        return (m * 60 + s) * 1000
                except ValueError:
                    pass
        elif "Duration:" in line:
            raw = line.replace("Duration:", "").strip()
            if ":" in raw:
                parts = raw.split(":")
                try:
                    if len(parts) == 3:
                        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                        return (h * 3600 + m * 60 + s) * 1000
                    if len(parts) == 2:
                        m, s = int(parts[0]), int(parts[1])
                        return (m * 60 + s) * 1000
                except ValueError:
                    pass
    return None


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None


class HFGPMScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @property
    def source_key(self) -> str:
        return "hfgpm"

    @property
    def source_name(self) -> str:
        return "HFGPM"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        query = _clean_search_string(title)

        form_data = {
            "do": "search",
            "subaction": "search",
            "search_start": "1",
            "full_search": "1",
            "story": query,
            "titleonly": "3",
            "searchuser": "",
            "replyless": "0",
            "replylimit": "0",
            "searchdate": "0",
            "beforeafter": "after",
            "sortby": "date",
            "resorder": "desc",
            "result_num": "500",
            "result_from": "1",
            "showposts": "0",
            "catlist[]": "0",
        }

        try:
            response = await self._client.post(
                SEARCH_URL, data=form_data, timeout=30.0
            )
            response.raise_for_status()
        except Exception:
            logger.exception("HFGPM search request failed")
            return []

        tree = lxml_html.fromstring(response.text)
        film_nodes = tree.xpath('//div[@class="base shortstory"]')
        if not film_nodes:
            logger.info("HFGPM search for %r returned 0 results", title)
            return []

        results: list[MetadataItem] = []

        for node in film_nodes:
            if len(results) >= MAX_RESULTS:
                break

            entry = _first_text(
                node.xpath('./div[@class="bshead"]/div[@class="bshead"]/h1/a/text()')
            )
            if not entry:
                entry = _first_text(node.xpath('.//h1/a/text()'))
            if not entry:
                continue

            # Parse "Studio - Title" format
            if " - " in entry:
                studio, film_title = entry.split(" - ", 1)
                studio = normalize_whitespace(studio)
                film_title = normalize_whitespace(film_title)
            else:
                studio = None
                film_title = normalize_whitespace(entry)

            href = _first_text(
                node.xpath('./div[@class="bshead"]/div[@class="bshead"]/h1/a/@href')
            )
            if not href:
                href = _first_text(node.xpath('.//h1/a/@href'))
            if not href:
                continue
            film_url = _to_absolute_url(href)
            source_id = _encode_source_id(film_url)

            # Extract year from main content if available
            main_lines = [x.strip() for x in node.xpath('.//div[@class="maincont"]/div//text()') if x.strip()]
            release_date = _parse_release_date(main_lines)
            if not release_date:
                header_lines = [x.strip() for x in node.xpath('.//div[contains(@class,"bshead")]//text()') if x.strip()]
                release_date = _parse_post_date(header_lines)
            release_year = release_date.year if release_date else None

            if year and release_year and year != release_year:
                continue

            thumb = _first_text(node.xpath('.//img/@src'))

            rating_key = build_rating_key(self.source_key, source_id)
            guid = build_guid(provider_id, rating_key)

            results.append(
                MetadataItem(
                    type="movie",
                    ratingKey=rating_key,
                    guid=guid,
                    title=film_title,
                    year=release_year,
                    studio=studio,
                    thumb=_to_absolute_url(thumb) if thumb else None,
                )
            )

        logger.info("HFGPM search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        film_url = _decode_source_id(source_id)
        film_url = _to_absolute_url(film_url)

        response = await self._client.get(film_url, timeout=45.0)
        response.raise_for_status()
        tree = lxml_html.fromstring(response.text)

        # Title from page entry
        entry = _first_text(tree.xpath('//h1/text()'))
        studio: str | None = None
        page_title: str | None = entry

        if entry and " - " in entry:
            studio, page_title = entry.split(" - ", 1)
            studio = normalize_whitespace(studio)
            page_title = normalize_whitespace(page_title)
        elif entry:
            page_title = normalize_whitespace(entry)

        if not page_title:
            page_title = _first_text(tree.xpath('//title/text()'))

        # Main content lines — used for release date, duration, synopsis
        # Class may be "maincont" or "maincont clr" depending on the page template.
        main_lines = [
            x.strip()
            for x in tree.xpath('//div[contains(@class,"maincont")]/div//text()')
            if x.strip()
        ]
        header_lines = [
            x.strip()
            for x in tree.xpath('//div[contains(@class,"bshead")]//text()')
            if x.strip()
        ]

        release_date = _parse_release_date(main_lines)
        if not release_date:
            release_date = _parse_post_date(header_lines)
        duration_ms = _parse_duration_ms(main_lines)

        release_year = release_date.year if release_date else None
        release_iso = release_date.strftime("%Y-%m-%d") if release_date else None

        # Technical detail labels to exclude from synopsis
        tech_prefixes = (
            "Release Year:", "Duration:", "Studio:", "Actors:", "Cast:", "Genre:",
            "Length:", "Video:", "Audio:", "single file",
        )

        # Cast — look for explicit label first, then fall back to unlabeled
        # comma-separated performer line (common on movie detail pages).
        cast: list[str] = []
        for i, line in enumerate(main_lines):
            if "Actors:" in line or "Cast:" in line:
                raw = line.split(":", 1)[1].strip() if ":" in line else ""
                if not raw and i + 1 < len(main_lines):
                    raw = main_lines[i + 1]
                if raw:
                    cast = _dedupe([name.strip() for name in raw.split(",") if name.strip()])
                break

        cast_line_idx: int | None = None
        if not cast:
            # Unlabeled cast: first comma-separated line after the title that
            # looks like a list of performer names (multiple commas, each part
            # is short enough to be a name, not a synopsis sentence).
            title_key = (page_title or "").lower()
            for i, line in enumerate(main_lines):
                if line.lower() == title_key or line == entry:
                    continue
                if any(line.startswith(p) for p in tech_prefixes):
                    continue
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if len(parts) >= 3 and all(len(p) < 40 for p in parts):
                    cast = _dedupe(parts)
                    cast_line_idx = i
                    break

        # Genre — look for genre label
        raw_genres: list[str] = []
        for i, line in enumerate(main_lines):
            if "Genre:" in line:
                raw = line.split(":", 1)[1].strip() if ":" in line else ""
                if not raw and i + 1 < len(main_lines):
                    raw = main_lines[i + 1]
                if raw:
                    raw_genres = _dedupe([g.strip() for g in raw.split(",") if g.strip()])
                break

        # Synopsis — remaining content lines that aren't structured labels,
        # the cast line, or the entry title.
        synopsis_parts: list[str] = []
        for i, line in enumerate(main_lines):
            if i == cast_line_idx:
                continue
            if any(line.startswith(p) for p in tech_prefixes):
                continue
            if line == entry or line == page_title:
                continue
            if len(line) <= 10:
                continue
            # Skip file size/resolution/codec fragments
            if re.match(r"^\d+(\.\d+)?\s*(GB|MB|Kbps|Khz|fps)", line):
                continue
            synopsis_parts.append(line)
        synopsis = normalize_whitespace(" ".join(synopsis_parts))

        # Images — filter out site chrome / ad banners (typically from the site domain itself)
        image_urls = _dedupe([
            _to_absolute_url(src)
            for src in tree.xpath('//div[contains(@class,"maincont")]//img/@src')
            if src and "gay-hotfile.errio.net/chat/" not in src
        ])

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        image_items: list[ImageItem] = []
        for img_url in image_urls[: settings.artwork_max_posters]:
            image_items.append(ImageItem(alt=page_title or "", type="coverPoster", url=img_url))
        bg_urls = image_urls[1:] if len(image_urls) > 1 else image_urls[:1]
        for img_url in bg_urls[: settings.artwork_max_backgrounds]:
            image_items.append(ImageItem(alt=page_title or "", type="background", url=img_url))

        collection_items = [CollectionItem(tag=studio)] if studio else []

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            title=page_title,
            year=release_year,
            originallyAvailableAt=release_iso,
            summary=synopsis or None,
            studio=studio,
            duration=duration_ms,
            contentRating="X",
            isAdult=True,
            Image=image_items or None,
            Genre=[GenreItem(tag=g) for g in raw_genres] or None,
            Role=[RoleItem(tag=c, role="Performer") for c in cast] or None,
            Collection=collection_items or None,
            Guid=[GuidItem(id=guid)],
        )
