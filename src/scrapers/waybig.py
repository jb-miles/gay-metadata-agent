from __future__ import annotations

import base64
import logging
import re
import urllib.parse
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

BASE_URL = "https://www.waybig.com"
BASE_SEARCH_URL = BASE_URL + "/blog/index.php?s={0}"
MAX_RESULTS = 20
MAX_PAGES = 10


def _clean_search_string(value: str) -> str:
    cleaned = value.lower().strip()

    cleaned = re.sub(r" - |- ", ": ", cleaned)
    cleaned = cleaned.replace(" & ", " ")
    cleaned = re.sub(r"['\"]", " ", cleaned)
    cleaned = normalize_whitespace(cleaned)

    if len(cleaned) > 50:
        cutoff = cleaned[:51].rfind(" ")
        if cutoff > 0:
            cleaned = cleaned[:cutoff]
        else:
            cleaned = cleaned[:50]

    cleaned = strip_diacritics(cleaned)
    encoded = urllib.parse.quote(cleaned.strip())
    return encoded.replace("%25", "%").replace("*", "").replace("%2A", "+")


def _normalize_url(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    return BASE_URL.rstrip("/") + "/" + value.lstrip("/")


def _encode_source_id(url: str) -> str:
    payload = url.encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_source_id(source_id: str) -> str:
    try:
        padding = "=" * ((4 - (len(source_id) % 4)) % 4)
        payload = base64.urlsafe_b64decode(source_id + padding)
        return payload.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Invalid WayBig source id: {source_id}") from exc


def _parse_release_date(value: str | None) -> datetime | None:
    if not value:
        return None

    raw = normalize_whitespace(value)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = normalize_whitespace(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _split_entry(entry: str) -> tuple[str | None, str]:
    film_entry = normalize_whitespace(entry)
    if not film_entry:
        return None, ""

    film_title = film_entry
    film_studio: str | None = None

    if re.search(r" at ", film_entry, flags=re.IGNORECASE):
        film_title, film_studio = re.split(r" at ", film_entry, maxsplit=1, flags=re.IGNORECASE)
    elif ": " in film_entry:
        film_studio, film_title = film_entry.split(": ", 1)
    elif re.search(r" on ", film_entry, flags=re.IGNORECASE):
        film_title, film_studio = re.split(r" on ", film_entry, maxsplit=1, flags=re.IGNORECASE)
    elif "? " in film_entry:
        film_studio, film_title = film_entry.split("? ", 1)
    elif ", " in film_entry:
        film_studio, film_title = film_entry.split(", ", 1)

    return normalize_whitespace(film_studio or "") or None, normalize_whitespace(film_title)


def _classify_tags(tags: list[str], studio: str | None, _title: str) -> tuple[list[str], list[str]]:
    cast: list[str] = []
    genres: list[str] = []

    studio_key = _norm_token(studio or "")
    invalid_chars = set('!;:",#$%^&*_~+?')

    for raw_tag in _dedupe(tags):
        tag = raw_tag[:-1] if raw_tag.endswith("'") else raw_tag
        lower = tag.lower()

        if (
            "compilation" in lower
            or "movie" in lower
            or "series" in lower
            or ".tv" in lower
            or ".com" in lower
            or ".net" in lower
        ):
            continue

        if any(ch in tag for ch in invalid_chars):
            continue

        tag_key = _norm_token(tag)
        if studio_key and tag_key == studio_key:
            continue
        if studio_key and studio_key in tag_key:
            continue

        words = tag.split()
        if 0 < len(words) <= 3 and all(re.fullmatch(r"[A-Za-z0-9'.-]+", part) for part in words):
            cast.append(tag)
        else:
            genres.append(tag)

    return _dedupe(cast), _dedupe(genres)


def _norm_token(value: str) -> str:
    lowered = strip_diacritics(value.lower())
    lowered = re.sub(r"[^a-z0-9]+", "", lowered)
    return lowered


class WayBigScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @property
    def source_key(self) -> str:
        return "waybig"

    @property
    def source_name(self) -> str:
        return "WayBig"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id

        query = _clean_search_string(title)
        search_url = BASE_SEARCH_URL.format(query)

        results: list[MetadataItem] = []
        current_url = search_url

        for _page in range(MAX_PAGES):
            if len(results) >= MAX_RESULTS:
                break

            try:
                response = await self._client.get(current_url, timeout=30.0)
                response.raise_for_status()
            except Exception:
                logger.exception("WayBig search request failed for %s", current_url)
                break

            tree = lxml_html.fromstring(response.text)
            articles = tree.xpath('//div[@class="row"]/div[contains(@class,"content-col")]/article')
            if not articles:
                break

            for article in articles:
                if len(results) >= MAX_RESULTS:
                    break

                entry = _first_text(article.xpath('.//a//*[contains(@class, "entry-title")]/text()'))
                if not entry:
                    continue

                studio, parsed_title = _split_entry(entry)
                if not parsed_title:
                    parsed_title = entry

                href = _first_text(article.xpath('.//a[@rel="bookmark"]/@href'))
                if not href:
                    continue

                article_url = _normalize_url(href)
                source_id = _encode_source_id(article_url)

                date_raw = _first_text(article.xpath('.//span[contains(@class,"meta-date")]/strong/text()'))
                release_date = _parse_release_date(date_raw)

                thumb = _first_text(article.xpath('.//img[contains(@src, "zing.waybig.com/reviews")]/@src'))

                match_year = release_date.year if release_date else None
                if year and match_year and year != match_year:
                    continue

                rating_key = build_rating_key(self.source_key, source_id)
                guid = build_guid(provider_id, rating_key)

                results.append(
                    MetadataItem(
                        type="movie",
                        ratingKey=rating_key,
                        guid=guid,
                        title=parsed_title,
                        year=match_year,
                        studio=studio,
                        thumb=_normalize_url(thumb) if thumb else None,
                    )
                )

            next_link = _first_text(tree.xpath('//div[contains(@class, "nav-links")]/a[contains(@class, "next")]/@href'))
            if not next_link:
                break
            current_url = _normalize_url(next_link)

        logger.info("WayBig search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        article_url = _decode_source_id(source_id)
        article_url = _normalize_url(article_url)

        response = await self._client.get(article_url, timeout=45.0)
        response.raise_for_status()

        tree = lxml_html.fromstring(response.text)

        entry_title = _first_text(tree.xpath('//h1[contains(@class, "entry-title")]/text()'))
        studio, parsed_title = _split_entry(entry_title or "")
        if not parsed_title:
            parsed_title = entry_title or ""

        synopsis_nodes = tree.xpath(
            '//div[contains(@class, "entry-content")]/p'
            '[not(descendant::script) and not(contains(., "Watch as"))]'
        )
        synopsis_parts = [normalize_whitespace(node.text_content()) for node in synopsis_nodes if node is not None]
        synopsis_parts = [part for part in synopsis_parts if part]
        synopsis = "\n".join(synopsis_parts)
        synopsis = re.sub(r"Watch.*at.*", "", synopsis, flags=re.IGNORECASE)
        synopsis = normalize_whitespace(synopsis)

        tag_values = tree.xpath('//a[contains(@href, "/blog/tag/")]/text()')
        cast, genres = _classify_tags(tag_values, studio, parsed_title)

        date_raw = _first_text(tree.xpath('//span[contains(@class,"meta-date")]/strong/text()'))
        release_date = _parse_release_date(date_raw)

        image_values = _dedupe(
            [_normalize_url(value) for value in tree.xpath('//img[contains(@src, "zing.waybig.com/reviews")]/@src') if value]
        )

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        image_items: list[ImageItem] = []
        for image_url in image_values[: settings.artwork_max_posters]:
            image_items.append(ImageItem(alt=parsed_title, type="coverPoster", url=image_url))

        background_values = image_values[1:] if len(image_values) > 1 else image_values[:1]
        for image_url in background_values[: settings.artwork_max_backgrounds]:
            image_items.append(ImageItem(alt=parsed_title, type="background", url=image_url))

        collection_items = [CollectionItem(tag=studio)] if studio else None

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            title=parsed_title or None,
            year=release_date.year if release_date else None,
            originallyAvailableAt=release_date.strftime("%Y-%m-%d") if release_date else None,
            summary=synopsis or None,
            studio=studio,
            contentRating="X",
            isAdult=True,
            Image=image_items or None,
            Genre=[GenreItem(tag=item) for item in genres] or None,
            Role=[RoleItem(tag=item, role="Performer") for item in cast] or None,
            Collection=collection_items,
            Guid=[GuidItem(id=guid)],
        )


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None
