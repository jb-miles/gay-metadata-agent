"""Scraper registry — maps source keys to scraper instances."""
from __future__ import annotations

import httpx

from src.scrapers.aebn import AEBNScraper
from src.scrapers.base import BaseScraper
from src.scrapers.gayadultfilms import GayAdultFilmsScraper
from src.scrapers.gayadultscenes import GayAdultScenesScraper
from src.scrapers.gayempire import GayEmpireScraper
from src.scrapers.gayhotmovies import GayHotMoviesScraper
from src.scrapers.gaymovie import GayMovieScraper
from src.scrapers.gayrado import GayRadoScraper
from src.scrapers.gayworld import GayWorldScraper
from src.scrapers.gevi import GEVIScraper
from src.scrapers.geviscenes import GEVIScenesScraper
from src.scrapers.hfgpm import HFGPMScraper
from src.scrapers.tla import TLAScraper
from src.scrapers.waybig import WayBigScraper

_registry: dict[str, BaseScraper] = {}


def init_scrapers(http_client: httpx.AsyncClient) -> None:
    """Instantiate all scrapers and register them. Called once at startup."""
    _registry.clear()
    _registry["gevi"] = GEVIScraper(http_client)
    _registry["aebn"] = AEBNScraper(http_client)
    _registry["tla"] = TLAScraper(http_client)
    _registry["geviscenes"] = GEVIScenesScraper(http_client)
    _registry["waybig"] = WayBigScraper(http_client)
    _registry["gayempire"] = GayEmpireScraper(http_client)
    _registry["gayhotmovies"] = GayHotMoviesScraper(http_client)
    _registry["gayrado"] = GayRadoScraper(http_client)
    _registry["gayworld"] = GayWorldScraper(http_client)
    _registry["gaymovie"] = GayMovieScraper(http_client)
    _registry["hfgpm"] = HFGPMScraper(http_client)
    _registry["gayadultfilms"] = GayAdultFilmsScraper(http_client)
    _registry["gayadultscenes"] = GayAdultScenesScraper(http_client)


def shutdown_scrapers() -> None:
    _registry.clear()


def get_scraper(source_key: str) -> BaseScraper | None:
    return _registry.get(source_key)


def get_all_scrapers() -> dict[str, BaseScraper]:
    return dict(_registry)
