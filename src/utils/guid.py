from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedRatingKey:
    source: str
    source_id: str


def build_rating_key(source: str, source_id: str) -> str:
    if not source or not source_id:
        raise ValueError("source and source_id are required")
    if "/" in source or "/" in source_id:
        raise ValueError("rating keys cannot contain forward slashes")
    return f"{source}-{source_id}"


def parse_rating_key(rating_key: str) -> ParsedRatingKey:
    source, separator, source_id = rating_key.partition("-")
    if not separator or not source_id:
        raise ValueError(f"Invalid rating key: {rating_key}")
    if "/" in source or "/" in source_id:
        raise ValueError(f"Invalid rating key: {rating_key}")
    return ParsedRatingKey(source=source, source_id=source_id)


def build_guid(provider_id: str, rating_key: str, media_type: str = "movie") -> str:
    if "/" in rating_key:
        raise ValueError("rating_key cannot contain forward slashes")
    return f"{provider_id}://{media_type}/{rating_key}"

