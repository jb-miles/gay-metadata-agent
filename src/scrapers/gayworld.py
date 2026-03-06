from __future__ import annotations

import base64
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

BASE_URL = "https://gay-world.org"
BASE_SEARCH_URL = BASE_URL + "/?s={0}"
MAX_RESULTS = 20
MAX_PAGES = 10


def _clean_search_string(title: str) -> str:
    value = title.lower().strip()
    value = strip_diacritics(value)
    encoded = urllib.parse.quote(value)
    return encoded.replace("%25", "%").replace("%26", "").replace("*", "")


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


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None


class GayWorldScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @property
    def source_key(self) -> str:
        return "gayworld"

    @property
    def source_name(self) -> str:
        return "Gay World"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        encoded = _clean_search_string(title)

        results: list[MetadataItem] = []
        current_url = BASE_SEARCH_URL.format(encoded)

        for _page in range(MAX_PAGES):
            if len(results) >= MAX_RESULTS:
                break

            try:
                response = await self._client.get(current_url, timeout=30.0)
                response.raise_for_status()
            except Exception:
                logger.exception("GayWorld search failed for %s", current_url)
                break

            tree = lxml_html.fromstring(response.text)

            # Find result links, filter to /movies/ URLs
            result_links = tree.xpath(
                '//div[contains(@class,"fusion-post-content-wrapper")]//h2/a'
            )
            film_links = [
                link for link in result_links
                if "/movies/" in (link.get("href") or "")
            ]
            if not film_links:
                break

            for link in film_links:
                if len(results) >= MAX_RESULTS:
                    break

                film_title = normalize_whitespace(link.text_content())
                if not film_title:
                    continue

                href = link.get("href")
                if not href:
                    continue
                film_url = _to_absolute_url(href)
                source_id = _encode_source_id(film_url)

                # Try to find a thumbnail from the parent article
                parent = link.getparent()
                thumb = None
                if parent is not None:
                    grand = parent.getparent()
                    if grand is not None:
                        thumb = _first_text(grand.xpath('.//img/@src'))

                rating_key = build_rating_key(self.source_key, source_id)
                guid = build_guid(provider_id, rating_key)

                results.append(
                    MetadataItem(
                        type="movie",
                        ratingKey=rating_key,
                        guid=guid,
                        title=film_title,
                        thumb=_to_absolute_url(thumb) if thumb else None,
                    )
                )

            next_link = _first_text(tree.xpath('//a[@class="pagination-next"]/@href'))
            if not next_link:
                break
            current_url = _to_absolute_url(next_link)

        logger.info("GayWorld search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        film_url = _decode_source_id(source_id)
        film_url = _to_absolute_url(film_url)

        response = await self._client.get(film_url, timeout=45.0)
        response.raise_for_status()
        tree = lxml_html.fromstring(response.text)

        # Title — prefer page h1
        page_title = _first_text(tree.xpath('//main//h1/text()'))
        if not page_title:
            page_title = _first_text(tree.xpath('//h1[contains(@class,"entry-title")]/text()'))
        if not page_title:
            page_title = _first_text(tree.xpath('//title/text()'))
            if page_title and "|" in page_title:
                page_title = page_title.split("|")[0].strip()

        # Studio
        studio = _first_text(tree.xpath('//strong[contains(.,"Studio:")]//a//text()'))
        if not studio:
            studio = _first_text(tree.xpath('//strong[contains(.,"Studio:")]/following-sibling::text()'))
            if studio:
                studio = studio.strip().strip(":").strip()

        # Cast — look for actors/cast label
        cast: list[str] = []
        cast_links = tree.xpath('//strong[contains(.,"Actors") or contains(.,"Cast")]/following-sibling::a/text()')
        if cast_links:
            cast = _dedupe(cast_links)
        else:
            # Try inline text after Actors label
            cast_text = _first_text(
                tree.xpath('//strong[contains(.,"Actors") or contains(.,"Cast")]/following-sibling::text()')
            )
            if cast_text:
                # Strip leading colon/whitespace — text node is ": Name1, Name2, ..."
                cast_text = cast_text.lstrip(":").strip()
                cast = _dedupe([name.strip() for name in cast_text.split(",") if name.strip()])

        # Synopsis — main content paragraphs
        content_nodes = tree.xpath(
            '//div[contains(@class,"entry-content") or contains(@class,"post-content")]//p'
        )
        synopsis_parts: list[str] = []
        for node in content_nodes:
            text = normalize_whitespace(node.text_content())
            if text and not text.startswith("Studio:") and not text.startswith("Actors"):
                synopsis_parts.append(text)
        synopsis = "\n".join(synopsis_parts)
        synopsis = normalize_whitespace(synopsis)

        # Genres — from WordPress categories/tags
        raw_genres = _dedupe(
            tree.xpath('//a[contains(@href,"/category/") or contains(@href,"/tag/")]/text()')
        )
        # Filter out generic navigation terms
        raw_genres = [
            g for g in raw_genres
            if g.lower() not in {"movies", "home", "uncategorized", "gay world"}
        ]

        # Directors
        directors: list[str] = []
        director_links = tree.xpath(
            '//strong[contains(.,"Director")]/following-sibling::a/text()'
        )
        if director_links:
            directors = _dedupe(director_links)
        else:
            director_text = _first_text(
                tree.xpath('//strong[contains(.,"Director")]/following-sibling::text()')
            )
            if director_text:
                director_text = director_text.lstrip(":").strip()
                directors = _dedupe([d.strip() for d in director_text.split(",") if d.strip()])

        # Images — featured image and content images
        image_urls: list[str] = []
        featured = _first_text(tree.xpath('//div[contains(@class,"featured-image")]//img/@src'))
        if featured:
            image_urls.append(_to_absolute_url(featured))
        content_imgs = tree.xpath(
            '//div[contains(@class,"entry-content") or contains(@class,"post-content")]//img/@src'
        )
        for img in content_imgs:
            abs_url = _to_absolute_url(img)
            if abs_url not in image_urls:
                image_urls.append(abs_url)

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
            summary=synopsis or None,
            studio=studio,
            contentRating="X",
            isAdult=True,
            Image=image_items or None,
            Genre=[GenreItem(tag=g) for g in raw_genres] or None,
            Role=[RoleItem(tag=c, role="Performer") for c in cast] or None,
            Director=[DirectorItem(tag=d) for d in directors] or None,
            Collection=collection_items or None,
            Guid=[GuidItem(id=guid)],
        )
