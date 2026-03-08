from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re

from src.config import get_settings
from src.models.metadata import (
    ChapterItem,
    CollectionItem,
    DirectorItem,
    GenreItem,
    GuidItem,
    ImageItem,
    MetadataItem,
    ProducerItem,
    RoleItem,
)
from src.scrapers import get_scraper
from src.utils.audit import audit_event, summarize_match_item, summarize_metadata
from src.utils.guid import build_guid, parse_rating_key
from src.utils.text import strip_diacritics

logger = logging.getLogger(__name__)

MOVIE_ENRICHMENT_SOURCES = (
    "gevi",
    "aebn",
    "tla",
    "gayempire",
    "gayhotmovies",
    "gayrado",
    "gaymovie",
    "hfgpm",
    "gayworld",
)
MOVIE_ENRICHMENT_INPUT_SOURCES = set(MOVIE_ENRICHMENT_SOURCES) | {"gayadultfilms"}
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
class IdentityAssessment:
    accepted: bool
    score: float
    reasons: list[str]
    corroborators: int


@dataclass
class MetadataProvenance:
    primary_source: str
    enhancements: dict[str, set[str]]


async def build_enriched_metadata(
    requested_rating_key: str,
    selected_source: str,
    selected_metadata: MetadataItem,
) -> MetadataItem:
    if selected_source not in MOVIE_ENRICHMENT_INPUT_SOURCES or not selected_metadata.title:
        return _retarget_metadata(selected_metadata, requested_rating_key)

    settings = get_settings()
    current = selected_metadata
    reference = selected_metadata
    provenance = MetadataProvenance(
        primary_source=selected_source,
        enhancements={},
    )

    gevi_metadata = (
        selected_metadata
        if selected_source == "gevi"
        else await _resolve_source_metadata("gevi", reference)
    )
    if gevi_metadata is not None:
        merged = _merge_metadata(gevi_metadata, current)
        if selected_source == "gevi":
            current = merged
            provenance.primary_source = "gevi"
        else:
            _record_enhancements(provenance, selected_source, gevi_metadata, merged)
            current = merged
            provenance.primary_source = "gevi"
        reference = current
        audit_event(
            "metadata_enrichment_base_selected",
            requested_rating_key=requested_rating_key,
            base_source="gevi",
            metadata=summarize_metadata(current),
        )
    else:
        audit_event(
            "metadata_enrichment_base_selected",
            requested_rating_key=requested_rating_key,
            base_source=selected_source,
            metadata=summarize_metadata(current),
        )

    for source_key in _iter_enrichment_sources(selected_source, settings.search_order):
        if not settings.is_scraper_enabled(source_key):
            continue
        missing_fields = _missing_fields(current)
        if not missing_fields:
            break

        candidate_metadata = await _resolve_source_metadata(source_key, reference)
        if candidate_metadata is None:
            continue

        merged = _merge_metadata(current, candidate_metadata)
        if merged == current:
            continue

        _record_enhancements(provenance, source_key, current, merged)

        audit_event(
            "metadata_enrichment_merged",
            requested_rating_key=requested_rating_key,
            source_key=source_key,
            missing_fields=sorted(missing_fields),
            merged_metadata=summarize_metadata(candidate_metadata),
        )
        current = merged
        reference = current

    current = _apply_summary_sources(current, provenance)
    return _retarget_metadata(current, requested_rating_key)


async def _resolve_source_metadata(source_key: str, reference: MetadataItem) -> MetadataItem | None:
    settings = get_settings()
    if not settings.is_scraper_enabled(source_key):
        return None

    scraper = get_scraper(source_key)
    if scraper is None or not reference.title:
        return None

    search_attempts = [(reference.title, reference.year)]
    if reference.year is not None:
        search_attempts.append((reference.title, None))

    best_match: MetadataItem | None = None
    for title, year in search_attempts:
        try:
            candidates = await scraper.search(title, year)
        except Exception:
            logger.exception("Metadata enrichment search failed for %s", source_key)
            audit_event(
                "metadata_enrichment_search_failed",
                source_key=source_key,
                title=title,
                year=year,
            )
            return None

        best_match = _select_best_candidate(reference, candidates)
        audit_event(
            "metadata_enrichment_candidates_evaluated",
            source_key=source_key,
            title=title,
            year=year,
            result_count=len(candidates),
            selected=summarize_match_item(best_match) if best_match else None,
        )
        if best_match is not None:
            break

    if best_match is None or not best_match.ratingKey:
        return None

    parsed = parse_rating_key(best_match.ratingKey)
    try:
        metadata = await scraper.get_metadata(parsed.source_id)
    except Exception:
        logger.exception("Metadata enrichment metadata fetch failed for %s", best_match.ratingKey)
        audit_event(
            "metadata_enrichment_metadata_failed",
            source_key=source_key,
            rating_key=best_match.ratingKey,
        )
        return None

    assessment = assess_same_work(reference, metadata)
    audit_event(
        "metadata_enrichment_metadata_evaluated",
        source_key=source_key,
        candidate_rating_key=best_match.ratingKey,
        accepted=assessment.accepted,
        score=assessment.score,
        corroborators=assessment.corroborators,
        reasons=assessment.reasons,
        metadata=summarize_metadata(metadata),
    )
    if not assessment.accepted:
        return None

    return metadata


