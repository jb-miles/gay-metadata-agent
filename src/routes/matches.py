from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.config import get_settings
from src.models.containers import MatchRequest, MediaContainer, MediaContainerEnvelope

router = APIRouter(tags=["matches"])
logger = logging.getLogger(__name__)


@router.post("/library/metadata/matches")
async def find_matches(body: MatchRequest, request: Request):
    if not body.title:
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
    return MediaContainerEnvelope(
        MediaContainer=MediaContainer(
            offset=0,
            totalSize=len(results),
            identifier=settings.provider_id,
            size=len(results),
            Metadata=results,
        )
    )
