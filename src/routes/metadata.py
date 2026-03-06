from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.config import get_settings
from src.models.containers import MediaContainer, MediaContainerEnvelope

router = APIRouter(tags=["metadata"])
logger = logging.getLogger(__name__)


@router.get("/library/metadata/{rating_key}")
async def get_metadata(rating_key: str, request: Request):
    metadata_service = request.app.state.metadata_service
    try:
        result = await metadata_service.get(rating_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        logger.exception("Metadata fetch failed for %s", rating_key)
        raise HTTPException(status_code=500, detail="Metadata fetch failed")

    settings = get_settings()
    return MediaContainerEnvelope(
        MediaContainer=MediaContainer(
            offset=0,
            totalSize=1,
            identifier=settings.provider_id,
            size=1,
            Metadata=[result],
        )
    )
