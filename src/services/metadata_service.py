from __future__ import annotations

import logging

from src.config import get_settings
from src.models.metadata import MetadataItem
from src.scrapers import get_scraper
from src.utils.cache import TTLCache
from src.utils.audit import audit_event, summarize_metadata
from src.utils.guid import parse_rating_key

logger = logging.getLogger(__name__)


class MetadataService:
    def __init__(self) -> None:
        settings = get_settings()
        self._cache = TTLCache(settings.cache_metadata_ttl_seconds)

    async def get(self, rating_key: str) -> MetadataItem:
        cached = self._cache.get(rating_key)
        if cached is not None:
            logger.debug("Metadata cache hit for %s", rating_key)
            audit_event("metadata_cache_hit", rating_key=rating_key)
            return cached

        parsed = parse_rating_key(rating_key)
        audit_event(
            "metadata_lookup_parsed",
            rating_key=rating_key,
            source=parsed.source,
            source_id=parsed.source_id,
        )
        scraper = get_scraper(parsed.source)
        if scraper is None:
            raise ValueError(f"No scraper registered for source: {parsed.source}")

        audit_event("metadata_fetch_started", rating_key=rating_key, source=parsed.source)
        metadata = await scraper.get_metadata(parsed.source_id)
        self._cache.set(rating_key, metadata)
        audit_event(
            "metadata_fetch_finished",
            rating_key=rating_key,
            source=parsed.source,
            metadata=summarize_metadata(metadata),
        )
        return metadata
