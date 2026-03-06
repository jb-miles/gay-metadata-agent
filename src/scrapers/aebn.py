from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime

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

BASE_URL = "https://gay.aebn.com"
BASE_SEARCH_URL = (
    BASE_URL
    + "/gay/search?queryType=Free+Form&sysQuery={0}&criteria=%7B%22sort%22%3A%22Relevance%22%7D"
)

MOVIE_ID_PATTERN = re.compile(r"/movies/(\d+)")
MAX_RESULTS = 20
MAX_PAGES = 10


def _clean_search_string(title: str) -> str:
    value = strip_diacritics(title.lower().strip())
    encoded = urllib.parse.quote(value)
    return encoded.replace("%25", "%").replace("*", "")


def _to_absolute_url(value: str) -> str:
    if not value:
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return BASE_URL + value
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


def _parse_release_date(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Released:", "").strip()
    raw = raw.replace("Sept", "Sep").replace("July", "Jul")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_duration_ms(value: str | None) -> int | None:
    if not value:
        return None

    raw = value.strip().replace("Running Time:", "").strip()
    if not raw:
        return None

    try:
        parts = [int(part) for part in raw.split(":")]
    except ValueError:
        return None

    if len(parts) == 2:
        minutes, seconds = parts
        hours = 0
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        return None

    total_seconds = hours * 3600 + minutes * 60 + seconds
    return total_seconds * 1000


def _clean_page_title(raw: str | None) -> str | None:
    if not raw:
        return None

    title = raw.strip()
    if "|" in title:
        title = title.split("|", 1)[0].strip()
    if title.lower().startswith("watch "):
        title = title[6:].strip()
    return title or None


class AEBNScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self._movie_urls: dict[str, str] = {}

    @property
    def source_key(self) -> str:
        return "aebn"

    @property
    def source_name(self) -> str:
        return "AEBN"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id

        encoded = _clean_search_string(title)
        search_url = BASE_SEARCH_URL.format(encoded)

        results: list[MetadataItem] = []
        current_url = search_url

        for _page in range(MAX_PAGES):
            if len(results) >= MAX_RESULTS:
                break

            try:
                response = await self._client.get(
                    current_url,
                    timeout=30.0,
                    headers={"Cookie": "ageGated=1"},
                )
                response.raise_for_status()
            except Exception:
                logger.exception("AEBN search request failed for %s", current_url)
                break

            tree = lxml_html.fromstring(response.text)

            film_nodes = tree.xpath(
                '//div[contains(@class, "dts-collection-item-movie")][@id]'
                '//div[contains(@id, "dtsImageOverlayContainer")]'
            )
            if not film_nodes:
                break

            for node in film_nodes:
                if len(results) >= MAX_RESULTS:
                    break

                href = _first_text(node.xpath('.//a[contains(@href, "/movies/")]/@href'))
                if not href:
                    continue

                title_text = _first_text(node.xpath('.//a//img/@title'))
                if not title_text:
                    title_text = _first_text(node.xpath('.//a[contains(@href, "/movies/")]//text()'))
                if not title_text:
                    continue

                matched_id = MOVIE_ID_PATTERN.search(href)
                if not matched_id:
                    continue

                movie_id = matched_id.group(1)
                self._movie_urls[movie_id] = _to_absolute_url(href)
                poster = _first_text(node.xpath('.//img/@src'))

                rating_key = build_rating_key(self.source_key, movie_id)
                guid = build_guid(provider_id, rating_key)

                match = MetadataItem(
                    type="movie",
                    ratingKey=rating_key,
                    guid=guid,
                    title=normalize_whitespace(title_text),
                    year=None,
                    thumb=_to_absolute_url(poster) if poster else None,
                )
                results.append(match)

            next_link = _first_text(
                tree.xpath(
                    '//ul[contains(@class, "dts-pagination")]/li[contains(@class, "active")]'
                    '/following-sibling::li[1]/a[contains(@class, "dts-paginator-tagging")]/@href'
                )
            )
            if not next_link:
                break

            current_url = _to_absolute_url(next_link)

        logger.info("AEBN search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        tree = await self._load_movie_tree(source_id)

        page_title = _clean_page_title(_first_text(tree.xpath("//title/text()")))

        studio_candidates = _dedupe(tree.xpath('//li[contains(@class, "section-detail-list-item-studio")]//a/text()'))
        studio = studio_candidates[0] if studio_candidates else None

        synopsis_parts = [x.strip() for x in tree.xpath('//div[contains(@class, "dts-section-page-detail-description-body")]//text()') if x.strip()]
        synopsis = normalize_whitespace(" ".join(synopsis_parts)) if synopsis_parts else ""

        release_raw = _first_text(
            tree.xpath(
                '//li[contains(@class, "section-detail-list-item-release-date")]/text()[normalize-space()]'
            )
        )
        release_date = _parse_release_date(release_raw)

        duration_raw = _first_text(
            tree.xpath(
                '//li[.//span[contains(normalize-space(), "Running Time:")]]/text()[normalize-space()]'
            )
        )
        duration_ms = _parse_duration_ms(duration_raw)

        cast = _dedupe(
            tree.xpath('//a[contains(@class, "dts-movie-star-wrapper")]//span/text()')
            + tree.xpath('//div[contains(@class, "dts-star-name-overlay")]/text()')
        )
        directors = _dedupe(tree.xpath('//li[contains(@class, "section-detail-list-item-director")]//a//span/text()'))

        raw_genres = _dedupe(
            [x.replace(",", "") for x in tree.xpath('//span[contains(@class, "dts-image-display-name")]/text()')]
            + [x.replace(",", "") for x in tree.xpath('//a[contains(@href, "sexActFilters")]/text()')]
        )

        series = _dedupe(tree.xpath('//li[contains(@class, "section-detail-list-item-series")]//a/text()'))

        image_candidates = _dedupe([
            _to_absolute_url(value.replace("=293", "=1000"))
            for value in tree.xpath('//*[contains(@class,"dts-movie-boxcover")]//img/@src')
            if value
        ])

        release_year = release_date.year if release_date else None
        release_iso = release_date.strftime("%Y-%m-%d") if release_date else None

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        image_items: list[ImageItem] = []
        for image_url in image_candidates[: settings.artwork_max_posters]:
            image_items.append(ImageItem(alt=page_title or "", type="coverPoster", url=image_url))

        background_candidates = image_candidates[1:] if len(image_candidates) > 1 else image_candidates[:1]
        for image_url in background_candidates[: settings.artwork_max_backgrounds]:
            image_items.append(ImageItem(alt=page_title or "", type="background", url=image_url))

        collection_items: list[CollectionItem] = []
        if studio:
            collection_items.append(CollectionItem(tag=studio))
        collection_items.extend(CollectionItem(tag=item) for item in series)

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
            Genre=[GenreItem(tag=item) for item in raw_genres] or None,
            Role=[RoleItem(tag=item, role="Performer") for item in cast] or None,
            Director=[DirectorItem(tag=item) for item in directors] or None,
            Collection=collection_items or None,
            Guid=[GuidItem(id=guid)],
        )

    async def _load_movie_tree(self, source_id: str):
        headers = {"Cookie": "ageGated=1"}

        initial_url = self._movie_urls.get(source_id) or f"{BASE_URL}/gay/movies/{source_id}"
        response = await self._client.get(initial_url, timeout=45.0, headers=headers)
        response.raise_for_status()

        tree = lxml_html.fromstring(response.text)
        page_title = _first_text(tree.xpath("//title/text()"))
        if page_title and page_title.lower() != "age gate page":
            return tree

        final_url = str(response.url)
        resolved = _extract_gate_target(final_url)
        if not resolved:
            raise ValueError(f"AEBN movie page blocked by age gate for id={source_id}")

        self._movie_urls[source_id] = resolved
        retry = await self._client.get(resolved, timeout=45.0, headers=headers)
        retry.raise_for_status()
        return lxml_html.fromstring(retry.text)


def _extract_gate_target(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if "/avs/gate" not in parsed.path:
        return None
    target = urllib.parse.parse_qs(parsed.query).get("f", [None])[0]
    if not target:
        return None
    decoded = urllib.parse.unquote(target)
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return decoded
    return BASE_URL + decoded


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None
