from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.config import get_settings
from src.utils.audit import audit_event
from src.models.containers import MatchRequest, MediaContainer, MediaContainerEnvelope

router = APIRouter(tags=["matches"])
logger = logging.getLogger(__name__)


@router.post("/library/metadata/matches")
async def find_matches(body: MatchRequest, request: Request):
    audit_event(
        "lookup_started",
        lookup_type="matches",
        title=body.title,
        year=body.year,
        manual=body.manual,
        guid=body.guid,
    )
    if not body.title:
        audit_event("lookup_finished", lookup_type="matches", result_count=0)
        return MediaContainerEnvelope(
            MediaContainer=MediaContainer(
                identifier=get_settings().provider_id,
            )
        )

    match_service = request.app.state.match_service
    try:
        results = await match_service.search(body.title, body.year)
    except Exception:
        logger.exception("Match search failed for %r", body.title)
        raise HTTPException(status_code=500, detail="Search failed")

    settings = get_settings()
    audit_event(
        "lookup_finished",
        lookup_type="matches",
        title=body.title,
        year=body.year,
        result_count=len(results),
        rating_keys=[item.ratingKey for item in results],
    )
    return MediaContainerEnvelope(
        MediaContainer=MediaContainer(
            offset=0,
            totalSize=len(results),
            identifier=settings.provider_id,
            size=len(results),
            Metadata=results,
        )
    )
