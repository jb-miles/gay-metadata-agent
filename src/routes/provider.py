from __future__ import annotations

from fastapi import APIRouter

from src.config import get_settings
from src.models.provider import (
    FeatureDefinition,
    MediaProvider,
    MediaProviderEnvelope,
    SchemeDefinition,
    SupportedType,
)

router = APIRouter(tags=["provider"])


@router.get("/", response_model=MediaProviderEnvelope)
async def provider_root() -> MediaProviderEnvelope:
    settings = get_settings()
    return MediaProviderEnvelope(
        MediaProvider=MediaProvider(
            identifier=settings.provider_id,
            title=settings.provider_title,
            version=settings.provider_version,
            Types=[
                SupportedType(
                    type=1,
                    Scheme=[SchemeDefinition(scheme=settings.provider_id)],
                )
            ],
            Feature=[
                FeatureDefinition(type="metadata", key="/library/metadata"),
                FeatureDefinition(type="match", key="/library/metadata/matches"),
            ],
        )
    )

