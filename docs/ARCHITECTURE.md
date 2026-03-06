# Architecture

## Overview

This project is a standalone Plex Custom Metadata Provider for gay adult media.

It replaces legacy Plex `.bundle` agents with a FastAPI service that Plex calls over HTTP:

- `POST /library/metadata/matches`
- `GET /library/metadata/{ratingKey}`
- `GET /library/metadata/{ratingKey}/images`
- `GET /`
- `GET /health`
- `GET /settings`

## Core Design

- Runtime: Python 3.11 + FastAPI
- HTTP client: `httpx`
- HTML parsing: `lxml`
- Data models: Pydantic v2
- Deployment: local process or Docker Compose

## Rating Keys

Each item uses a source-prefixed rating key:

- `gevi-12345`
- `aebn-67890`
- `tla-5057028`
- `gayrado-32700`

The metadata service parses the source prefix and dispatches to the correct scraper.

## Implemented Sources

Movie sources:

- `gevi`
- `aebn`
- `tla`
- `gayempire`
- `gayhotmovies`
- `gayrado`
- `gaymovie`
- `gayworld`
- `hfgpm`

Scene / blog / aggregator sources:

- `geviscenes`
- `waybig`
- `gayadultfilms`
- `gayadultscenes`

## Current Search Tiers

1. Tier 1: `GEVI`
2. Tier 2: `AEBN`, `TLA`
3. Tier 3: `GayEmpire`, `GayHotMovies`, `GayRado`
4. Tier 4 fallback: `GayMovie`, `GayWorld`, `HFGPM`

## Important Constraints

- Provider ID should remain stable.
- All current content is exposed as Plex movie metadata.
- IAFD integration is intentionally out of scope.
- `.env` is local-only; `.env.example` is the committed template.
