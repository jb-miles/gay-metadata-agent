# Gay Adult Metadata Agent for Plex

FastAPI-based Plex Custom Metadata Provider for gay adult media metadata.

## What This Is

This repository contains a standalone Plex metadata service for gay adult media. It replaces the deprecated Python 2.7 `.bundle` agents with a FastAPI service that Plex talks to over HTTP.

Internal legacy working name: `PGMAM`. The public-facing project name is now `Gay Adult Metadata Agent for Plex`.

Current implemented sources include:
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
- aggregators: `GayAdultFilms`, `GayAdultScenes`

## Repo Layout

- `src/`: FastAPI app, models, routes, scrapers, services, utilities
- `tests/`: test package scaffold
- `docs/`: architecture and roadmap notes
- `data/`: local runtime data mount, intentionally not committed

See:
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
- [`docs/ROADMAP.md`](./docs/ROADMAP.md)
- [`CONTRIBUTING.md`](./CONTRIBUTING.md)

## Local Setup

1. Copy `.env.example` to `.env`.
2. Adjust any local settings in `.env`.
3. Install dependencies with `pip install -r requirements.txt`.
4. Run the app with `uvicorn src.main:app --reload --host 127.0.0.1 --port 8778`.
5. Verify:
   - `GET /`
   - `GET /health`
   - `GET /settings`

## Docker Compose

The checked-in Compose workflow is the default local runtime:

```bash
cp .env.example .env
docker compose up -d --build
```

The service is published only on `127.0.0.1:8778`.

Useful commands:

```bash
docker compose ps
docker compose logs -f
docker compose down
```

## Plex Registration

Register the provider with Plex Media Server:

```bash
python register.py --pms-token YOUR_PLEX_TOKEN
```

Optional flags:

```bash
python register.py \
  --pms-url http://localhost:32400 \
  --pms-token YOUR_PLEX_TOKEN \
  --provider-url http://localhost:8778
```

## Ad Hoc Docker

If you need a one-off container outside Compose:

```bash
docker build -t pgmam-provider .
docker run --rm -p 8778:8778 --name pgmam-provider pgmam-provider
```

## Repo Hygiene

Local-only files are intentionally ignored:
- `.env`
- `data/`
- Python cache directories
- test/editor cache artifacts

Use `.env.example` as the committed configuration template.
