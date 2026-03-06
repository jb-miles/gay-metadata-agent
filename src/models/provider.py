from __future__ import annotations

from pydantic import BaseModel, Field


class SchemeDefinition(BaseModel):
    scheme: str


class SupportedType(BaseModel):
    type: int
    Scheme: list[SchemeDefinition] = Field(default_factory=list)


class FeatureDefinition(BaseModel):
    type: str
    key: str


class MediaProvider(BaseModel):
    identifier: str
    title: str
    version: str
    Types: list[SupportedType] = Field(default_factory=list)
    Feature: list[FeatureDefinition] = Field(default_factory=list)


class MediaProviderEnvelope(BaseModel):
    MediaProvider: MediaProvider

