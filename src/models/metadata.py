from __future__ import annotations

from pydantic import BaseModel


class ImageItem(BaseModel):
    alt: str
    type: str
    url: str


class GenreItem(BaseModel):
    tag: str


class RoleItem(BaseModel):
    tag: str
    role: str | None = None
    thumb: str | None = None


class DirectorItem(BaseModel):
    tag: str


class ProducerItem(BaseModel):
    tag: str


class CollectionItem(BaseModel):
    tag: str


class GuidItem(BaseModel):
    id: str


class ChapterItem(BaseModel):
    title: str
    startTimeOffset: int
    endTimeOffset: int


class MetadataItem(BaseModel):
    type: str | None = None
    ratingKey: str | None = None
    guid: str | None = None
    title: str | None = None
    year: int | None = None
    thumb: str | None = None
    summary: str | None = None
    originallyAvailableAt: str | None = None
    studio: str | None = None
    duration: int | None = None
    contentRating: str | None = None
    isAdult: bool | None = None
    Image: list[ImageItem] | None = None
    Genre: list[GenreItem] | None = None
    Role: list[RoleItem] | None = None
    Director: list[DirectorItem] | None = None
    Producer: list[ProducerItem] | None = None
    Chapter: list[ChapterItem] | None = None
    Collection: list[CollectionItem] | None = None
    Guid: list[GuidItem] | None = None
