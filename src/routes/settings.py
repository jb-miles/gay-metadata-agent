from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.config import get_settings

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "provider_title": settings.provider_title,
            "provider_version": settings.provider_version,
            "host": settings.host,
            "port": settings.port,
            "search_order": settings.search_order,
            "enabled_scrapers": settings.enabled_scrapers,
        },
    )


@router.post("/settings")
async def update_settings() -> None:
    raise HTTPException(
        status_code=501,
        detail="Settings updates are not implemented yet. Edit the .env file directly.",
    )
