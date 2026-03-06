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

BASE_URL = "https://www.gaydvdempire.com"
BASE_SEARCH_URL = BASE_URL + "/AllSearch/Search?view=list&q={0}&page={1}"

PRODUCT_ID_PATTERN = re.compile(r"/(\d{4,})/")
MAX_RESULTS = 20
MAX_PAGES = 10


def _clean_search_string(title: str) -> str:
    value = title.replace(" -", ":").replace("\u2013", "-").replace("\u2014", "-").lower().strip()
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


def _fix_sort_title(title: str) -> str:
    """Convert sort-order titles: 'Best of X, The' -> 'The Best of X'."""
    pattern = r",\s*(The|An|A)$"
    matched = re.search(pattern, title, re.IGNORECASE)
    if matched:
        determinate = matched.group(1)
        title = re.sub(pattern, "", title).strip()
        title = f"{determinate} {title}"
    return title


def _strip_studio_suffix(title: str, studio: str | None) -> str:
    """Remove trailing '(Studio Name)' from title if it matches the studio."""
    if not studio:
        return title
    matched = re.search(r"\(([^)]+)\)$", title)
    if matched:
        inner = matched.group(1).strip()
        if inner in studio or studio in inner:
            title = title[: matched.start()].strip()
    return title


def _parse_release_date(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = normalize_whitespace(value)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_duration_ms(value: str | None) -> int | None:
    """Parse '120 mins.' or '2 hrs. 30 mins.' to milliseconds."""
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


def _parse_scene_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d+)\s*min", normalize_whitespace(value), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _first_text(values: list[str]) -> str | None:
    for value in values:
        cleaned = normalize_whitespace(value)
        if cleaned:
            return cleaned
    return None


def _extract_scene_breakdown(tree: lxml_html.HtmlElement, cast: list[str]) -> list[dict[str, object]]:
    scene_titles = [
        normalize_whitespace(item)
        for item in tree.xpath('//div[@class="col-sm-6 m-b-1"]/h3/a[@label="Scene Title"]/text()[normalize-space()]')
    ]
    scene_duration_raw = [
        normalize_whitespace(item)
        for item in tree.xpath('//div[@class="col-sm-6 m-b-1"]/span[contains(text(), " min")]/text()[normalize-space()]')
    ]

    scenes: list[dict[str, object]] = []
    cast_index = {name.lower(): name for name in cast}

    for idx, title in enumerate(scene_titles, start=1):
        duration_minutes = _parse_scene_minutes(scene_duration_raw[idx - 1] if idx - 1 < len(scene_duration_raw) else None)
        detected_cast: list[str] = []
        lowered_title = title.lower()
        for name_lower, original_name in cast_index.items():
            if re.search(rf"\b{re.escape(name_lower)}\b", lowered_title):
                detected_cast.append(original_name)

        scenes.append(
            {
                "number": idx,
                "title": title,
                "duration_minutes": duration_minutes,
                "cast": detected_cast,
            }
        )

    return scenes


def _append_scene_breakdown(summary: str | None, scenes: list[dict[str, object]]) -> str | None:
    base_summary = normalize_whitespace(summary or "")
    if not scenes:
        return base_summary or None

    lines: list[str] = ["Scene Breakdown:"]
    for scene in scenes:
        number = scene["number"]
        title = scene["title"]
        duration_minutes = scene["duration_minutes"]
        scene_cast = scene["cast"]

        line = f"{number}. {title}"
        if duration_minutes:
            line += f" ({duration_minutes} min)"
        lines.append(line)
        if scene_cast:
            lines.append(f"Cast: {', '.join(scene_cast)}")

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
        title = f"Scene {scene['number']}: {scene['title']}"
        chapters.append(
            ChapterItem(
                title=title,
                startTimeOffset=cursor,
                endTimeOffset=cursor + scene_ms,
            )
        )
        cursor += scene_ms
    return chapters


class GayEmpireScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        # Set age-gate cookies on the shared client for this domain.
        self._client.cookies.set("ageConfirmed", "true", domain="www.gaydvdempire.com")
        self._movie_urls: dict[str, str] = {}

    @property
    def source_key(self) -> str:
        return "gayempire"

    @property
    def source_name(self) -> str:
        return "Gay Empire"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        encoded = _clean_search_string(title)

        results: list[MetadataItem] = []

        for page_num in range(MAX_PAGES):
            if len(results) >= MAX_RESULTS:
                break

            search_url = BASE_SEARCH_URL.format(encoded, page_num)
            try:
                response = await self._client.get(search_url, timeout=30.0)
                response.raise_for_status()
            except Exception:
                logger.exception("GayEmpire search failed for %s", search_url)
                break

            tree = lxml_html.fromstring(response.text)
            film_nodes = tree.xpath('.//div[contains(@id, "_Item")]')
            if not film_nodes:
                break

            for node in film_nodes:
                if len(results) >= MAX_RESULTS:
                    break

                film_title = _first_text(node.xpath('.//a[@category and @label="Title"]/text()'))
                if not film_title:
                    continue
                film_title = _fix_sort_title(film_title.strip())

                href = _first_text(node.xpath('.//a[@category and @label="Title"]/@href'))
                if not href:
                    continue
                film_url = _to_absolute_url(href)

                id_match = PRODUCT_ID_PATTERN.search(href)
                if not id_match:
                    continue
                product_id = id_match.group(1)
                self._movie_urls[product_id] = film_url

                studio = _first_text(node.xpath('.//a[@category and @label="Studio Link"]/@title'))
                film_title = _strip_studio_suffix(film_title, studio)

                production_year: int | None = None
                year_text = _first_text(node.xpath('.//a[@category and @label="Title"]/following-sibling::text()'))
                if year_text:
                    yr_match = re.search(r"\((\d{4})\)", year_text)
                    if yr_match:
                        production_year = int(yr_match.group(1))

                release_text = _first_text(node.xpath('.//small[text()="released"]/following-sibling::text()'))
                release_date = _parse_release_date(release_text)
                if release_date and not production_year:
                    production_year = release_date.year

                thumb = _first_text(node.xpath('.//img/@src'))

                if year and production_year and year != production_year:
                    continue

                rating_key = build_rating_key(self.source_key, product_id)
                guid = build_guid(provider_id, rating_key)

                results.append(
                    MetadataItem(
                        type="movie",
                        ratingKey=rating_key,
                        guid=guid,
                        title=normalize_whitespace(film_title),
                        year=production_year,
                        studio=normalize_whitespace(studio) if studio else None,
                        thumb=_to_absolute_url(thumb) if thumb else None,
                    )
                )

            next_link = _first_text(tree.xpath('.//a[@title="Next"]/@href'))
            if not next_link:
                break

        logger.info("GayEmpire search for %r returned %d results", title, len(results))
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
        if page_title:
            page_title = _fix_sort_title(page_title.strip())
        if not page_title:
            page_title = _first_text(tree.xpath('//title/text()'))
            if page_title and "|" in page_title:
                page_title = page_title.split("|")[0].strip()

        # Studio — use the product info section to avoid nav links
        studio = _first_text(
            tree.xpath('//small[contains(text(),"Studio")]/following-sibling::a/text()')
        )
        if not studio:
            studio = _first_text(
                tree.xpath('//ul[@class="list-unstyled m-b-2"]//a[contains(@label,"Studio")]/text()')
            )
        if not studio:
            studio = _first_text(tree.xpath('//a[contains(@label, "Studio - Details")]/text()'))
        if page_title and studio:
            page_title = _strip_studio_suffix(page_title, studio)

        # Synopsis
        synopsis_parts = tree.xpath(
            '//div[@class="col-xs-12 text-center p-y-2 bg-lightgrey"]/div//p/text()'
        )
        synopsis = normalize_whitespace(" ".join(p.strip() for p in synopsis_parts if p.strip()))
        synopsis = re.sub(r"<[^<]+?>", "", synopsis).strip()

        # Directors
        directors = _dedupe(
            tree.xpath('//a[contains(@label, "Director - details")]/text()[normalize-space()]')
        )

        # Producers
        producers = _dedupe(
            tree.xpath('//li[.//small[contains(text(),"Producer")]]/text()[normalize-space()]')
        )

        # Cast
        cast = _dedupe(
            tree.xpath('//a[@class="PerformerName" and @label="Performers - detail"]/text()')
        )

        # Genres
        raw_genres = _dedupe(
            tree.xpath('//ul[@class="list-unstyled m-b-2"]//a[@label="Category"]/text()[normalize-space()]')
        )

        # Release date — detail page uses <small>Released:</small> text
        release_text = _first_text(
            tree.xpath('//small[contains(text(),"Released")]/following-sibling::text()')
        )
        release_date = _parse_release_date(release_text)

        # Production year — fallback if no release date
        if not release_date:
            year_text = _first_text(
                tree.xpath('//small[contains(text(),"Production Year")]/following-sibling::text()')
            )
            if year_text:
                yr_match = re.search(r"(\d{4})", year_text)
                if yr_match:
                    release_date = datetime(int(yr_match.group(1)), 12, 31)

        # Duration — detail page uses <small>Length: </small> text
        duration_text = _first_text(
            tree.xpath('//small[contains(text(),"Length")]/following-sibling::text()')
        )
        duration_ms = _parse_duration_ms(duration_text)
        scenes = _extract_scene_breakdown(tree, cast)
        chapters = _build_chapters_from_scenes(scenes, duration_ms)
        synopsis = _append_scene_breakdown(synopsis, scenes)

        # Images
        poster = _first_text(tree.xpath('//img[@itemprop="image"]/@src'))
        back_cover = _first_text(tree.xpath('//a[@id="back-cover"]/@href'))

        release_year = release_date.year if release_date else None
        release_iso = release_date.strftime("%Y-%m-%d") if release_date else None

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
