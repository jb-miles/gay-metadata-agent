from __future__ import annotations

import unittest
from unittest.mock import patch

from src.config import Settings
from src.models.metadata import (
    DirectorItem,
    GenreItem,
    ImageItem,
    MetadataItem,
    ProducerItem,
    RoleItem,
)
from src.services.metadata_enrichment import assess_same_work
from src.services.metadata_service import MetadataService
from src.utils.guid import build_guid


def _settings() -> Settings:
    scraper_flags = {
        "gevi": True,
        "aebn": True,
        "tla": True,
        "gayempire": False,
        "gayhotmovies": False,
        "gayrado": False,
        "gaymovie": False,
        "hfgpm": False,
        "gayworld": False,
        "waybig": False,
        "geviscenes": False,
        "gayadultfilms": True,
        "gayadultscenes": False,
        "cduniverse": False,
        "homoactive": False,
        "bestexclusiveporn": False,
        "aventertainments": False,
        "simplyadult": False,
        "adultfilmdb": False,
        "queerclick": False,
        "fagalicious": False,
        "wolffvideo": False,
    }
    return Settings(
        host="127.0.0.1",
        port=8778,
        api_token=None,
        log_level="info",
        provider_id="tv.plex.agents.custom.jb.miles.pgmam",
        provider_title="PGMAM",
        provider_version="2.0.0",
        scraper_flags=scraper_flags,
        search_order=["gevi", "aebn", "tla"],
        artwork_prefer_front_cover=True,
        artwork_download_performer_images=True,
        artwork_max_posters=3,
        artwork_max_backgrounds=2,
        scenes_as_episodes=False,
        cache_search_ttl_seconds=3600,
        cache_metadata_ttl_seconds=86400,
        audit_log_enabled=False,
        audit_log_dir="data/audit",
        audit_http_body_enabled=False,
        audit_http_body_preview_chars=0,
    )


class FakeScraper:
    def __init__(self, matches=None, metadata_by_id=None):
        self.matches = matches or []
        self.metadata_by_id = metadata_by_id or {}

    async def search(self, title: str, year: int | None = None):
        return list(self.matches)

    async def get_metadata(self, source_id: str):
        return self.metadata_by_id[source_id]


class MetadataEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_service_uses_gevi_as_base_and_preserves_requested_identity(self):
        settings = _settings()
        requested_rating_key = "aebn-123"

        aebn_metadata = MetadataItem(
            type="movie",
            ratingKey=requested_rating_key,
            guid=build_guid(settings.provider_id, requested_rating_key),
            title="Head to Head",
            year=2026,
            studio="Falcon Studios",
            Producer=[ProducerItem(tag="Tim Valenti")],
        )
        gevi_metadata = MetadataItem(
            type="movie",
            ratingKey="gevi-1",
            guid=build_guid(settings.provider_id, "gevi-1"),
            title="Head to Head",
            year=2026,
            studio="Falcon",
            summary="A GEVI synopsis.",
            Image=[ImageItem(alt="Head to Head", type="coverPoster", url="https://img/gevi-cover.jpg")],
            Role=[RoleItem(tag="Performer A", role="Performer")],
            Director=[DirectorItem(tag="Director A")],
        )
        tla_metadata = MetadataItem(
            type="movie",
            ratingKey="tla-5",
            guid=build_guid(settings.provider_id, "tla-5"),
            title="Head to Head",
            year=2026,
            studio="Falcon Studios",
            originallyAvailableAt="2026-02-13",
            Genre=[GenreItem(tag="Feature")],
            Image=[ImageItem(alt="Head to Head", type="background", url="https://img/tla-bg.jpg")],
        )

        scrapers = {
            "aebn": FakeScraper(metadata_by_id={"123": aebn_metadata}),
            "gevi": FakeScraper(
                matches=[
                    MetadataItem(
                        ratingKey="gevi-1",
                        title="Head to Head",
                        year=2026,
                        studio="Falcon",
                    )
                ],
                metadata_by_id={"1": gevi_metadata},
            ),
            "tla": FakeScraper(
                matches=[
                    MetadataItem(
                        ratingKey="tla-5",
                        title="Head to Head",
                        year=2026,
                        studio="Falcon Studios",
                    )
                ],
                metadata_by_id={"5": tla_metadata},
            ),
        }

        with (
            patch("src.services.metadata_service.get_settings", return_value=settings),
            patch("src.services.metadata_enrichment.get_settings", return_value=settings),
            patch(
                "src.services.metadata_service.get_scraper",
                side_effect=lambda source_key: scrapers.get(source_key),
            ),
            patch(
                "src.services.metadata_enrichment.get_scraper",
                side_effect=lambda source_key: scrapers.get(source_key),
            ),
        ):
            service = MetadataService()
            metadata = await service.get(requested_rating_key)

        self.assertEqual(metadata.ratingKey, requested_rating_key)
        self.assertEqual(metadata.guid, build_guid(settings.provider_id, requested_rating_key))
        self.assertEqual(
            metadata.summary,
            "A GEVI synopsis.\n\n--------------\n\nGEVI\nproducers, AEBN\nrelease date, background, genres, TLA",
        )
        self.assertEqual(metadata.originallyAvailableAt, "2026-02-13")
        self.assertEqual([producer.tag for producer in metadata.Producer or []], ["Tim Valenti"])
        self.assertEqual([genre.tag for genre in metadata.Genre or []], ["Feature"])
        self.assertEqual(
            sorted((image.type, image.url) for image in metadata.Image or []),
            [
                ("background", "https://img/tla-bg.jpg"),
                ("coverPoster", "https://img/gevi-cover.jpg"),
            ],
        )

    def test_assess_same_work_rejects_conflicting_studio_and_year(self):
        reference = MetadataItem(
            title="Head to Head",
            year=2026,
            studio="Falcon Studios",
        )
        conflicting = MetadataItem(
            title="Head to Head",
            year=1998,
            studio="Raging Stallion",
        )

        assessment = assess_same_work(reference, conflicting)

        self.assertFalse(assessment.accepted)
        self.assertIn("studio_conflict", assessment.reasons)

    async def test_metadata_service_appends_primary_source_when_no_enrichment_occurs(self):
        settings = _settings()
        requested_rating_key = "gevi-1"
        gevi_metadata = MetadataItem(
            type="movie",
            ratingKey=requested_rating_key,
            guid=build_guid(settings.provider_id, requested_rating_key),
            title="Head to Head",
            year=2026,
            studio="Falcon",
            summary="A GEVI synopsis.",
        )

        scrapers = {
            "gevi": FakeScraper(metadata_by_id={"1": gevi_metadata}),
        }

        with (
            patch("src.services.metadata_service.get_settings", return_value=settings),
            patch("src.services.metadata_enrichment.get_settings", return_value=settings),
            patch(
                "src.services.metadata_service.get_scraper",
                side_effect=lambda source_key: scrapers.get(source_key),
            ),
            patch(
                "src.services.metadata_enrichment.get_scraper",
                side_effect=lambda source_key: scrapers.get(source_key),
            ),
        ):
            service = MetadataService()
            metadata = await service.get(requested_rating_key)

        self.assertEqual(
            metadata.summary,
            "A GEVI synopsis.\n\n--------------\n\nGEVI",
        )


if __name__ == "__main__":
    unittest.main()
