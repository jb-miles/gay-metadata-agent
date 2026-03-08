# Gay Adult Metadata Agent for Plex

Standalone Plex Custom Metadata Provider for gay adult media.

This project replaces the legacy Python 2.7 Plex `.bundle` agents with a FastAPI service that Plex Media Server talks to over HTTP. Plex deprecated the old plugin framework and plans to remove legacy agent support in 2026. This provider is the migration path.

See also:
- [STATUS.md](./STATUS.md)
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
- [docs/ROADMAP.md](./docs/ROADMAP.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)

## Current Status

Implemented and working today:
- provider service, health endpoint, registration flow, Docker packaging
- multi-source matching and metadata fetch
- movie enrichment pipeline with GEVI-first reconciliation
- summary source citations showing primary source plus secondary field enhancements

Implemented sources:
- `GEVI`
- `AEBN`
- `TLA`
- `GayEmpire`
- `GayHotMovies`
- `GayRado`
- `GayMovie`
- `GayWorld`
- `HFGPM`
- `GEVIScenes`
- `WayBig`
- aggregators: `GayAdultFilms`, `GayAdultScenes`

Current work:
- metadata completeness pass across tier 1-3 movie sources
- normalization of release dates, producers, and scene/chapter behavior

## Requirements

- Plex Media Server `1.43.0+`
- Docker and Docker Compose plugin
- a Plex auth token for provider registration
- local network access from Plex to this provider

The default deployment binds the provider to `127.0.0.1:8778`, which is the right setup when Plex and the provider run on the same host.

## Quick Start

```bash
git clone https://github.com/jb-miles/gay-metadata-agent.git
cd gay-metadata-agent
cp .env.example .env
docker compose up -d --build
```

After the container is up, register the provider with Plex:

```bash
python register.py --pms-token YOUR_PLEX_TOKEN
```

Then verify:

```bash
curl http://127.0.0.1:8778/health
curl "http://127.0.0.1:32400/media/providers/metadata?X-Plex-Token=YOUR_PLEX_TOKEN"
```

## Deployment Guide

### 1. Clone the Repository

```bash
git clone https://github.com/jb-miles/gay-metadata-agent.git
cd gay-metadata-agent
```

### 2. Configure the Provider

Copy the example environment file:

```bash
cp .env.example .env
```

The defaults are usable for same-host deployment. The most important settings are:
- `HOST=127.0.0.1`
- `PORT=8778`
- `PROVIDER_ID=tv.plex.agents.custom.jb.miles.pgmam`
- `SEARCH_ORDER=gevi,aebn,tla,gayempire,gayhotmovies,gayrado,waybig,geviscenes,gaymovie,hfgpm,gayworld`

You can also enable or disable individual scrapers in `.env`.

### 3. Build and Start the Service

```bash
docker compose up -d --build
```

Useful commands:

```bash
docker compose ps
docker compose logs -f
docker compose down
```

### 4. Get Your Plex Token

You need a Plex token to register the provider.

Common ways to get it:
- from an existing Plex Web request in your browser developer tools
- from the local Plex `Preferences.xml` on the PMS host
- from another trusted Plex automation already using `X-Plex-Token`

### 5. Register the Provider with Plex

Minimal form:

```bash
python register.py --pms-token YOUR_PLEX_TOKEN
```

Explicit form:

```bash
python register.py \
  --pms-url http://127.0.0.1:32400 \
  --pms-token YOUR_PLEX_TOKEN \
  --provider-url http://127.0.0.1:8778
```

If registration succeeds, Plex will list the provider under `/media/providers/metadata`.

### 6. Verify the Service

Check provider health:

```bash
curl http://127.0.0.1:8778/health
```

Expected result:

```json
{"status":"ok","version":"2.0.0"}
```

Confirm Plex sees the provider:

```bash
curl "http://127.0.0.1:32400/media/providers/metadata?X-Plex-Token=YOUR_PLEX_TOKEN"
```

### 7. Use It in Plex

Create or edit a movie library in Plex and select the custom provider:
- Provider ID: `tv.plex.agents.custom.jb.miles.pgmam`
- Media type: currently all supported content is exposed as Plex movie metadata

This project currently treats scenes as movies for compatibility with existing libraries.

## Upgrading

Pull the latest code and rebuild:

```bash
git pull
docker compose up -d --build
```

You generally do not need to re-register the provider unless:
- the provider URL changes
- the provider ID changes
- Plex loses the registration entry

## Local Development

If you do not want to use Docker:

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --host 127.0.0.1 --port 8778
```

## Known Limitations

- only Plex movie metadata is supported right now
- provider preferences are managed through `.env`, not Plex UI preferences
- IAFD integration is intentionally out of scope
- several lower-priority legacy sources are not yet ported
- performer thumbnails are planned but not implemented yet

## License

See [LICENSE](./LICENSE).
