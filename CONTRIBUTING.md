# Contributing

## Local Development

1. Copy `.env.example` to `.env`.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the provider:

```bash
uvicorn src.main:app --reload --host 127.0.0.1 --port 8778
```

Or use Docker Compose:

```bash
docker compose up -d --build
```

## Verification

Minimum verification for scraper work:

1. `GET /health` returns `200`.
2. `POST /library/metadata/matches` returns plausible ranked results.
3. `GET /library/metadata/{ratingKey}` returns the expected metadata for at least one live title from the changed source.

## Project Conventions

- Keep the provider ID stable: `tv.plex.agents.custom.jb.miles.pgmam`
- Prefer `lxml` XPath for scraper ports unless there is a strong reason not to.
- Use `.env.example` for committed defaults; never commit local `.env`.
- Keep search ordering and source tiering aligned with the roadmap in `docs/ROADMAP.md`.
