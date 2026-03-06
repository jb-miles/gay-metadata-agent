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

BASE_URL = "https://www.tlavideo.com"
BASE_SEARCH_URL = BASE_URL + "/search/search?media=14&q={0}&siterefine=Gay&page={1}"

VIDEO_ID_PATTERN = re.compile(r"/videos/(\d{4,})/")
MAX_RESULTS = 20
MAX_PAGES = 10


def _clean_search_string(title: str) -> str:
    value = title.strip().lower()
    value = strip_diacritics(value)
    encoded = urllib.parse.quote(value)
    return encoded.replace("%25", "%").replace("*", "")


def _to_absolute_url(value: str) -> str:
    if not value:
        return value
    if value.startswith(("http://", "https://")):
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


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None


def _strip_studio_suffix(title: str, studio: str | None) -> str:
    if not studio:
        return title
    matched = re.search(r"\(([^)]+)\)$", title)
    if not matched:
        return title
    suffix = normalize_whitespace(matched.group(1))
    if suffix.lower() == studio.lower():
        return title[: matched.start()].strip()
    return title


def _parse_release_date(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = normalize_whitespace(value)
    for fmt in ("%b %d %Y", "%B %d %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_duration_ms(value: str | None) -> int | None:
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
        return None
    return total_minutes * 60_000 if total_minutes > 0 else None


def _parse_scene_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d+)\s*min", normalize_whitespace(value), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _scene_key(title: str) -> str:
    return normalize_whitespace(title.replace("...", "")).lower()


def _extract_scene_breakdown(tree: lxml_html.HtmlElement, cast: list[str]) -> list[dict[str, object]]:
    scenes: list[dict[str, object]] = []
    cast_index = {name.lower(): name for name in cast}
    scene_index_by_key: dict[str, int] = {}

    scene_nodes = tree.xpath('//div[contains(@class,"scene-list")]//div[contains(@class,"row")]')
    for node in scene_nodes:
        title = _first_text(node.xpath('.//h3/text()[normalize-space()]'))
        if not title:
            continue
        title_key = _scene_key(title)

        scene_cast = _dedupe(node.xpath('.//small//a/text()[normalize-space()]'))
        if not scene_cast:
            lowered_title = title.lower()
            scene_cast = [
                original_name
                for name_lower, original_name in cast_index.items()
                if re.search(rf"\b{re.escape(name_lower)}\b", lowered_title)
            ]

        scene_data = {
            "number": len(scenes) + 1,
            "title": title,
            "duration_minutes": _parse_scene_minutes(
                _first_text(node.xpath('.//small[contains(text()," min")]/text()'))
            ),
            "cast": scene_cast,
        }
        existing_idx = scene_index_by_key.get(title_key)
        if existing_idx is None:
            scene_index_by_key[title_key] = len(scenes)
            scenes.append(scene_data)
            continue

        existing_title = str(scenes[existing_idx]["title"])
        if title.endswith("...") and len(existing_title) >= len(title):
            continue
        if len(title) > len(existing_title):
            scene_data["number"] = scenes[existing_idx]["number"]
            scenes[existing_idx] = scene_data

    return scenes


def _append_scene_breakdown(summary: str | None, scenes: list[dict[str, object]]) -> str | None:
    base_summary = normalize_whitespace(summary or "")
    if not scenes:
        return base_summary or None

    lines: list[str] = ["Scene Breakdown:"]
    for scene in scenes:
        line = f"{scene['number']}. {scene['title']}"
        if scene["duration_minutes"]:
            line += f" ({scene['duration_minutes']} min)"
        lines.append(line)
        if scene["cast"]:
            lines.append(f"Cast: {', '.join(scene['cast'])}")

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


class TLAScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self._client.cookies.set("ageConfirmed", "true", domain="www.tlavideo.com")
        self._movie_urls: dict[str, str] = {}

    @property
    def source_key(self) -> str:
        return "tla"

    @property
    def source_name(self) -> str:
        return "TLA"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        encoded = _clean_search_string(title)

        results: list[MetadataItem] = []

        for page_num in range(1, MAX_PAGES + 1):
            if len(results) >= MAX_RESULTS:
                break

            search_url = BASE_SEARCH_URL.format(encoded, page_num)
            try:
                response = await self._client.get(search_url, timeout=30.0)
                response.raise_for_status()
            except Exception:
                logger.exception("TLA search failed for %s", search_url)
                break

            tree = lxml_html.fromstring(response.text)
            film_nodes = tree.xpath('//div[contains(@class,"item-preview-video")]')
            if not film_nodes:
                break

            for node in film_nodes:
                if len(results) >= MAX_RESULTS:
                    break

                href = _first_text(
                    node.xpath('./a[@label="Boxcover"]/@href | ./a[@label="Title"]/@href')
                )
                if not href:
                    continue
                video_url = _to_absolute_url(href)

                id_match = VIDEO_ID_PATTERN.search(video_url)
                if not id_match:
                    continue
                video_id = id_match.group(1)
                self._movie_urls[video_id] = video_url

                film_title = _first_text(node.xpath('./@itemtitle'))
                if not film_title:
                    film_title = _first_text(node.xpath('.//a[@label="Title"]/text()'))
                if not film_title:
                    continue
                matched = re.search(r"^(?P<title>.+?)\s+\((?P<studio>[^)]+)\)$", film_title)
                if matched:
                    film_title = matched.group("title")

                thumb = _first_text(node.xpath('.//img/@data-src'))
                if not thumb or "blank-" in thumb:
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

        logger.info("TLA search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        film_url = self._movie_urls.get(source_id) or f"{BASE_URL}/videos/{source_id}"
        response = await self._client.get(film_url, timeout=45.0)
        response.raise_for_status()
        tree = lxml_html.fromstring(response.text)

        page_title = _first_text(tree.xpath("//h1/text()"))
        if not page_title:
            page_title = _first_text(tree.xpath("//title/text()"))
            if page_title and "|" in page_title:
                page_title = page_title.split("|", 1)[0].strip()

        studio = _first_text(
            tree.xpath(
                '//small[contains(text(),"Studio")]/following-sibling::a/text()'
                ' | //a[contains(@href,"/studios/")]/text()'
            )
        )
        if page_title and studio:
            page_title = _strip_studio_suffix(page_title, studio)

        synopsis_parts = tree.xpath(
            '//div[@id="synopsis-container"]//div[contains(@class,"synopsis-content")]//text()[normalize-space()]'
        )
        synopsis = normalize_whitespace(" ".join(synopsis_parts))

        directors = _dedupe(
            tree.xpath(
                '//small[contains(text(),"Director")]/following-sibling::a/text()[normalize-space()]'
            )
        )
        producers = _dedupe(
            tree.xpath(
                '//small[contains(text(),"Producer")]/following-sibling::span//text()[normalize-space()]'
                ' | //small[contains(text(),"Producer")]/following-sibling::a/text()[normalize-space()]'
            )
        )
        cast = _dedupe(tree.xpath('//a[contains(@href,"/actors/")]/text()[normalize-space()]'))
        genres = _dedupe(
            tree.xpath('//a[contains(@href,"/categories/")]/text()[normalize-space()]')
        )

        release_text = _first_text(
            tree.xpath('//small[contains(text(),"Released")]/following-sibling::text()[1]')
        )
        release_date = _parse_release_date(release_text)
        production_year_text = _first_text(
            tree.xpath('//small[contains(text(),"Production Year")]/following-sibling::text()[1]')
        )
        if not release_date and production_year_text:
            year_match = re.search(r"(\d{4})", production_year_text)
            if year_match:
                release_date = datetime(int(year_match.group(1)), 12, 31)

        duration_text = _first_text(
            tree.xpath('//small[contains(text(),"Length")]/following-sibling::text()[1]')
        )
        duration_ms = _parse_duration_ms(duration_text)

        scenes = _extract_scene_breakdown(tree, cast)
        synopsis = _append_scene_breakdown(synopsis, scenes)
        chapters = _build_chapters_from_scenes(scenes, duration_ms)

        images = _dedupe(
            tree.xpath(
                '//img[@itemprop="image"]/@src'
                ' | //a[@id="back-cover"]/@href'
                ' | //div[contains(@class,"gallery")]//a/@href'
            )
        )
        poster = images[0] if images else None
        release_year = release_date.year if release_date else None
        release_iso = release_date.strftime("%Y-%m-%d") if release_date else None

        rating_key = build_rating_key(self.source_key, source_id)
        guid = build_guid(provider_id, rating_key)

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            Guid=[GuidItem(id=guid)],
            title=page_title,
            year=release_year,
            originallyAvailableAt=release_iso,
            studio=studio,
            summary=synopsis or None,
            duration=duration_ms,
            contentRating="X",
            isAdult=True,
            thumb=_to_absolute_url(poster) if poster else None,
            Image=[
                ImageItem(
                    url=_to_absolute_url(url),
                    type="poster" if idx == 0 else "background",
                    alt=page_title or f"TLA image {idx + 1}",
                )
                for idx, url in enumerate(images)
            ]
            or None,
            Genre=[GenreItem(tag=item) for item in genres] or None,
            Role=[RoleItem(tag=item) for item in cast] or None,
            Director=[DirectorItem(tag=item) for item in directors] or None,
            Producer=[ProducerItem(tag=item) for item in producers] or None,
            Chapter=chapters or None,
            Collection=[CollectionItem(tag=studio)] if studio else None,
        )
