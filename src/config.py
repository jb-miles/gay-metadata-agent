from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv


SCRAPER_ENV_MAP = {
    "gayadultfilms": "SCRAPER_GAYADULTFILMS",
    "gayadultscenes": "SCRAPER_GAYADULTSCENES",
    "gevi": "SCRAPER_GEVI",
    "geviscenes": "SCRAPER_GEVISCENES",
    "aebn": "SCRAPER_AEBN",
    "tla": "SCRAPER_TLA",
    "gayempire": "SCRAPER_GAYEMPIRE",
    "gayhotmovies": "SCRAPER_GAYHOTMOVIES",
    "gayworld": "SCRAPER_GAYWORLD",
    "gaymovie": "SCRAPER_GAYMOVIE",
    "hfgpm": "SCRAPER_HFGPM",
    "cduniverse": "SCRAPER_CDUNIVERSE",
    "homoactive": "SCRAPER_HOMOACTIVE",
    "bestexclusiveporn": "SCRAPER_BESTEXCLUSIVEPORN",
    "aventertainments": "SCRAPER_AVENTERTAINMENTS",
    "simplyadult": "SCRAPER_SIMPLYADULT",
    "adultfilmdb": "SCRAPER_ADULTFILMDB",
    "waybig": "SCRAPER_WAYBIG",
    "queerclick": "SCRAPER_QUEERCLICK",
    "fagalicious": "SCRAPER_FAGALICIOUS",
    "gayrado": "SCRAPER_GAYRADO",
    "wolffvideo": "SCRAPER_WOLFFVIDEO",
}

DEFAULT_SEARCH_ORDER = [
    "gevi",
    "aebn",
    "tla",
    "gayempire",
    "gayhotmovies",
    "gayrado",
    "waybig",
    "geviscenes",
    "gaymovie",
    "hfgpm",
    "gayworld",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env() -> None:
    env_path = _project_root() / ".env"
    load_dotenv(env_path, override=False)


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _parse_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer.") from exc


def _parse_csv(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    api_token: str | None
    log_level: str
    provider_id: str
    provider_title: str
    provider_version: str
    scraper_flags: dict[str, bool]
    search_order: list[str]
    artwork_prefer_front_cover: bool
    artwork_download_performer_images: bool
    artwork_max_posters: int
    artwork_max_backgrounds: int
    scenes_as_episodes: bool
    cache_search_ttl_seconds: int
    cache_metadata_ttl_seconds: int
    audit_log_enabled: bool
    audit_log_dir: str
    audit_http_body_enabled: bool
    audit_http_body_preview_chars: int

    @property
    def enabled_scrapers(self) -> list[str]:
        return [key for key, enabled in self.scraper_flags.items() if enabled]

    def is_scraper_enabled(self, source_key: str) -> bool:
        return self.scraper_flags.get(source_key, False)


def _build_settings() -> Settings:
    _load_env()
    scraper_flags = {
        key: _parse_bool(env_name, default=True)
        for key, env_name in SCRAPER_ENV_MAP.items()
    }
    for key in {
        "cduniverse",
        "homoactive",
        "bestexclusiveporn",
        "aventertainments",
        "simplyadult",
        "adultfilmdb",
        "queerclick",
        "fagalicious",
        "wolffvideo",
    }:
        scraper_flags[key] = _parse_bool(SCRAPER_ENV_MAP[key], default=False)

    return Settings(
        host=_get_env("HOST", "127.0.0.1"),
        port=_parse_int("PORT", 8778),
        api_token=os.getenv("API_TOKEN") or None,
        log_level=_get_env("LOG_LEVEL", "info"),
        provider_id=_get_env("PROVIDER_ID", "tv.plex.agents.custom.jb.miles.pgmam"),
        provider_title=_get_env("PROVIDER_TITLE", "Gay Adult Metadata Agent for Plex"),
        provider_version=_get_env("PROVIDER_VERSION", "2.0.0"),
        scraper_flags=scraper_flags,
        search_order=_parse_csv("SEARCH_ORDER", DEFAULT_SEARCH_ORDER),
        artwork_prefer_front_cover=_parse_bool("ARTWORK_PREFER_FRONT_COVER", True),
        artwork_download_performer_images=_parse_bool(
            "ARTWORK_DOWNLOAD_PERFORMER_IMAGES", True
        ),
        artwork_max_posters=_parse_int("ARTWORK_MAX_POSTERS", 3),
        artwork_max_backgrounds=_parse_int("ARTWORK_MAX_BACKGROUNDS", 2),
        scenes_as_episodes=_parse_bool("SCENES_AS_EPISODES", False),
        cache_search_ttl_seconds=_parse_int("CACHE_SEARCH_TTL_SECONDS", 3600),
        cache_metadata_ttl_seconds=_parse_int("CACHE_METADATA_TTL_SECONDS", 86400),
        audit_log_enabled=_parse_bool("AUDIT_LOG_ENABLED", True),
        audit_log_dir=_get_env("AUDIT_LOG_DIR", "data/audit"),
        audit_http_body_enabled=_parse_bool("AUDIT_HTTP_BODY_ENABLED", False),
        audit_http_body_preview_chars=_parse_int("AUDIT_HTTP_BODY_PREVIEW_CHARS", 0),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _build_settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
