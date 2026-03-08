# Project Status

## Summary

The project is past the initial porting stage and into metadata-quality work.

The provider is already usable:
- it runs as a standalone FastAPI service
- it registers with Plex Media Server as a custom metadata provider
- it serves live match and metadata responses
- it supports multi-source enrichment for movie metadata

## What Has Been Done

Completed phases:
- Phase 1: service scaffold, config loading, models, health checks, registration flow
- Phase 2: GEVI implementation
- Phase 3: AEBN, GEVIScenes, WayBig, and aggregator support
- Phase 4: GayEmpire, GayHotMovies, GayWorld, GayMovie, HFGPM
- Phase 5: TLA and GayRado

Key completed capabilities:
- source-prefixed rating keys and GUID handling
- multi-source match aggregation and deduplication
- live metadata fetch for implemented sources
- GEVI-first movie enrichment with selected match identity preserved
- producer capture where available
- scene breakdown capture for supported sources
- chapter generation when scene durations reconcile with runtime
- source citations appended at the bottom of summary metadata

## What We Are Doing Now

Current phase:
- Phase 6: Metadata Completeness Pass

Current focus:
- audit tier 1-3 movie sources for visible but unmapped fields
- normalize release date versus production year handling
- normalize producer behavior across sources
- normalize scene breakdown and chapter rules
- decide which source-specific extras belong in Plex fields versus summary text

## What We Intend To Do Next

Planned next phases:
- Phase 7: performer thumbnails
- Phase 8: bottom-tier source expansion
- Phase 9: studio-aware source prioritization
- Phase 10: settings and general polish

Planned low-priority source additions:
- `CDUniverse`
- `HomoActive`
- `BestExclusivePorn`
- `AVEntertainments`
- `SimplyAdult`
- `AdultFilmDatabase`
- `QueerClick`
- `Fagalicious`
- `WolffVideo`

## Implemented Sources

Movie-oriented sources:
- `GEVI`
- `AEBN`
- `TLA`
- `GayEmpire`
- `GayHotMovies`
- `GayRado`
- `GayMovie`
- `GayWorld`
- `HFGPM`

Scene / review / aggregator sources:
- `GEVIScenes`
- `WayBig`
- `GayAdultFilms`
- `GayAdultScenes`

## Deployment Position

This repo is at a point where users can reasonably:
- clone it
- configure `.env`
- start it with Docker Compose
- register it with Plex
- begin testing it on a Plex movie library

What still needs improvement is polish, not basic viability:
- richer public docs over time
- broader source coverage
- more metadata normalization
- optional settings UX

## Recommended Messaging For A Release Post

Suggested framing:
- this is an actively usable custom metadata provider, not just a prototype
- the migration from legacy `.bundle` agents is well underway
- current work is about metadata completeness and quality, not basic architecture
- additional sources and thumbnails are planned, but the provider is already deployable today
