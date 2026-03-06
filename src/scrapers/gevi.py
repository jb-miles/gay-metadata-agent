from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime

import httpx
from lxml import html as lxml_html

from src.config import get_settings
from src.models.metadata import (
    CollectionItem,
    DirectorItem,
    GenreItem,
    GuidItem,
    ImageItem,
    MetadataItem,
    RoleItem,
)
from src.scrapers.base import BaseScraper
from src.scrapers.enrichment import (
    EnrichmentData,
    enrich_from_aebn,
    enrich_from_gayempire,
    enrich_from_gayhotmovies,
)
from src.utils.guid import build_guid, build_rating_key
from src.utils.text import strip_diacritics

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gayeroticvideoindex.com"
BASE_SEARCH_URL = (
    BASE_URL
    + "/shtt.php?draw=4"
    "&columns[0][data]=0&columns[0][name]=title&columns[0][searchable]=true&columns[0][orderable]=true"
    "&columns[0][search][value]=&columns[0][search][regex]=false"
    "&columns[1][data]=1&columns[1][name]=release&columns[1][searchable]=true&columns[1][orderable]=true"
    "&columns[1][search][value]={2}&columns[1][search][regex]=false"
    "&columns[2][data]=2&columns[2][name]=company&columns[2][searchable]=true&columns[2][orderable]=true"
    "&columns[2][search][value]=&columns[2][search][regex]=false"
    "&columns[3][data]=3&columns[3][name]=line&columns[3][searchable]=true&columns[3][orderable]=true"
    "&columns[3][search][value]=&columns[3][search][regex]=false"
    "&columns[4][data]=4&columns[4][name]=type&columns[4][searchable]=true&columns[4][orderable]=true"
    "&columns[4][search][value]=show+compilation&columns[4][search][regex]=false"
    "&columns[5][data]=5&columns[5][name]=rating&columns[5][searchable]=true&columns[5][orderable]=true"
    "&columns[5][search][value]=&columns[5][search][regex]=false"
    "&columns[6][data]=6&columns[6][name]=category&columns[6][searchable]=true&columns[6][orderable]=true"
    "&columns[6][search][value]=&columns[6][search][regex]=false"
    "&order[0][column]=0&order[0][dir]=asc"
    "&start={0}&length=100"
    "&search[value]={1}&search[regex]=false"
    "&_=1676140164112"
)

TITLE_PATTERN = re.compile(r"<a href='(?P<url>.*?)'>(?P<title>.*?)</a>")
VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)")
SYNOPSIS_STRIP = re.compile(
    r"View this scene at.*|found in compilation.*|see also.*|^\d+\.$",
    re.IGNORECASE | re.MULTILINE,
)

MAX_RESULTS = 20
MAX_PAGES = 10


def _clean_search_string(title: str) -> tuple[str, str]:
    """Clean a title for the GEVI search API. Returns (encoded_title, search_type)."""
    value = title.lower().strip()

    # Detect containing vs starting-with search
    search_type = "containing" if "~~" in value else "starting+with"
    value = value.replace("~~", "")

    # Replace connectors with space
    value = value.replace(" & ", " ").replace(" and ", " ").replace("'s ", " ").replace("'t ", " ")

    # Remove specific characters
    value = re.sub(r"[,!#+=%s]" % re.escape("\u00b2"), "", value)

    # Replace specific characters with space
    value = re.sub(r"[@\-\u2013\u2014()\.'%s]" % re.escape("\u2019"), " ", value)

    # Collapse whitespace
    value = " ".join(value.split())

    # Preserve ½ through diacritics stripping
    has_half = "\u00bd" in value
    value = strip_diacritics(value)
    if has_half:
        value = value.replace("12", "\u00bd")

    value = urllib.parse.quote(value.strip())

    # Fix double encoding
    value = value.replace("%25", "%").replace("*", "").replace("%2A", "+")

    return value, search_type


