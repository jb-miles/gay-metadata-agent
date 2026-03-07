from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from src.config import get_settings
from src.models.metadata import MetadataItem
from src.scrapers import get_scraper
from src.utils.audit import audit_event, summarize_match_item
from src.utils.cache import TTLCache
from src.utils.text import strip_diacritics

logger = logging.getLogger(__name__)
MAX_RESULTS = 60
STUDIO_SUFFIX_TOKENS = {
    "entertainment",
    "entertainments",
    "films",
    "media",
    "network",
    "pictures",
    "productions",
    "releasing",
    "studio",
    "studios",
    "video",
    "videos",
}


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
        audit_event("match_hints_parsed", hints=hints)
        cache_key = (hints.search_title.lower().strip(), hints.search_year)
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Search cache hit for %r", cache_key)
            audit_event("match_cache_hit", cache_key=cache_key, result_count=len(cached))
            return cached

        settings = get_settings()
        results = await self._search_sources(hints, settings)
        if not results:
            for fallback_hints in _derive_fallback_hints(hints):
                audit_event("match_fallback_hints_parsed", hints=fallback_hints)
                fallback_results = await self._search_sources(fallback_hints, settings)
                if fallback_results:
                    hints = fallback_hints
                    results = fallback_results
                    audit_event(
                        "match_fallback_selected",
                        hints=fallback_hints,
                        result_count=len(fallback_results),
                    )
                    break

        source_priority = {source: idx for idx, source in enumerate(settings.search_order)}
        deduped = _dedupe_results(results)
        scored: list[tuple[float, MetadataItem]] = []
        for item in deduped:
            score_details = _score_result(item, hints, source_priority)
            scored.append((score_details["total"], item))
            audit_event(
                "match_candidate_scored",
                candidate=summarize_match_item(item),
                score=score_details["total"],
                score_details=score_details,
            )
        deduped.sort(
            key=lambda item: (
                -_score_result(item, hints, source_priority)["total"],
                source_priority.get(_rating_key_source(item.ratingKey), 10_000),
                _normalize_key(item.title),
            )
        )
        deduped = deduped[:MAX_RESULTS]
        self._cache.set(cache_key, deduped)
        audit_event(
            "match_results_finalized",
            cache_key=cache_key,
            result_count=len(deduped),
            results=[summarize_match_item(item) for item in deduped],
        )
        return deduped

    async def _search_sources(
        self,
        hints: SearchHints,
        settings,
    ) -> list[MetadataItem]:
        results: list[MetadataItem] = []

        for source_key in settings.search_order:
            if not settings.is_scraper_enabled(source_key):
                audit_event("match_source_skipped", source_key=source_key, reason="disabled")
                continue
            scraper = get_scraper(source_key)
            if scraper is None:
                audit_event("match_source_skipped", source_key=source_key, reason="unregistered")
                continue
            try:
                audit_event(
                    "match_source_started",
                    source_key=source_key,
                    search_title=hints.search_title,
                    search_year=hints.search_year,
                )
                matches = await scraper.search(hints.search_title, hints.search_year)
                audit_event(
                    "match_source_raw_results",
                    source_key=source_key,
                    result_count=len(matches),
                    results=[summarize_match_item(item) for item in matches],
                )
                filtered: list[MetadataItem] = []
                for item in matches:
                    passed, reasons = _evaluate_match_gates(item, hints)
                    audit_event(
                        "match_candidate_gated",
                        source_key=source_key,
                        candidate=summarize_match_item(item),
                        passed=passed,
                        reasons=reasons,
                    )
                    if passed:
                        filtered.append(item)
                audit_event(
                    "match_source_filtered_results",
                    source_key=source_key,
                    result_count=len(filtered),
                    results=[summarize_match_item(item) for item in filtered],
                )
                results.extend(filtered)
            except Exception:
                logger.exception("Search failed for scraper %s", source_key)
                audit_event("match_source_failed", source_key=source_key)

        return results


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
        audit_event(
            "match_candidate_deduped",
            dedupe_key=dedupe_key,
            primary=summarize_match_item(deduped[idx]),
            secondary=summarize_match_item(result),
        )
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

    # Drop leading studio prefix from common scanner naming formats:
    # "<Studio> - <Title>" and "(<Studio>) - <Title>".
    if " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        if left and right:
            left = left.strip()
            right = right.strip()
            studio_candidate = left
            if left.startswith("(") and left.endswith(")"):
                studio_candidate = left[1:-1].strip()
            if studio_candidate and len(studio_candidate) <= 80:
                preferred_studio = studio_candidate
                cleaned = right
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


