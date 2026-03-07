from __future__ import annotations

import httpx

from src.config import get_settings
from src.utils.audit import audit_event


async def _on_request(request: httpx.Request) -> None:
    audit_event(
        "source_request",
        method=request.method,
        url=str(request.url),
        headers=dict(request.headers),
    )


async def _on_response(response: httpx.Response) -> None:
    settings = get_settings()
    payload = {
        "method": response.request.method,
        "url": str(response.request.url),
        "status_code": response.status_code,
        "headers": dict(response.headers),
    }
    if settings.audit_http_body_enabled or settings.audit_http_body_preview_chars > 0:
        await response.aread()
        preview_limit = settings.audit_http_body_preview_chars
        if preview_limit > 0:
            payload["body_preview"] = response.text[:preview_limit]
        if settings.audit_http_body_enabled:
            payload["body"] = response.text
    audit_event("source_response", **payload)


def build_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        },
        timeout=30.0,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        event_hooks={
            "request": [_on_request],
            "response": [_on_response],
        },
    )
