from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from src.config import get_settings
from src.models.metadata import MetadataItem
from src.scrapers import get_scraper
from src.utils.cache import TTLCache
from src.utils.text import strip_diacritics

logger = logging.getLogger(__name__)
MAX_RESULTS = 60


@dataclass(frozen=True)
class SearchHints:
    search_title: str
    search_year: int | None
    preferred_studio: str | None


class MatchService:
    def __init__(self) -> None:
        settings = get_settings()
        self._cache = TTLCache(settings.cache_search_ttl_seconds)

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        if not title:
            return []

        hints = _parse_search_hints(title=title, year=year)
        cache_key = (hints.search_title.lower().strip(), hints.search_year)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Search cache hit for %r", cache_key)
            return cached

        settings = get_settings()
        results: list[MetadataItem] = []

        for source_key in settings.search_order:
            if not settings.is_scraper_enabled(source_key):
                continue
            scraper = get_scraper(source_key)
            if scraper is None:
                continue
            try:
                matches = await scraper.search(hints.search_title, hints.search_year)
                filtered = [m for m in matches if _passes_match_gates(m, hints)]
                results.extend(filtered)
            except Exception:
                logger.exception("Search failed for scraper %s", source_key)

        source_priority = {source: idx for idx, source in enumerate(settings.search_order)}
        deduped = _dedupe_results(results)
        deduped.sort(
            key=lambda item: (
                -_score_result(item, hints, source_priority),
                source_priority.get(_rating_key_source(item.ratingKey), 10_000),
                _normalize_key(item.title),
            )
        )
        deduped = deduped[:MAX_RESULTS]
        self._cache.set(cache_key, deduped)
        return deduped


def _dedupe_results(results: list[MetadataItem]) -> list[MetadataItem]:
    deduped: list[MetadataItem] = []
    index_by_key: dict[str, int] = {}
    seen_rating_keys: set[str] = set()

    for result in results:
        if result.ratingKey and result.ratingKey in seen_rating_keys:
            continue
        if result.ratingKey:
            seen_rating_keys.add(result.ratingKey)

        dedupe_key = _build_dedupe_key(result)
        if dedupe_key not in index_by_key:
            index_by_key[dedupe_key] = len(deduped)
            deduped.append(result)
            continue

        idx = index_by_key[dedupe_key]
        deduped[idx] = _merge_results(deduped[idx], result)

    return deduped


def _build_dedupe_key(result: MetadataItem) -> str:
    title_key = _normalize_key(result.title)
    year = result.year
    studio_key = _normalize_key(result.studio)

    if title_key and year:
        return f"title-year:{title_key}:{year}"
    if title_key and studio_key:
        return f"title-studio:{title_key}:{studio_key}"
    if title_key:
        return f"title:{title_key}"
    if result.ratingKey:
        return f"rating:{result.ratingKey}"
    return "unknown"


def _normalize_key(value: str | None) -> str:
    if not value:
        return ""

    lowered = strip_diacritics(value.lower())
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _merge_results(primary: MetadataItem, secondary: MetadataItem) -> MetadataItem:
    updates: dict[str, object] = {}

    for field in ("thumb", "summary", "studio", "originallyAvailableAt"):
        if not getattr(primary, field, None) and getattr(secondary, field, None):
            updates[field] = getattr(secondary, field)

    if primary.year is None and secondary.year is not None:
        updates["year"] = secondary.year

    if updates:
        return primary.model_copy(update=updates)
    return primary


def _parse_search_hints(title: str, year: int | None) -> SearchHints:
    cleaned = title.strip()
    resolved_year = year

    # Scanner hints may include full filenames.
    cleaned = re.sub(r"\.(mp4|mkv|avi|mov|wmv|m4v)$", "", cleaned, flags=re.IGNORECASE)

    # Scene naming convention: "<Studio> [sc] <Title> (YYYY)"
    scene_match = re.match(r"^(.+?)\s+\[(?i:sc)\]\s+(.+)$", cleaned)
    if scene_match:
        cleaned = scene_match.group(2).strip()
    else:
        cleaned = cleaned.replace("[sc]", " ").replace("[SC]", " ")

    # Extract trailing year from "... (YYYY)" if request did not already include year.
    year_match = re.search(r"\((19\d{2}|20\d{2})\)\s*$", cleaned)
    if year_match:
        if resolved_year is None:
            resolved_year = int(year_match.group(1))
        cleaned = cleaned[: year_match.start()].strip()

    preferred_studio: str | None = None

    # Drop leading studio prefix from common dummy naming formats:
    # "<Studio> - <Title>" and "<Studio> [sc] <Title>".
    if " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        if left and right and len(left) <= 80:
            preferred_studio = left.strip()
            cleaned = right.strip()
    elif "  " in cleaned:
        # after removing [sc], scene naming can leave multiple spaces.
        cleaned = " ".join(cleaned.split())

    cleaned = cleaned.strip(" -:_")
    cleaned = " ".join(cleaned.split())

    return SearchHints(
        search_title=cleaned or title.strip(),
        search_year=resolved_year,
        preferred_studio=preferred_studio,
    )


def _rating_key_source(rating_key: str | None) -> str:
    if not rating_key or "-" not in rating_key:
        return ""
    return rating_key.split("-", 1)[0]


def _score_result(
    item: MetadataItem,
    hints: SearchHints,
    source_priority: dict[str, int],
) -> float:
    query_title = _normalize_key(hints.search_title)
    item_title = _normalize_key(item.title)
    if not query_title or not item_title:
        return 0.0

    score = 0.0
    if item_title == query_title:
        score += 100.0
    elif item_title.startswith(query_title) or query_title.startswith(item_title):
        score += 45.0
    elif query_title in item_title:
        score += 25.0

    similarity = SequenceMatcher(None, query_title, item_title).ratio()
    score += similarity * 50.0

    if hints.search_year is not None and item.year is not None:
        if item.year == hints.search_year:
            score += 30.0
        elif abs(item.year - hints.search_year) == 1:
            score += 10.0
        else:
            score -= min(abs(item.year - hints.search_year), 10)

    if hints.preferred_studio:
        preferred = _normalize_key(hints.preferred_studio)
        item_studio = _normalize_key(item.studio)
        if preferred and item_studio:
            if preferred == item_studio:
                score += 25.0
            elif preferred in item_studio or item_studio in preferred:
                score += 12.0

    source = _rating_key_source(item.ratingKey)
    score += max(0, 10 - source_priority.get(source, 10))
    return score


def _passes_match_gates(item: MetadataItem, hints: SearchHints) -> bool:
    query_title = _normalize_key(hints.search_title)
    item_title = _normalize_key(item.title)
    if not query_title or not item_title:
        return False

    title_ok = (
        item_title == query_title
        or query_title in item_title
        or item_title in query_title
        or SequenceMatcher(None, query_title, item_title).ratio() >= 0.82
    )
    if not title_ok:
        return False

    if hints.search_year is not None and item.year is not None:
        if abs(item.year - hints.search_year) > 3:
            return False

    return True
