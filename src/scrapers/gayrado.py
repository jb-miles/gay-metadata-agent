from __future__ import annotations

import logging
import re
import urllib.parse

import httpx
from lxml import html as lxml_html

from src.config import get_settings
from src.models.metadata import (
    CollectionItem,
    DirectorItem,
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

BASE_URL = "https://www.gayrado.com/shop/en"
BASE_SEARCH_URL = BASE_URL + "/search?controller=search&s={0}"
FALLBACK_DETAIL_URL = BASE_URL + "/index.php?id_product={0}&controller=product&id_lang=1"

PRODUCT_ID_PATTERN = re.compile(r"/dvds/(\d+)-", re.IGNORECASE)
TITLE_PATTERN = re.compile(r"^(?P<title>.+?)\s+DVD\s+\((?P<studio>[^)]+)\)$", re.IGNORECASE)
MAX_RESULTS = 20
MAX_PAGES = 10
GENRE_SKIP = {"dvds & media", "dvds"}


def _clean_search_string(title: str) -> str:
    value = title.strip().lower()
    value = strip_diacritics(value)
    encoded = urllib.parse.quote(value)
    return encoded.replace("%25", "%").replace("*", "")


def _to_absolute_url(value: str) -> str:
    if not value:
        return value
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return "https://www.gayrado.com" + value
    return value


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


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None


def _parse_runtime_ms(value: str | None) -> int | None:
    if not value:
        return None
    raw = normalize_whitespace(value)
    total_minutes = 0
    hrs_match = re.search(r"(\d+)\s*h", raw, re.IGNORECASE)
    mins_match = re.search(r"(\d+)\s*min", raw, re.IGNORECASE)
    if hrs_match:
        total_minutes += int(hrs_match.group(1)) * 60
    if mins_match:
        total_minutes += int(mins_match.group(1))
    elif not hrs_match:
        mins_only = re.search(r"(\d+)", raw)
        if not mins_only:
            return None
        total_minutes = int(mins_only.group(1))
    return total_minutes * 60_000 if total_minutes > 0 else None


def _parse_title_and_studio(value: str) -> tuple[str, str | None]:
    cleaned = normalize_whitespace(value)
    matched = TITLE_PATTERN.match(cleaned)
    if not matched:
        return cleaned, None
    return normalize_whitespace(matched.group("title")), normalize_whitespace(matched.group("studio"))


def _extract_description_text(tree: lxml_html.HtmlElement) -> str:
    desc_node = tree.xpath('(//div[@id="description"]//div[contains(@class,"product-description")])[1]')
    if not desc_node:
        return ""
    return normalize_whitespace(" ".join(desc_node[0].xpath('.//text()[normalize-space()]')))


def _extract_labeled_block(desc_text: str, label: str, terminators: list[str]) -> str | None:
    if not desc_text:
        return None
    terminator_pattern = "|".join(re.escape(item) for item in terminators)
    if terminator_pattern:
        pattern = rf"{re.escape(label)}\s*(.*?)(?={terminator_pattern}|$)"
    else:
        pattern = rf"{re.escape(label)}\s*(.*)$"
    matched = re.search(pattern, desc_text, re.IGNORECASE)
    if not matched:
        return None
    return normalize_whitespace(matched.group(1))


class GayRadoScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self._movie_urls: dict[str, str] = {}

    @property
    def source_key(self) -> str:
        return "gayrado"

    @property
    def source_name(self) -> str:
        return "GayRado"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        encoded = _clean_search_string(title)
        current_url = BASE_SEARCH_URL.format(encoded)

        results: list[MetadataItem] = []
        seen_ids: set[str] = set()

        for _page in range(MAX_PAGES):
            if len(results) >= MAX_RESULTS:
                break

            try:
                response = await self._client.get(current_url, timeout=30.0)
                response.raise_for_status()
            except Exception:
                logger.exception("GayRado search failed for %s", current_url)
                break

            tree = lxml_html.fromstring(response.text)
            film_nodes = tree.xpath('//h2[contains(@class,"product-title")]')
            if not film_nodes:
                break

            for node in film_nodes:
                if len(results) >= MAX_RESULTS:
                    break

                href = _first_text(node.xpath('./a/@href'))
                title_text = _first_text(node.xpath('./a/text()'))
                if not href or not title_text or "/dvds/" not in href.lower():
                    continue

                film_url = _to_absolute_url(href)
                id_match = PRODUCT_ID_PATTERN.search(film_url)
                if not id_match:
                    continue
                product_id = id_match.group(1)
                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                self._movie_urls[product_id] = film_url

                film_title, studio = _parse_title_and_studio(title_text)
                rating_key = build_rating_key(self.source_key, product_id)
                guid = build_guid(provider_id, rating_key)

                results.append(
                    MetadataItem(
                        type="movie",
                        ratingKey=rating_key,
                        guid=guid,
                        title=film_title,
                        studio=studio,
                    )
                )

            next_link = _first_text(tree.xpath('//li[@id="pagination_next"]/a/@href'))
            if not next_link:
                break
            current_url = _to_absolute_url(next_link)

        logger.info("GayRado search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        film_url = self._movie_urls.get(source_id) or FALLBACK_DETAIL_URL.format(source_id)
        response = await self._client.get(film_url, timeout=45.0)
        response.raise_for_status()
        tree = lxml_html.fromstring(response.text)

        raw_title = _first_text(tree.xpath("//h1/text()")) or _first_text(tree.xpath("//title/text()")) or ""
        if raw_title and "|" in raw_title:
            raw_title = raw_title.split("|", 1)[0].strip()
        page_title, title_studio = _parse_title_and_studio(raw_title)

        desc_text = _extract_description_text(tree)
        summary = normalize_whitespace(desc_text.split("Running Time:", 1)[0])
        summary = re.sub(
            r"A note about barebacking:.*$",
            "",
            summary,
            flags=re.IGNORECASE,
        ).strip()

        runtime_text = _extract_labeled_block(desc_text, "Running Time:", ["Starring:", "Director:", "Studio:"])
        cast_blob = _extract_labeled_block(desc_text, "Starring:", ["Director:", "Studio:", "Categories:"])
        director_blob = _extract_labeled_block(desc_text, "Director:", ["A note about barebacking:", "Studio:", "Categories:"])
        studio = _extract_labeled_block(desc_text, "Studio:", ["Categories:"]) or title_studio
        genres_blob = _extract_labeled_block(desc_text, "Categories:", [])

        cast = _dedupe([item.strip() for item in (cast_blob or "").split(",") if item.strip()])
        directors = _dedupe([item.strip() for item in (director_blob or "").split(",") if item.strip()])
        genres = [
            item
            for item in _dedupe([part.strip() for part in (genres_blob or "").split(",") if part.strip()])
            if item.lower() not in GENRE_SKIP
        ]

        image_urls = _dedupe(
            tree.xpath(
                '//li[contains(@class,"thumb-container")]//img/@data-image-large-src'
                ' | //img[contains(@class,"js-qv-product-cover")]/@src'
                ' | //meta[@property="og:image"]/@content'
            )
        )
        poster = image_urls[0] if image_urls else None

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            Guid=[GuidItem(id=guid)],
            title=page_title or raw_title,
            studio=studio,
            summary=summary or None,
            duration=_parse_runtime_ms(runtime_text),
            contentRating="X",
            isAdult=True,
            thumb=_to_absolute_url(poster) if poster else None,
            Image=[
                ImageItem(
                    url=_to_absolute_url(url),
                    type="poster" if idx == 0 else "background",
                    alt=page_title or raw_title or f"GayRado image {idx + 1}",
                )
                for idx, url in enumerate(image_urls)
            ]
            or None,
            Genre=[GenreItem(tag=item) for item in genres] or None,
            Role=[RoleItem(tag=item) for item in cast] or None,
            Director=[DirectorItem(tag=item) for item in directors] or None,
            Collection=[CollectionItem(tag=studio)] if studio else None,
        )
