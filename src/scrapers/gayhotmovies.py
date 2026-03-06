from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime

import httpx
from lxml import html as lxml_html

from src.config import get_settings
from src.models.metadata import (
    ChapterItem,
    CollectionItem,
    DirectorItem,
    GenreItem,
    GuidItem,
    ImageItem,
    MetadataItem,
    ProducerItem,
    RoleItem,
)
from src.scrapers.base import BaseScraper
from src.utils.guid import build_guid, build_rating_key
from src.utils.text import normalize_whitespace, strip_diacritics

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gayhotmovies.com"
BASE_SEARCH_URL = BASE_URL + "/adult-movies/search?q={0}&sort=title"

VIDEO_ID_PATTERN = re.compile(r"/(\d{4,})/")
MAX_RESULTS = 20
MAX_PAGES = 10



def _clean_search_string(title: str) -> str:
    value = title.strip()
    # Remove specific punctuation characters
    value = re.sub(r"[-',.&!.#]", "", value)
    # Replace dashes and parens with spaces
    value = re.sub(r"[\u2013\u2014()]", " ", value)
    # Drop standalone ' and '/' 'And'
    value = re.sub(r"\sand\s", " ", value, flags=re.IGNORECASE)
    value = normalize_whitespace(value)
    value = strip_diacritics(value)
    encoded = urllib.parse.quote(value)
    return encoded.replace("%25", "%").replace("*", "")


def _to_absolute_url(value: str) -> str:
    if not value:
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return BASE_URL + value
    return value


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = normalize_whitespace(item)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _parse_duration_ms(value: str | None) -> int | None:
    """Parse '9 hrs. 99 mins.' or '120 mins.' to milliseconds."""
    if not value:
        return None
    raw = normalize_whitespace(value)
    total_minutes = 0
    hrs_match = re.search(r"(\d+)\s*hrs?\.?", raw)
    mins_match = re.search(r"(\d+)\s*mins?\.?", raw)
    if hrs_match:
        total_minutes += int(hrs_match.group(1)) * 60
    if mins_match:
        total_minutes += int(mins_match.group(1))
    elif not hrs_match:
        try:
            total_minutes = int(raw.split()[0])
        except (ValueError, IndexError):
            return None
    return total_minutes * 60_000 if total_minutes > 0 else None


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None


def _parse_scene_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d+)\s*min", normalize_whitespace(value), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _split_director_blob(directors: list[str]) -> list[str]:
    if len(directors) != 1:
        return directors
    value = directors[0]
    if "," in value:
        return _dedupe([item.strip() for item in value.split(",") if item.strip()])

    tokens = value.split()
    if len(tokens) == 4 and all(token[:1].isupper() for token in tokens):
        return [f"{tokens[0]} {tokens[1]}", f"{tokens[2]} {tokens[3]}"]
    return directors


def _extract_scene_breakdown(tree: lxml_html.HtmlElement) -> list[dict[str, object]]:
    titles = [
        normalize_whitespace(item)
        for item in tree.xpath('//a[@label="Scene Title"]/text()[normalize-space()]')
    ]
    duration_values = [
        normalize_whitespace(item)
        for item in tree.xpath('//small[@class="badge"]/text()[normalize-space()]')
    ]

    scenes: list[dict[str, object]] = []
    for idx, title in enumerate(titles, start=1):
        duration_minutes = _parse_scene_minutes(
            duration_values[idx - 1] if idx - 1 < len(duration_values) else None
        )
        scenes.append(
            {
                "number": idx,
                "title": title,
                "duration_minutes": duration_minutes,
            }
        )
    return scenes


def _append_scene_breakdown(summary: str | None, scenes: list[dict[str, object]]) -> str | None:
    base_summary = normalize_whitespace(summary or "")
    if not scenes:
        return base_summary or None

    lines: list[str] = ["Scene Breakdown:"]
    for scene in scenes:
        line = f"{scene['number']}. {scene['title']}"
        duration_minutes = scene["duration_minutes"]
        if duration_minutes:
            line += f" ({duration_minutes} min)"
        lines.append(line)

    scene_text = "\n".join(lines)
    if base_summary:
        return f"{base_summary}\n\n{scene_text}"
    return scene_text


def _build_chapters_from_scenes(
    scenes: list[dict[str, object]], duration_ms: int | None
) -> list[ChapterItem]:
    if not scenes or duration_ms is None:
        return []

    if not all(scene.get("duration_minutes") for scene in scenes):
        return []

    total_scene_ms = sum(int(scene["duration_minutes"]) * 60_000 for scene in scenes)
    if total_scene_ms != duration_ms:
        return []

    chapters: list[ChapterItem] = []
    cursor = 0
    for scene in scenes:
        scene_ms = int(scene["duration_minutes"]) * 60_000
        chapters.append(
            ChapterItem(
                title=f"Scene {scene['number']}: {scene['title']}",
                startTimeOffset=cursor,
                endTimeOffset=cursor + scene_ms,
            )
        )
        cursor += scene_ms
    return chapters


class GayHotMoviesScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        # Set age-gate cookies on the shared client for this domain.
        self._client.cookies.set("ageConfirmed", "true", domain="www.gayhotmovies.com")
        self._movie_urls: dict[str, str] = {}

    @property
    def source_key(self) -> str:
        return "gayhotmovies"

    @property
    def source_name(self) -> str:
        return "Gay Hot Movies"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        encoded = _clean_search_string(title)

        results: list[MetadataItem] = []
        current_url = BASE_SEARCH_URL.format(encoded)

        for _page in range(MAX_PAGES):
            if len(results) >= MAX_RESULTS:
                break

            try:
                response = await self._client.get(current_url, timeout=30.0)
                response.raise_for_status()
            except Exception:
                logger.exception("GayHotMovies search failed for %s", current_url)
                break

            tree = lxml_html.fromstring(response.text)

            # Detect age gate page
            if tree.xpath('//title[contains(text(), "Age Confirmation")]') or tree.xpath(
                '//form[contains(@action, "AgeConfirmation")]'
            ):
                logger.warning("GayHotMovies: age gate page detected")
                break

            film_nodes = tree.xpath('//div[@class="item-preview-video"]')
            if not film_nodes:
                break

            for node in film_nodes:
                if len(results) >= MAX_RESULTS:
                    break

                film_title = _first_text(node.xpath('./@itemtitle'))
                if not film_title:
                    continue

                href = _first_text(node.xpath('./a[@label="Boxcover"]/@href'))
                if not href:
                    continue
                film_url = _to_absolute_url(href)

                id_match = VIDEO_ID_PATTERN.search(href)
                if not id_match:
                    continue
                video_id = id_match.group(1)
                self._movie_urls[video_id] = film_url

                thumb = _first_text(node.xpath('.//img/@src'))

                rating_key = build_rating_key(self.source_key, video_id)
                guid = build_guid(provider_id, rating_key)

                results.append(
                    MetadataItem(
                        type="movie",
                        ratingKey=rating_key,
                        guid=guid,
                        title=normalize_whitespace(film_title),
                        thumb=_to_absolute_url(thumb) if thumb else None,
                    )
                )

            next_link = _first_text(tree.xpath('//a[@title="Next"]/@href'))
            if not next_link:
                break
            current_url = _to_absolute_url(next_link)

        logger.info("GayHotMovies search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        film_url = self._movie_urls.get(source_id) or f"{BASE_URL}/{source_id}/"

        response = await self._client.get(film_url, timeout=45.0)
        response.raise_for_status()
        tree = lxml_html.fromstring(response.text)

        # Title
        page_title = _first_text(tree.xpath('//h1/text()'))
        if not page_title:
            page_title = _first_text(tree.xpath('//title/text()'))
            if page_title and "|" in page_title:
                page_title = page_title.split("|")[0].strip()

        # Studio
        studio = _first_text(tree.xpath('//a[@label="Studio"]/text()'))

        # Release year
        release_year: int | None = None
        release_iso: str | None = None
        year_text = _first_text(
            tree.xpath('//strong[text()="Release Year:"]/following-sibling::text()[1]')
        )
        if year_text:
            year_text = year_text.strip()
            try:
                release_year = int(year_text)
                release_iso = f"{release_year}-12-31"
            except ValueError:
                pass

        # Duration
        duration_text = _first_text(
            tree.xpath('//strong[text()="Run Time: "]/following-sibling::text()[1]')
        )
        duration_ms = _parse_duration_ms(duration_text)

        # Synopsis
        synopsis_parts = [
            x.strip()
            for x in tree.xpath("//article//text()")
            if x.strip()
        ]
        synopsis = normalize_whitespace(" ".join(synopsis_parts))
        synopsis = re.sub(r"<[^<]+?>", "", synopsis).strip()
        # Strip boilerplate
        synopsis = re.sub(
            r"The movie you are enjoying was created by consenting adults.*",
            "", synopsis, flags=re.DOTALL | re.IGNORECASE,
        )
        synopsis = re.sub(
            r"This title ships.*", "", synopsis, flags=re.DOTALL | re.IGNORECASE,
        )
        synopsis = synopsis.strip()
        scenes = _extract_scene_breakdown(tree)
        synopsis = _append_scene_breakdown(synopsis, scenes)
        chapters = _build_chapters_from_scenes(scenes, duration_ms)

        # Directors
        directors = _dedupe(
            tree.xpath('//a[@label="Director"]/text()[normalize-space()]')
        )
        directors = _split_director_blob(directors)

        # Producers
        producers = _dedupe(
            tree.xpath('//li[.//small[contains(text(),"Producer")]]/text()[normalize-space()]')
        )

        # Cast
        cast = _dedupe(
            tree.xpath('//a[@label="Performer"]/text()[normalize-space()]')
        )

        # Genres
        raw_genres = _dedupe(
            tree.xpath('//a[@label="Category"]/text()[normalize-space()]')
        )

        # Images
        poster = _first_text(tree.xpath('//img[@label="Front Boxcover"]/@src'))
        back_cover = _first_text(tree.xpath('//a[@class="fancy"]/@href'))

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        image_items: list[ImageItem] = []
        if poster:
            image_items.append(
                ImageItem(alt=page_title or "", type="coverPoster", url=_to_absolute_url(poster))
            )
        if back_cover:
            image_items.append(
                ImageItem(alt=page_title or "", type="background", url=_to_absolute_url(back_cover))
            )
        elif poster:
            image_items.append(
                ImageItem(alt=page_title or "", type="background", url=_to_absolute_url(poster))
            )

        collection_items = [CollectionItem(tag=studio)] if studio else []

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            title=page_title,
            year=release_year,
            originallyAvailableAt=release_iso,
            summary=synopsis or None,
            studio=studio,
            duration=duration_ms,
            contentRating="X",
            isAdult=True,
            Image=image_items or None,
            Genre=[GenreItem(tag=g) for g in raw_genres] or None,
            Role=[RoleItem(tag=c, role="Performer") for c in cast] or None,
            Director=[DirectorItem(tag=d) for d in directors] or None,
            Producer=[ProducerItem(tag=p) for p in producers] or None,
            Chapter=chapters or None,
            Collection=collection_items or None,
            Guid=[GuidItem(id=guid)],
        )
