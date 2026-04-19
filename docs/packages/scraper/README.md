# contrai-scraper

Playwright spectator-mode scraper for online Coinche games.

**Target:** `https://app.belote-rebelote.fr/` (auth required).

**Stack:** Playwright async, Python 3.14, uv. Storage: SQLite (default, schema TBD).

## Current flow (v1)

login → Online → Spectator → Contree → Tournament → identify players via `#nord/#sud/#est/#ouest` → poll `#tour` for new rounds.

## Pending

- Bidding observation
- Card-play observation
- Game persistence (schema design)
- Multi-table orchestration
- Rate-limiting / ToS considerations

## Screenshots

Reference DOM captures of the target site in [`screenshots/`](screenshots/).