def _iter_enrichment_sources(selected_source: str, search_order: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = {"gevi", selected_source}

    for source_key in search_order:
        if source_key not in MOVIE_ENRICHMENT_SOURCES or source_key in seen:
            continue
        ordered.append(source_key)
        seen.add(source_key)

    for source_key in MOVIE_ENRICHMENT_SOURCES:
        if source_key not in seen:
            ordered.append(source_key)
            seen.add(source_key)

    return ordered


def _select_best_candidate(reference: MetadataItem, candidates: list[MetadataItem]) -> MetadataItem | None:
    ranked: list[tuple[float, MetadataItem]] = []
    for candidate in candidates:
        assessment = assess_same_work(reference, candidate, require_corroboration=False)
        audit_event(
            "metadata_enrichment_candidate_scored",
            candidate=summarize_match_item(candidate),
            accepted=assessment.accepted,
            score=assessment.score,
            reasons=assessment.reasons,
        )
        if assessment.accepted:
            ranked.append((assessment.score, candidate))

    if not ranked:
        return None

    ranked.sort(
        key=lambda item: (
            -item[0],
            _normalize_key(item[1].studio),
            _normalize_key(item[1].title),
        )
    )
    return ranked[0][1]


def assess_same_work(
    reference: MetadataItem,
    candidate: MetadataItem,
    *,
    require_corroboration: bool = True,
) -> IdentityAssessment:
    reasons: list[str] = []
    title_score, title_reasons, title_definitive = _score_title_match(
        reference.title,
        candidate.title,
    )
    reasons.extend(title_reasons)
    if title_score < 70.0:
        reasons.append("title_below_threshold")
        return IdentityAssessment(False, title_score, reasons, 0)

    if _studio_conflict(reference.studio, candidate.studio):
        reasons.append("studio_conflict")
        return IdentityAssessment(False, title_score - 25.0, reasons, 0)

    if _year_conflict(reference.year, candidate.year):
        reasons.append("year_conflict")
        return IdentityAssessment(False, title_score - 25.0, reasons, 0)

    score = title_score
    corroborators = 0

    if _studio_match(reference.studio, candidate.studio):
        corroborators += 1
        score += 20.0
        reasons.append("studio_match")
    elif reference.studio and candidate.studio:
        reasons.append("studio_unknown")

    if _year_match(reference.year, candidate.year):
        corroborators += 1
        score += 15.0
        reasons.append("year_match")

    if _duration_match(reference.duration, candidate.duration):
        corroborators += 1
        score += 10.0
        reasons.append("duration_match")

    if _summary_match(reference.summary, candidate.summary):
        corroborators += 1
        score += 8.0
        reasons.append("summary_match")

    if _tag_overlap(reference.Role, candidate.Role):
        corroborators += 1
        score += 8.0
        reasons.append("cast_overlap")

    if _tag_overlap(reference.Director, candidate.Director):
        corroborators += 1
        score += 6.0
        reasons.append("director_overlap")

    accepted = title_definitive or not require_corroboration or corroborators >= 1
    if not accepted:
        reasons.append("missing_corroboration")

    return IdentityAssessment(accepted, score, reasons, corroborators)


def _score_title_match(
    reference_title: str | None,
    candidate_title: str | None,
) -> tuple[float, list[str], bool]:
    reasons: list[str] = []
    reference_key = _normalize_key(reference_title)
    candidate_key = _normalize_key(candidate_title)
    if not reference_key or not candidate_key:
        return 0.0, ["missing_normalized_title"], False

    reference_raw = _normalize_title_for_expansion(reference_title)
    candidate_raw = _normalize_title_for_expansion(candidate_title)
    similarity = SequenceMatcher(None, reference_key, candidate_key).ratio()

    if reference_key == candidate_key:
        return 100.0, ["title_exact"], True
    if _is_subtitle_expansion(reference_raw, candidate_raw) or _is_subtitle_expansion(
        candidate_raw, reference_raw
    ):
        return 97.0, ["title_subtitle_expansion"], True
    if reference_key in candidate_key or candidate_key in reference_key:
        reasons.append("title_contains")
        return max(80.0, similarity * 100.0), reasons, False
    if similarity >= 0.9:
        reasons.append("title_similarity_strong")
        return similarity * 100.0, reasons, False
    if similarity >= 0.84:
        reasons.append("title_similarity_good")
        return similarity * 90.0, reasons, False
    return similarity * 60.0, ["title_similarity_weak"], False


def _missing_fields(metadata: MetadataItem) -> set[str]:
    missing: set[str] = set()
    if not metadata.summary:
        missing.add("summary")
    if not metadata.studio:
        missing.add("studio")
    if metadata.year is None:
        missing.add("year")
    if not metadata.originallyAvailableAt:
        missing.add("release_date")
    if metadata.duration is None:
        missing.add("duration")
    if not _has_image_type(metadata, "coverPoster"):
        missing.add("poster")
    if not _has_image_type(metadata, "background"):
        missing.add("background")
    if not metadata.Genre:
        missing.add("genres")
    if not metadata.Role:
        missing.add("cast")
    if not metadata.Director:
        missing.add("directors")
    if not metadata.Producer:
        missing.add("producers")
    return missing


def _has_image_type(metadata: MetadataItem, image_type: str) -> bool:
    return any(image.type == image_type for image in metadata.Image or [])


def _merge_metadata(primary: MetadataItem, secondary: MetadataItem) -> MetadataItem:
    updates: dict[str, object] = {}

    scalar_fields = (
        "type",
        "title",
        "year",
        "thumb",
        "summary",
        "originallyAvailableAt",
        "studio",
        "duration",
        "contentRating",
        "isAdult",
    )
    for field in scalar_fields:
        primary_value = getattr(primary, field, None)
        secondary_value = getattr(secondary, field, None)
        if not primary_value and secondary_value is not None:
            updates[field] = secondary_value

    if primary.summary and secondary.summary:
        if primary.summary in secondary.summary and len(secondary.summary) > len(primary.summary):
            updates["summary"] = secondary.summary

    merged_images = _merge_unique_items(primary.Image, secondary.Image, _image_key)
    if merged_images != (primary.Image or []):
        updates["Image"] = merged_images or None

    merged_genres = _merge_unique_items(primary.Genre, secondary.Genre, _tag_key)
    if merged_genres != (primary.Genre or []):
        updates["Genre"] = merged_genres or None

    merged_roles = _merge_unique_items(primary.Role, secondary.Role, _role_key)
    if merged_roles != (primary.Role or []):
        updates["Role"] = merged_roles or None

    merged_directors = _merge_unique_items(primary.Director, secondary.Director, _tag_key)
    if merged_directors != (primary.Director or []):
        updates["Director"] = merged_directors or None

    merged_producers = _merge_unique_items(primary.Producer, secondary.Producer, _tag_key)
    if merged_producers != (primary.Producer or []):
        updates["Producer"] = merged_producers or None

    merged_chapters = _merge_unique_items(primary.Chapter, secondary.Chapter, _chapter_key)
    if merged_chapters != (primary.Chapter or []):
        updates["Chapter"] = merged_chapters or None

    merged_collections = _merge_unique_items(primary.Collection, secondary.Collection, _tag_key)
    if merged_collections != (primary.Collection or []):
        updates["Collection"] = merged_collections or None

    if updates:
        return primary.model_copy(update=updates)
    return primary


def _record_enhancements(
    provenance: MetadataProvenance,
    source_key: str,
    before: MetadataItem,
    after: MetadataItem,
) -> None:
    changed_fields = _changed_fields(before, after)
    if not changed_fields:
        return
    existing = provenance.enhancements.setdefault(source_key, set())
    existing.update(changed_fields)


def _changed_fields(before: MetadataItem, after: MetadataItem) -> set[str]:
    changed: set[str] = set()
    if before.summary != after.summary:
        changed.add("summary")
    if before.year != after.year:
        changed.add("year")
    if before.originallyAvailableAt != after.originallyAvailableAt:
        changed.add("release date")
    if before.studio != after.studio:
        changed.add("studio")
    if before.duration != after.duration:
        changed.add("duration")
    if before.contentRating != after.contentRating:
        changed.add("content rating")
    if before.thumb != after.thumb:
        changed.add("poster")
    if _has_new_image_type(before, after, "coverPoster"):
        changed.add("poster")
    if _has_new_image_type(before, after, "background"):
        changed.add("background")
    if _has_list_growth(before.Genre, after.Genre, _tag_key):
        changed.add("genres")
    if _has_list_growth(before.Role, after.Role, _role_key):
        changed.add("cast")
    if _has_list_growth(before.Director, after.Director, _tag_key):
        changed.add("directors")
    if _has_list_growth(before.Producer, after.Producer, _tag_key):
        changed.add("producers")
    if _has_list_growth(before.Chapter, after.Chapter, _chapter_key):
        changed.add("chapters")
    if _has_list_growth(before.Collection, after.Collection, _tag_key):
        changed.add("collections")
    return changed


def _has_list_growth(before_items, after_items, key_func) -> bool:
    before_keys = {key_func(item) for item in before_items or []}
    after_keys = {key_func(item) for item in after_items or []}
    return bool(after_keys - before_keys)


def _has_new_image_type(before: MetadataItem, after: MetadataItem, image_type: str) -> bool:
    before_urls = {
        image.url
        for image in before.Image or []
        if image.type == image_type and image.url
    }
    after_urls = {
        image.url
        for image in after.Image or []
        if image.type == image_type and image.url
    }
    return bool(after_urls - before_urls)


def _apply_summary_sources(metadata: MetadataItem, provenance: MetadataProvenance) -> MetadataItem:
    source_lines = [_display_source_name(provenance.primary_source)]
    for source_key in _ordered_enhancement_sources(provenance.enhancements):
        fields = sorted(
            provenance.enhancements[source_key],
            key=_field_sort_key,
        )
        if not fields:
            continue
        source_lines.append(f"{', '.join(fields)}, {_display_source_name(source_key)}")

    if not source_lines:
        return metadata

    base_summary = _strip_summary_sources(metadata.summary)
    citation_block = "\n".join(source_lines)
    if base_summary:
        summary = f"{base_summary}\n\n--------------\n\n{citation_block}"
    else:
        summary = f"--------------\n\n{citation_block}"
    return metadata.model_copy(update={"summary": summary})


def _ordered_enhancement_sources(enhancements: dict[str, set[str]]) -> list[str]:
    return sorted(
        enhancements,
        key=lambda source_key: (
            MOVIE_ENRICHMENT_SOURCES.index(source_key)
            if source_key in MOVIE_ENRICHMENT_SOURCES
            else len(MOVIE_ENRICHMENT_SOURCES),
            _display_source_name(source_key),
        ),
    )


def _field_sort_key(field_name: str) -> tuple[int, str]:
    ordered_fields = (
        "summary",
        "studio",
        "year",
        "release date",
        "duration",
        "content rating",
        "poster",
        "background",
        "genres",
        "cast",
        "directors",
        "producers",
        "chapters",
        "collections",
    )
    return (
        ordered_fields.index(field_name) if field_name in ordered_fields else len(ordered_fields),
        field_name,
    )


def _display_source_name(source_key: str) -> str:
    display_names = {
        "gevi": "GEVI",
        "aebn": "AEBN",
        "tla": "TLA",
        "gayempire": "GayEmpire",
        "gayhotmovies": "GayHotMovies",
        "gayrado": "GayRado",
        "gaymovie": "GayMovie",
        "gayworld": "GayWorld",
        "hfgpm": "HFGPM",
        "waybig": "WayBig",
        "geviscenes": "GEVIScenes",
        "gayadultfilms": "GayAdultFilms",
        "gayadultscenes": "GayAdultScenes",
    }
    return display_names.get(source_key, source_key)


def _strip_summary_sources(summary: str | None) -> str:
    if not summary:
        return ""
    separator = "\n\n--------------\n\n"
    base_summary, _, _ = summary.partition(separator)
    return base_summary.strip()


def _retarget_metadata(metadata: MetadataItem, requested_rating_key: str) -> MetadataItem:
    provider_id = get_settings().provider_id
    guid = build_guid(provider_id, requested_rating_key)
    updates: dict[str, object] = {
        "ratingKey": requested_rating_key,
        "guid": guid,
        "Guid": [GuidItem(id=guid)],
    }
    if not metadata.thumb:
        poster = next(
            (image.url for image in metadata.Image or [] if image.type == "coverPoster"),
            None,
        )
        if poster:
            updates["thumb"] = poster
    return metadata.model_copy(update=updates)


def _merge_unique_items(items_a, items_b, key_func):
    merged = list(items_a or [])
    seen = {key_func(item) for item in merged}
    for item in items_b or []:
        key = key_func(item)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _image_key(item: ImageItem) -> tuple[str, str]:
    return item.type, item.url


def _tag_key(item: GenreItem | DirectorItem | ProducerItem | CollectionItem) -> str:
    return _normalize_key(item.tag)


def _role_key(item: RoleItem) -> tuple[str, str]:
    return _normalize_key(item.tag), _normalize_key(item.role)


def _chapter_key(item: ChapterItem) -> tuple[str, int, int]:
    return item.title, item.startTimeOffset, item.endTimeOffset


def _studio_match(reference_studio: str | None, candidate_studio: str | None) -> bool:
    if not reference_studio or not candidate_studio:
        return False

    reference_key = _normalize_studio_key(reference_studio)
    candidate_key = _normalize_studio_key(candidate_studio)
    if not reference_key or not candidate_key:
        return False

    return (
        reference_key == candidate_key
        or reference_key in candidate_key
        or candidate_key in reference_key
    )


def _studio_conflict(reference_studio: str | None, candidate_studio: str | None) -> bool:
    return bool(reference_studio and candidate_studio and not _studio_match(reference_studio, candidate_studio))


def _year_match(reference_year: int | None, candidate_year: int | None) -> bool:
    return bool(
        reference_year is not None
        and candidate_year is not None
        and abs(reference_year - candidate_year) <= 3
    )


def _year_conflict(reference_year: int | None, candidate_year: int | None) -> bool:
    return bool(
        reference_year is not None
        and candidate_year is not None
        and abs(reference_year - candidate_year) > 3
    )


def _duration_match(reference_duration: int | None, candidate_duration: int | None) -> bool:
    if reference_duration is None or candidate_duration is None:
        return False
    diff = abs(reference_duration - candidate_duration)
    return diff <= 600_000 or diff <= max(reference_duration, candidate_duration) * 0.1


def _summary_match(reference_summary: str | None, candidate_summary: str | None) -> bool:
    ref_tokens = _summary_tokens(reference_summary)
    cand_tokens = _summary_tokens(candidate_summary)
    if not ref_tokens or not cand_tokens:
        return False
    intersection = len(ref_tokens & cand_tokens)
    union = len(ref_tokens | cand_tokens)
    return union > 0 and (intersection / union) >= 0.3


def _summary_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    normalized = _normalize_key(value)
    return {token for token in normalized.split() if len(token) >= 4}


def _tag_overlap(items_a, items_b) -> bool:
    if not items_a or not items_b:
        return False
    keys_a = {_normalize_key(item.tag) for item in items_a if getattr(item, "tag", None)}
    keys_b = {_normalize_key(item.tag) for item in items_b if getattr(item, "tag", None)}
    return bool(keys_a & keys_b)


def _normalize_key(value: str | None) -> str:
    if not value:
        return ""
    lowered = strip_diacritics(value.lower())
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _normalize_studio_key(value: str | None) -> str:
    tokens = _normalize_key(value).split()
    while tokens and tokens[-1] in STUDIO_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _normalize_title_for_expansion(value: str | None) -> str:
    if not value:
        return ""
    lowered = strip_diacritics(value.lower())
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _is_subtitle_expansion(reference_title: str, candidate_title: str) -> bool:
    if not reference_title or not candidate_title or not candidate_title.startswith(reference_title):
        return False
    if len(candidate_title) <= len(reference_title):
        return False
    remainder = candidate_title[len(reference_title) :].strip()
    if not remainder:
        return False
    return remainder.startswith((":", "-", "part ", "vol ", "volume "))
