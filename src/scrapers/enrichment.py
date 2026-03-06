"""Lightweight extraction helpers for external sites linked from GEVI pages.

These are NOT full scrapers. They extract a subset of metadata (synopsis, cast,
directors, genres, poster/art URLs) from pages already fetched by the GEVI scraper.
Standalone source scrapers now exist; these helpers remain for GEVI cross-source
enrichment.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentData:
    synopsis: str = ""
    directors: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    poster_urls: list[str] = field(default_factory=list)
    art_urls: list[str] = field(default_factory=list)


def enrich_from_aebn(tree) -> EnrichmentData:
    """Extract metadata from an AEBN film page."""
    data = EnrichmentData()

    # Synopsis
    try:
        parts = tree.xpath('//div[@class="dts-section-page-detail-description-body"]/text()')
        if parts:
            data.synopsis = parts[0].strip()
    except Exception:
        logger.debug("AEBN enrichment: no synopsis")

    # Directors
    try:
        raw = tree.xpath('//li[@class="section-detail-list-item-director"]/span/a/span/text()')
        data.directors = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("AEBN enrichment: no directors")

    # Cast
    try:
        raw = tree.xpath('//div[@class="dts-star-name-overlay"]/text()')
        data.cast = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("AEBN enrichment: no cast")

    # Genres (categories + sex acts)
    try:
        cats = tree.xpath('//span[@class="dts-image-display-name"]/text()')
        acts = tree.xpath('//a[contains(@href,"sexActFilters")]/text()')
        combined = {x.strip().replace(",", "") for x in cats + acts if x.strip()}
        data.genres = sorted(combined, key=str.lower)
    except Exception:
        logger.debug("AEBN enrichment: no genres")

    # Images
    try:
        imgs = tree.xpath('//*[contains(@class,"dts-movie-boxcover")]//img/@src')
        imgs = [x.replace("=293", "=1000") for x in imgs]
        imgs = [("http:" + x if not x.startswith("http") else x) for x in imgs]
        if imgs:
            data.poster_urls = [imgs[0]]
            data.art_urls = [imgs[1] if len(imgs) > 1 else imgs[0]]
    except Exception:
        logger.debug("AEBN enrichment: no images")

    return data


def enrich_from_gayempire(tree) -> EnrichmentData:
    """Extract metadata from a GayEmpire film page."""
    data = EnrichmentData()

    # Synopsis
    try:
        parts = tree.xpath('//div[@class="col-xs-12 text-center p-y-2 bg-lightgrey"]/div//p/text()')
        synopsis = "\n".join(parts)
        synopsis = re.sub(r"<[^<]+?>", "", synopsis).strip()
        data.synopsis = synopsis
    except Exception:
        logger.debug("GayEmpire enrichment: no synopsis")

    # Directors
    try:
        raw = tree.xpath('//a[contains(@label, "Director - details")]/text()[normalize-space()]')
        data.directors = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("GayEmpire enrichment: no directors")

    # Cast
    try:
        raw = tree.xpath('//a[@class="PerformerName" and @label="Performers - detail"]/text()')
        data.cast = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("GayEmpire enrichment: no cast")

    # Genres
    try:
        raw = tree.xpath('//ul[@class="list-unstyled m-b-2"]//a[@label="Category"]/text()[normalize-space()]')
        data.genres = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("GayEmpire enrichment: no genres")

    # Images
    try:
        poster = tree.xpath('//img[@itemprop="image"]/@src')
        art = tree.xpath('//a[@id="back-cover"]/@href')
        if poster:
            data.poster_urls = [poster[0]]
        if art:
            data.art_urls = [art[0]]
        elif poster:
            data.art_urls = [poster[0]]
    except Exception:
        logger.debug("GayEmpire enrichment: no images")

    return data


def enrich_from_gayhotmovies(tree) -> EnrichmentData:
    """Extract metadata from a GayHotMovies film page."""
    data = EnrichmentData()

    # Synopsis
    try:
        parts = tree.xpath("//article//text()")
        synopsis = "\n".join(parts)
        synopsis = re.sub(r"<[^<]+?>", "", synopsis).strip()
        # Strip boilerplate
        synopsis = re.sub(
            r"The movie you are enjoying was created by consenting adults.*",
            "",
            synopsis,
            flags=re.DOTALL | re.IGNORECASE,
        )
        synopsis = re.sub(
            r"This title ships.*",
            "",
            synopsis,
            flags=re.DOTALL | re.IGNORECASE,
        )
        data.synopsis = synopsis.strip()
    except Exception:
        logger.debug("GayHotMovies enrichment: no synopsis")

    # Directors
    try:
        raw = tree.xpath('//a[@label="Director"]/text()[normalize-space()]')
        data.directors = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("GayHotMovies enrichment: no directors")

    # Cast
    try:
        raw = tree.xpath('//a[@label="Performer"]/text()[normalize-space()]')
        data.cast = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("GayHotMovies enrichment: no cast")

    # Genres
    try:
        raw = tree.xpath('//a[@label="Category"]/text()[normalize-space()]')
        data.genres = sorted({x.strip() for x in raw if x.strip()}, key=str.lower)
    except Exception:
        logger.debug("GayHotMovies enrichment: no genres")

    # Images
    try:
        poster = tree.xpath('//img[@label="Front Boxcover"]/@src')
        art = tree.xpath('//a[@class="fancy"]/@href')
        if poster:
            data.poster_urls = [poster[0]]
        if art:
            data.art_urls = [art[0]]
        elif poster:
            data.art_urls = [poster[0]]
    except Exception:
        logger.debug("GayHotMovies enrichment: no images")

    return data