def _derive_fallback_hints(hints: SearchHints) -> list[SearchHints]:
    if hints.preferred_studio:
        return []

    tokens = hints.search_title.split()
    if len(tokens) < 3:
        return []

    ranked_fallbacks: list[tuple[int, SearchHints]] = []
    seen: set[tuple[str, str]] = set()
    max_prefix_words = min(4, len(tokens) - 1)

    for prefix_words in range(1, max_prefix_words + 1):
        studio_tokens = tokens[:prefix_words]
        title_tokens = tokens[prefix_words:]
        if len(title_tokens) < 1:
            continue
        if not _looks_like_studio_prefix(studio_tokens):
            continue

        preferred_studio = " ".join(studio_tokens).strip()
        search_title = " ".join(title_tokens).strip()
        dedupe_key = (
            _normalize_key(preferred_studio),
            _normalize_key(search_title),
        )
        if not preferred_studio or not search_title or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ranked_fallbacks.append(
            (
                _studio_prefix_priority(studio_tokens),
                SearchHints(
                    search_title=search_title,
                    search_year=hints.search_year,
                    preferred_studio=preferred_studio,
                ),
            )
        )

    ranked_fallbacks.sort(
        key=lambda item: (
            -item[0],
            -len(item[1].preferred_studio.split()),
            len(item[1].search_title.split()),
        )
    )
    return [fallback for _, fallback in ranked_fallbacks]


def _looks_like_studio_prefix(tokens: list[str]) -> bool:
    if not tokens:
        return False

    blocked_last_tokens = {"a", "an", "the", "of", "to", "for", "and"}
    last = tokens[-1]
    if last.lower() in blocked_last_tokens:
        return False

    for token in tokens:
        if not re.fullmatch(r"[A-Za-z0-9&+'-]+", token):
            return False
        if token[0].islower():
            return False

    return True


def _studio_prefix_priority(tokens: list[str]) -> int:
    priority = len(tokens)
    if tokens[-1].lower() in STUDIO_SUFFIX_TOKENS:
        priority += 100
    return priority


def _rating_key_source(rating_key: str | None) -> str:
    if not rating_key or "-" not in rating_key:
        return ""
    return rating_key.split("-", 1)[0]


def _score_result(
    item: MetadataItem,
    hints: SearchHints,
    source_priority: dict[str, int],
) -> dict[str, float]:
    query_title = _normalize_key(hints.search_title)
    item_title = _normalize_key(item.title)
    raw_query_title = _normalize_title_for_expansion(hints.search_title)
    raw_item_title = _normalize_title_for_expansion(item.title)
    if not query_title or not item_title:
        return {"total": 0.0}

    score = 0.0
    components: dict[str, float] = {}
    subtitle_expansion = _is_subtitle_expansion(raw_query_title, raw_item_title)
    if item_title == query_title:
        score += 100.0
        components["title_exact"] = 100.0
    elif subtitle_expansion:
        score += 105.0
        components["title_subtitle_expansion"] = 105.0
    elif item_title.startswith(query_title) or query_title.startswith(item_title):
        score += 45.0
        components["title_prefix"] = 45.0
    elif query_title in item_title:
        score += 25.0
        components["title_contains"] = 25.0

    similarity = SequenceMatcher(None, query_title, item_title).ratio()
    similarity_score = similarity * 50.0
    score += similarity_score
    components["title_similarity"] = similarity_score

    if hints.search_year is not None and item.year is not None:
        if item.year == hints.search_year:
            score += 30.0
            components["year_exact"] = 30.0
        elif abs(item.year - hints.search_year) == 1:
            score += 10.0
            components["year_near"] = 10.0
        else:
            penalty = float(min(abs(item.year - hints.search_year), 10))
            score -= penalty
            components["year_penalty"] = -penalty

    if item.year is not None:
        score += 12.0
        components["year_present"] = 12.0
    else:
        score -= 8.0
        components["year_missing"] = -8.0

    if hints.preferred_studio:
        preferred = _normalize_key(hints.preferred_studio)
        item_studio = _normalize_key(item.studio)
        if preferred and item_studio:
            if preferred == item_studio:
                score += 25.0
                components["studio_exact"] = 25.0
            elif preferred in item_studio or item_studio in preferred:
                score += 12.0
                components["studio_partial"] = 12.0
    elif item.studio:
        score += 4.0
        components["studio_present"] = 4.0

    source = _rating_key_source(item.ratingKey)
    source_bonus = float(max(0, 10 - source_priority.get(source, 10)))
    score += source_bonus
    components["source_priority"] = source_bonus
    components["total"] = score
    return components


def _evaluate_match_gates(item: MetadataItem, hints: SearchHints) -> tuple[bool, list[str]]:
    query_title = _normalize_key(hints.search_title)
    item_title = _normalize_key(item.title)
    if not query_title or not item_title:
        return False, ["missing_normalized_title"]

    title_ok = (
        item_title == query_title
        or query_title in item_title
        or item_title in query_title
        or SequenceMatcher(None, query_title, item_title).ratio() >= 0.82
    )
    if not title_ok:
        return False, ["title_gate_failed"]

    if hints.search_year is not None and item.year is not None:
        if abs(item.year - hints.search_year) > 3:
            return False, ["year_gate_failed"]

    return True, ["passed"]


def _is_subtitle_expansion(query_title: str, item_title: str) -> bool:
    if not query_title or not item_title or not item_title.startswith(query_title):
        return False
    if len(item_title) <= len(query_title):
        return False

    remainder = item_title[len(query_title) :].strip()
    if not remainder:
        return False

    return remainder.startswith((":", "-", "part ", "vol ", "volume "))


def _normalize_title_for_expansion(value: str | None) -> str:
    if not value:
        return ""

    lowered = strip_diacritics(value.lower())
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()
