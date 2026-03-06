from __future__ import annotations

from fastapi import APIRouter

from src.config import get_settings
from src.models.containers import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", version=settings.provider_version)

