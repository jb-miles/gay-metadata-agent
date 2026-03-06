from __future__ import annotations

import logging

import httpx

from src.config import get_settings
from src.models.metadata import GuidItem, MetadataItem
from src.scrapers.base import BaseScraper
from src.utils.guid import build_guid, build_rating_key, parse_rating_key

logger = logging.getLogger(__name__)

FILM_SOURCES = (
    "gevi",
    "aebn",
    "gayempire",
    "gayhotmovies",
    "gayworld",
    "gaymovie",
    "hfgpm",
)


class GayAdultFilmsScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @property
    def source_key(self) -> str:
        return "gayadultfilms"

    @property
    def source_name(self) -> str:
        return "Gay Adult Films"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        from src.scrapers import get_scraper

        settings = get_settings()
        results: list[MetadataItem] = []

        for source_key in FILM_SOURCES:
            if not settings.is_scraper_enabled(source_key):
                continue
            scraper = get_scraper(source_key)
            if scraper is None:
                continue
            try:
                matches = await scraper.search(title, year)
            except Exception:
                logger.exception("GayAdultFilms delegate search failed for %s", source_key)
                continue

            for match in matches:
                wrapped = self._wrap_match(match)
                if wrapped is not None:
                    results.append(wrapped)

        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        from src.scrapers import get_scraper

        source_key, delegate_id = _split_delegate_source(source_id)

        scraper = get_scraper(source_key)
        if scraper is None:
            raise ValueError(f"No delegate scraper registered for {source_key}")

        metadata = await scraper.get_metadata(delegate_id)
        provider_id = get_settings().provider_id

        wrapped_rating_key = build_rating_key(self.source_key, source_id)
        wrapped_guid = build_guid(provider_id, wrapped_rating_key)

        return metadata.model_copy(
            update={
                "ratingKey": wrapped_rating_key,
                "guid": wrapped_guid,
                "Guid": [GuidItem(id=wrapped_guid)],
            }
        )

    def _wrap_match(self, match: MetadataItem) -> MetadataItem | None:
        if not match.ratingKey:
            return None

        parsed = parse_rating_key(match.ratingKey)
        delegate_key = f"{parsed.source}:{parsed.source_id}"

        provider_id = get_settings().provider_id
        wrapped_rating_key = build_rating_key(self.source_key, delegate_key)
        wrapped_guid = build_guid(provider_id, wrapped_rating_key)

        return match.model_copy(
            update={
                "ratingKey": wrapped_rating_key,
                "guid": wrapped_guid,
            }
        )


def _split_delegate_source(source_id: str) -> tuple[str, str]:
    source_key, sep, delegate_id = source_id.partition(":")
    if not sep or not source_key or not delegate_id:
        raise ValueError(f"Invalid GayAdultFilms source id: {source_id}")
    return source_key, delegate_id