class GEVIScraper(BaseScraper):
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    @property
    def source_key(self) -> str:
        return "gevi"

    @property
    def source_name(self) -> str:
        return "Gay Erotic Video Index"

    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        settings = get_settings()
        provider_id = settings.provider_id
        encoded_title, search_type = _clean_search_string(title)

        results: list[MetadataItem] = []
        start_record = 0

        for page in range(1, MAX_PAGES + 1):
            if len(results) >= MAX_RESULTS:
                break

            url = BASE_SEARCH_URL.format(start_record, encoded_title, search_type)
            start_record = page * 100

            try:
                resp = await self._client.get(
                    url,
                    headers={"Referer": "https://gayeroticvideoindex.com/search"},
                    timeout=20.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                logger.exception("GEVI search request failed (page %d)", page)
                break

            films_list = data.get("data")
            if not films_list:
                break

            total_filtered = data.get("recordsFiltered", len(films_list))

            for film in films_list:
                if len(results) >= MAX_RESULTS:
                    break

                try:
                    matched = TITLE_PATTERN.search(film[0])
                    if not matched:
                        continue

                    film_url = matched.group("url")
                    film_title = matched.group("title")

                    # Strip [sic] / (sic)
                    film_title = film_title.replace("[sic]", "").replace("(sic)", "").strip()

                    # Extract GEVI numeric ID
                    id_match = VIDEO_ID_PATTERN.search(film_url)
                    if not id_match:
                        continue
                    gevi_id = id_match.group(1)

                    # Year
                    film_year = None
                    if film[1]:
                        try:
                            raw_year = str(film[1]).strip()
                            # Handle ranges like "1995-97" — take first year
                            if "-" in raw_year:
                                raw_year = raw_year.split("-")[0].strip()
                            # Handle "cYYYY"
                            raw_year = raw_year.lstrip("c")
                            # Handle "YYYY,YYYY"
                            if "," in raw_year:
                                raw_year = raw_year.split(",")[0].strip()
                            film_year = int(raw_year) if raw_year.isdigit() else None
                        except (ValueError, IndexError):
                            pass

                    # Studio from company HTML
                    studio = None
                    if film[2]:
                        try:
                            studio = film[2].split(">", 1)[1].split("<")[0].strip()
                        except (IndexError, AttributeError):
                            studio = str(film[2]).strip() if film[2] else None

                    rating_key = build_rating_key("gevi", gevi_id)
                    guid = build_guid(provider_id, rating_key)

                    results.append(
                        MetadataItem(
                            type="movie",
                            ratingKey=rating_key,
                            guid=guid,
                            title=film_title,
                            year=film_year,
                            studio=studio,
                        )
                    )

                except Exception:
                    logger.exception("Error parsing GEVI search result")
                    continue

            if start_record >= total_filtered:
                break

        logger.info("GEVI search for %r returned %d results", title, len(results))
        return results

    async def get_metadata(self, source_id: str) -> MetadataItem:
        settings = get_settings()
        provider_id = settings.provider_id

        film_url = f"{BASE_URL}/video/{source_id}"
        resp = await self._client.get(
            film_url,
            headers={"Referer": "https://gayeroticvideoindex.com/search"},
            timeout=30.0,
        )
        resp.raise_for_status()
        tree = lxml_html.fromstring(resp.text)

        # --- Studio ---
        studio = None
        try:
            studios = tree.xpath('//a[contains(@href, "company/")]/text()[normalize-space()]')
            studios = [x.strip() for x in studios if x.strip()]
            if studios:
                studio = studios[0]
        except Exception:
            logger.debug("No studio found for gevi-%s", source_id)

        # --- Title ---
        film_title = None
        try:
            title_el = tree.xpath("//title/text()")
            if title_el:
                raw_title = title_el[0].strip()
                # Strip the site suffix ": Gay Erotic Video Index"
                suffix = ": Gay Erotic Video Index"
                if raw_title.endswith(suffix):
                    raw_title = raw_title[: -len(suffix)]
                film_title = raw_title.strip() or None
        except Exception:
            pass

        # --- Synopsis ---
        synopsis = ""
        try:
            parts = tree.xpath(
                '//div[contains(@class,"text-justify wideCols-1")]/p[@class="mb-2"]/span[@style]/text()'
            )
            synopsis = "\n".join(parts).strip()
            if synopsis:
                synopsis = SYNOPSIS_STRIP.sub("", synopsis).strip()
        except Exception:
            logger.debug("No synopsis from GEVI for gevi-%s", source_id)

        # --- Directors ---
        directors: list[str] = []
        try:
            raw = tree.xpath('//a[contains(@href, "director/")]/text()')
            directors = list({x.split("(")[0].strip() for x in raw if x.strip()})
            directors.sort(key=str.lower)
        except Exception:
            logger.debug("No directors from GEVI for gevi-%s", source_id)

        # --- Cast ---
        cast: list[str] = []
        try:
            raw = tree.xpath('//a[contains(@href, "performer/")]//text()')
            cast = list({x.split("(")[0].strip() for x in raw if x.strip()})
            cast.sort(key=str.lower)
        except Exception:
            logger.debug("No cast from GEVI for gevi-%s", source_id)

        # --- Genres ---
        genres: set[str] = set()
        try:
            body_types = tree.xpath('//div[.="Body Type:"]/following-sibling::div/div/text()')
            if body_types:
                raw_bt = body_types[0].strip().replace(";", ",")
                genres.update(x.strip() for x in raw_bt.split(",") if x.strip())
        except Exception:
            pass
        try:
            categories = tree.xpath('//div[.="Category:"]/following-sibling::div/text()')
            if categories:
                genres.update(x.strip() for x in categories[0].strip().split(",") if x.strip())
        except Exception:
            pass
        try:
            types = tree.xpath('//div[.="Type:"]/following-sibling::div/div/text()')
            if types:
                raw_t = types[0].strip().replace(";", ",")
                genres.update(x.strip() for x in raw_t.split(",") if x.strip())
        except Exception:
            pass

        # --- Countries ---
        countries: list[str] = []
        try:
            loc = tree.xpath('//div[.="Location:"]/following-sibling::div/text()')
            if loc:
                countries = [x.strip() for x in loc[0].strip().split(",") if x.strip()]
        except Exception:
            pass

        # --- Release Date ---
        release_date = _extract_gevi_release_date(tree)

        # --- Duration (minutes → milliseconds) ---
        duration_ms = _extract_gevi_duration(tree)

        # --- Rating (out of 4 stars → 0-10 scale) ---
        rating = _extract_gevi_rating(tree)

        # --- Images ---
        poster_urls: list[str] = []
        art_urls: list[str] = []
        try:
            img_srcs = tree.xpath('//img/@src[contains(.,"Covers/")]')
            images = []
            for src in img_srcs:
                url = src.replace("/Icons/", "/")
                if not url.startswith("http"):
                    url = BASE_URL + "/" + url.lstrip("/")
                if url not in images:
                    images.append(url)
            if images:
                poster_urls = [images[0]]
                art_urls = [images[1] if len(images) > 1 else images[0]]
        except Exception:
            logger.debug("No images from GEVI for gevi-%s", source_id)

        # --- External Enrichment ---
        enrichment = await self._enrich_from_external_links(tree)
        if enrichment:
            if not synopsis and enrichment.synopsis:
                synopsis = enrichment.synopsis
            if enrichment.directors:
                merged = set(d.lower() for d in directors)
                for d in enrichment.directors:
                    if d.lower() not in merged:
                        directors.append(d)
                        merged.add(d.lower())
                directors.sort(key=str.lower)
            if enrichment.cast:
                merged = set(c.lower() for c in cast)
                for c in enrichment.cast:
                    if c.lower() not in merged:
                        cast.append(c)
                        merged.add(c.lower())
                cast.sort(key=str.lower)
            if enrichment.genres:
                genres.update(enrichment.genres)
            if enrichment.poster_urls:
                poster_urls.extend(enrichment.poster_urls)
            if enrichment.art_urls:
                art_urls.extend(enrichment.art_urls)

        # --- Build MetadataItem ---
        rating_key = build_rating_key("gevi", source_id)
        guid = build_guid(provider_id, rating_key)

        genre_list = sorted(genres, key=str.lower)

        # Respect artwork limits from settings
        max_posters = settings.artwork_max_posters
        max_bgs = settings.artwork_max_backgrounds

        image_items: list[ImageItem] = []
        for url in poster_urls[:max_posters]:
            image_items.append(ImageItem(alt=film_title or "", type="coverPoster", url=url))
        for url in art_urls[:max_bgs]:
            image_items.append(ImageItem(alt=film_title or "", type="background", url=url))

        release_year = None
        originally_available = None
        if release_date:
            release_year = release_date.year
            originally_available = release_date.strftime("%Y-%m-%d")

        return MetadataItem(
            type="movie",
            ratingKey=rating_key,
            guid=guid,
            title=film_title,
            year=release_year,
            originallyAvailableAt=originally_available,
            summary=synopsis or None,
            studio=studio,
            duration=duration_ms,
            contentRating="X",
            isAdult=True,
            Image=image_items or None,
            Genre=[GenreItem(tag=g) for g in genre_list] or None,
            Role=[RoleItem(tag=name, role="Performer") for name in cast] or None,
            Director=[DirectorItem(tag=name) for name in directors] or None,
            Collection=[CollectionItem(tag=studio)] if studio else None,
            Guid=[GuidItem(id=guid)],
        )

    async def _enrich_from_external_links(self, tree) -> EnrichmentData | None:
        """Follow external links on a GEVI page to AEBN/GayHotMovies/GayEmpire for extra metadata."""
        try:
            all_links = tree.xpath("//a/@href")
            external_links = [x.strip() for x in all_links if ".com" in x]
        except Exception:
            return None

        web_links: dict[str, str] = {}
        for link in external_links:
            if "aebn" in link and "AEBN" not in web_links:
                web_links["AEBN"] = link
            elif "gayhotmovies" in link and "GayHotMovies" not in web_links:
                web_links["GayHotMovies"] = link
            elif "empire" in link and "GayEmpire" not in web_links:
                web_links["GayEmpire"] = link

        enrichment_funcs = {
            "AEBN": enrich_from_aebn,
            "GayHotMovies": enrich_from_gayhotmovies,
            "GayEmpire": enrich_from_gayempire,
        }

        for key in ["AEBN", "GayHotMovies", "GayEmpire"]:
            if key not in web_links:
                continue
            url = web_links[key]
            try:
                resp = await self._client.get(url, timeout=60.0)
                resp.raise_for_status()
                ext_tree = lxml_html.fromstring(resp.text)
                result = enrichment_funcs[key](ext_tree)
                logger.info("Enriched gevi metadata from %s: %s", key, url)
                return result
            except Exception:
                logger.debug("Failed to enrich from %s at %s", key, url)
                continue

        return None


def _extract_gevi_release_date(tree) -> datetime | None:
    """Extract the earliest release/production date from a GEVI film page."""
    try:
        all_td = tree.xpath("//td/text()[normalize-space()]")
        raw_dates: list[str] = []

        if "Gay Erotic Video Index" in all_td:
            # Format 1: tabular layout (e.g., "Bring Me a Boy 68")
            idxs = [i for i, x in enumerate(all_td) if x in ("released", "produced")]
            raw_dates = [all_td[i + 1] for i in idxs if i + 1 < len(all_td)]
        else:
            # Format 2: standard layout
            raw_dates = tree.xpath(
                '//td[a[contains(@href,"company/")]]/following-sibling::td[1]/text()[normalize-space()]'
            )
            try:
                produced = tree.xpath(
                    '//div[contains(.,"Produced")]/following-sibling::div[1]/text()[normalize-space()]'
                )
                if produced:
                    raw_dates.append(produced[0])
            except Exception:
                pass

        parsed_dates: list[datetime] = []
        for item in raw_dates:
            item = item.strip()
            if not item or item == "?":
                continue
            if "c" in item:
                item = item.replace("c", "")
            elif "," in item:
                item = item.split(",")[1].strip()
            elif "-" in item:
                parts = [x.strip() for x in item.split("-")]
                if len(parts) == 2 and parts[0] and parts[1]:
                    if len(parts[1]) == 1:
                        item = parts[0][:3] + parts[1]
                    elif len(parts[1]) == 2:
                        item = parts[0][:2] + parts[1]
                    else:
                        item = parts[1]

            item = item.strip()
            if not item or not item.isdigit():
                continue

            if len(item) <= 2:
                compare_year = datetime.now().year + 2
                century = 20 if int(item) <= compare_year % 100 else 19
                item = f"{century}{item}"

            try:
                parsed_dates.append(datetime.strptime(f"{item}1231", "%Y%m%d"))
            except ValueError:
                continue

        return min(parsed_dates) if parsed_dates else None

    except Exception:
        logger.debug("Failed to extract GEVI release date")
        return None


def _extract_gevi_duration(tree) -> int | None:
    """Extract duration from GEVI page. Returns milliseconds or None."""
    try:
        all_td = tree.xpath("//td/text()[normalize-space()]")
        durations: list[int] = []

        if "Gay Erotic Video Index" in all_td:
            idxs = [i for i, x in enumerate(all_td) if x == "length"]
            for i in idxs:
                if i + 1 < len(all_td):
                    val = all_td[i + 1].strip()
                    if val.isdigit():
                        durations.append(int(val))
        else:
            raw = tree.xpath(
                '//td[a[contains(@href,"company/")]]/following-sibling::td[2]/text()[normalize-space()]'
            )
            for val in raw:
                val = val.strip()
                if val.isdigit():
                    durations.append(int(val))

        if durations:
            return max(durations) * 60 * 1000  # minutes → milliseconds
        return None

    except Exception:
        logger.debug("Failed to extract GEVI duration")
        return None


def _extract_gevi_rating(tree) -> float:
    """Extract star rating from GEVI. Returns 0-10 scale."""
    try:
        raw = tree.xpath(
            '//div[.="Rating Out of 4:"]/following-sibling::div/text()'
            '|//div[.="Rating Out of 4:"]/following-sibling::div/div/text()'
        )
        star_counts: list[int] = []
        for x in raw:
            if x == "Produced:":
                break
            if "*" in x:
                star_counts.append(x.count("*"))

        if star_counts:
            avg = sum(star_counts) / len(star_counts)
            return round(avg * 10 / 4, 1)  # 4-star scale → 10-point scale
        return 0.0

    except Exception:
        return 0.0
