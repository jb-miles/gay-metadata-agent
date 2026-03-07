from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import json
import logging
from pathlib import Path
from typing import Any

from asgi_correlation_id.context import correlation_id
from pydantic import BaseModel
import structlog

from src.config import get_settings
from src.models.metadata import MetadataItem

logger = structlog.get_logger("audit")
_path_cache: Path | None = None


def current_trace_id() -> str | None:
    return correlation_id.get()


def audit_event(event: str, **fields: Any) -> None:
    settings = get_settings()
    if not settings.audit_log_enabled:
        return

    payload = {
        "event": event,
        "trace_id": current_trace_id(),
        **{key: _normalize(value) for key, value in fields.items()},
    }
    logger.info(
        "audit_event",
        audit_event_name=event,
        trace_id=payload["trace_id"],
        **{key: value for key, value in payload.items() if key not in {"event", "trace_id"}},
    )
    _write_audit_line(payload)


def summarize_metadata(metadata: MetadataItem) -> dict[str, Any]:
    return {
        "rating_key": metadata.ratingKey,
        "title": metadata.title,
        "studio": metadata.studio,
        "year": metadata.year,
        "originally_available_at": metadata.originallyAvailableAt,
        "duration": metadata.duration,
        "summary_length": len(metadata.summary or ""),
        "image_count": len(metadata.Image or []),
        "genre_count": len(metadata.Genre or []),
        "role_count": len(metadata.Role or []),
        "director_count": len(metadata.Director or []),
        "producer_count": len(metadata.Producer or []),
        "chapter_count": len(metadata.Chapter or []),
        "has_thumb": bool(metadata.thumb),
    }


def summarize_match_item(item: MetadataItem) -> dict[str, Any]:
    return {
        "rating_key": item.ratingKey,
        "title": item.title,
        "studio": item.studio,
        "year": item.year,
        "thumb": bool(item.thumb),
    }


def _write_audit_line(payload: dict[str, Any]) -> None:
    global _path_cache

    if _path_cache is None:
        settings = get_settings()
        audit_dir = Path(settings.audit_log_dir)
        if not audit_dir.is_absolute():
            audit_dir = Path(__file__).resolve().parents[2] / audit_dir
        audit_dir.mkdir(parents=True, exist_ok=True)
        _path_cache = audit_dir / "audit.jsonl"

    with _path_cache.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize(item) for item in value]
    return repr(value)
