from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from src.config import get_settings
from src.models.containers import MediaContainer, MediaContainerEnvelope
from src.models.metadata import MetadataItem

router = APIRouter(tags=["images"])
logger = logging.getLogger(__name__)


@router.get("/library/metadata/{rating_key}/images")
async def get_images(rating_key: str, request: Request):
    metadata_service = request.app.state.metadata_service
    try:
        result = await metadata_service.get(rating_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        logger.exception("Image fetch failed for %s", rating_key)
        raise HTTPException(status_code=500, detail="Image fetch failed")

    settings = get_settings()
    image_only = MetadataItem(
        ratingKey=result.ratingKey,
        Image=result.Image,
    )
    return MediaContainerEnvelope(
        MediaContainer=MediaContainer(
            identifier=settings.provider_id,
            size=len(result.Image) if result.Image else 0,
            Metadata=[image_only],
        )
    )
