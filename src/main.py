from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from src.config import get_settings
from src.routes.health import router as health_router
from src.routes.images import router as images_router
from src.routes.matches import router as matches_router
from src.routes.metadata import router as metadata_router
from src.routes.provider import router as provider_router
from src.routes.settings import router as settings_router
from src.scrapers import init_scrapers, shutdown_scrapers
from src.services.match_service import MatchService
from src.services.metadata_service import MetadataService
from src.utils.http_client import build_http_client
from src.utils.logger import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    http_client = build_http_client()
    init_scrapers(http_client)
    app.state.http_client = http_client
    app.state.match_service = MatchService()
    app.state.metadata_service = MetadataService()

    logger.info(
        "starting provider",
        extra={
            "provider_id": settings.provider_id,
            "provider_version": settings.provider_version,
        },
    )
    yield
    logger.info("stopping provider")
    shutdown_scrapers()
    await http_client.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.provider_title,
        version=settings.provider_version,
        lifespan=lifespan,
    )
    app.include_router(provider_router)
    app.include_router(health_router)
    app.include_router(matches_router)
    app.include_router(metadata_router)
    app.include_router(images_router)
    app.include_router(settings_router)
    return app


app = create_app()

