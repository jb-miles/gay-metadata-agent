from __future__ import annotations

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
from src.utils.text import normalize_whitespace

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gayeroticvideoindex.com"
TITLE_SUFFIX = ": Gay Erotic Video Index"

SCENE_ID_PATTERNS = (
    re.compile(r"\{(?P<id>\d{1,6}[A-Za-z]?)\}"),
    re.compile(r"\bgevi(?:\s*scene)?\s*[:#-]?\s*(?P<id>\d{1,6}[A-Za-z]?)\b", re.IGNORECASE),
    re.compile(r"\bepisode\s*[:#-]?\s*(?P<id>\d{1,6}[A-Za-z]?)\b", re.IGNORECASE),
)

ACTION_MAP = {
    "O": "Oral Sex",
    "A": "Anal Sex",
    "R": "Rimming",
}


def _extract_candidate_ids(title: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    for pattern in SCENE_ID_PATTERNS:
        for match in pattern.finditer(title):
            value = match.group("id")
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(value)

    stripped = title.strip()
    if re.fullmatch(r"\d{1,6}[A-Za-z]?", stripped):
        key = stripped.lower()
        if key not in seen:
            candidates.append(stripped)

    return candidates


def _clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    title = raw.strip()
    if title.endswith(TITLE_SUFFIX):
        title = title[: -len(TITLE_SUFFIX)]
    return normalize_whitespace(title) or None


def _to_absolute_url(value: str) -> str:
    if value.startswith("http"):
        return value
    return BASE_URL + "/" + value.lstrip("/")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
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


class GEVIScenesScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @property
    def source_key(self) -> str:
        return "geviscenes"

    @property
    def source_name(self) -> str:
        return "GEVI Scenes"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id

        candidate_ids = _extract_candidate_ids(title)
        if not candidate_ids:
            return []

        results: list[MetadataItem] = []
        for candidate_id in candidate_ids:
            match = await self._fetch_scene_match(candidate_id, provider_id)
            if not match:
                continue
            if year and match.year and match.year != year:
                continue
            results.append(match)

        logger.info("GEVI Scenes search for %r returned %d results", title, len(results))
        return results

    async def _fetch_scene_match(self, source_id: str, provider_id: str) -> MetadataItem | None:
        url = f"{BASE_URL}/episode/{source_id}"
        try:
            response = await self._client.get(
                url,
                timeout=30.0,
                headers={"Referer": "https://gayeroticvideoindex.com/search"},
            )
            response.raise_for_status()
        except Exception:
            logger.debug("GEVI Scenes lookup failed for %s", source_id)
            return None

        tree = lxml_html.fromstring(response.text)
        title = _clean_title(_first_text(tree.xpath("//title/text()")))
        if not title or title.lower() == "not found":
            return None

        studio = _first_text(tree.xpath('//a[contains(@href, "company/")]/text()'))

        date_raw = _first_text(
            tree.xpath(
                '//div[span[contains(normalize-space(), "Date:")]]/text()[normalize-space()]'
                '|//span[contains(normalize-space(), "Date:")]/following-sibling::text()[normalize-space()]'
            )
        )
        release_date = _parse_date(date_raw)

        image = _first_text(tree.xpath('//img[contains(@src, "Episodes/")]/@src'))

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            title=title,
            year=release_date.year if release_date else None,
            studio=studio,
            thumb=_to_absolute_url(image) if image else None,
        )

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        url = f"{BASE_URL}/episode/{source_id}"
        response = await self._client.get(
            url,
            timeout=30.0,
            headers={"Referer": "https://gayeroticvideoindex.com/search"},
        )
        response.raise_for_status()

        tree = lxml_html.fromstring(response.text)

        title = _clean_title(_first_text(tree.xpath("//title/text()")))
        if not title or title.lower() == "not found":
            raise ValueError(f"GEVI scene not found: {source_id}")

        studio = _first_text(tree.xpath('//a[contains(@href, "company/")]/text()'))

        synopsis_parts = [
            value.strip()
            for value in tree.xpath('//div[contains(@class, "wideCols-1")]//text()')
            if value and value.strip()
        ]
        synopsis = normalize_whitespace(" ".join(synopsis_parts)) if synopsis_parts else ""

        date_raw = _first_text(
            tree.xpath(
                '//div[span[contains(normalize-space(), "Date:")]]/text()[normalize-space()]'
                '|//span[contains(normalize-space(), "Date:")]/following-sibling::text()[normalize-space()]'
            )
        )
        release_date = _parse_date(date_raw)

        cast = _dedupe(
            tree.xpath('//a[contains(@href, "performer/")]//span/text()')
            + tree.xpath('//a[contains(@href, "performer/")]/text()')
        )

        action_codes = _dedupe(
            [
                "".join(row.xpath("./td[3]//text()")).strip()
                for row in tree.xpath("//table/tbody/tr")
                if row.xpath("./td[3]")
            ]
        )

        genres: list[str] = []
        for code in action_codes:
            for key, label in ACTION_MAP.items():
                if key in code and label not in genres:
                    genres.append(label)

        image_candidates = _dedupe([
            _to_absolute_url(value)
            for value in tree.xpath('//img[contains(@src, "Episodes/")]/@src')
            if value
        ])

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        image_items: list[ImageItem] = []
        for image_url in image_candidates[: settings.artwork_max_posters]:
            image_items.append(ImageItem(alt=title, type="coverPoster", url=image_url))

        background_candidates = image_candidates[1:] if len(image_candidates) > 1 else image_candidates[:1]
        for image_url in background_candidates[: settings.artwork_max_backgrounds]:
            image_items.append(ImageItem(alt=title, type="background", url=image_url))

        collection_items = [CollectionItem(tag=studio)] if studio else None

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            title=title,
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
