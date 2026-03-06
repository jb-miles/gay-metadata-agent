from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.metadata import MetadataItem


class BaseScraper(ABC):
    @property
    @abstractmethod
    def source_key(self) -> str:
        """Short source identifier used in rating keys."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable source name."""

    @abstractmethod
    async def search(self, title: str, year: int | None = None) -> list[MetadataItem]:
        """Search a source for matching titles."""

    @abstractmethod
    async def get_metadata(self, source_id: str) -> MetadataItem:
        """Fetch full metadata for a source-specific identifier."""

