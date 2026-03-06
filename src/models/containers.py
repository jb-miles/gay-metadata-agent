from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.metadata import MetadataItem


class MatchRequest(BaseModel):
    type: int = Field(default=1)
    title: str | None = None
    year: int | None = None
    manual: int | None = None
    guid: str | None = None


class MediaContainer(BaseModel):
    offset: int = 0
    totalSize: int = 0
    identifier: str
    size: int = 0
    Metadata: list[MetadataItem] = Field(default_factory=list)


class MediaContainerEnvelope(BaseModel):
    MediaContainer: MediaContainer


class HealthResponse(BaseModel):
    status: str
    version: str

