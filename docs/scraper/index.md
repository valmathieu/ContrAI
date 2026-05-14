# contrai-scraper

Playwright spectator-mode scraper for online Coinche games.

**Target:** `https://app.belote-rebelote.fr/` (auth required).

**Stack:** Playwright async, Python 3.14, uv. Storage: SQLite (default, schema TBD).

## Current flow (v1)

login → Online → Spectator → Contree → Tournament → identify players via `#nord/#sud/#est/#ouest` → poll `#tour` for new rounds.

```plantuml format="svg" source="seq_scraper.puml"
```

`FUTURE LOGIC` placeholders (bidding observation, gameplay observation, SQLite persistence, DB-based de-duplication of already-scraped players) appear as dashed `<<future>>` arrows on the diagram and map to the comment block at `main.py:105-108`.

## Pending

- Bidding observation
- Card-play observation
- Game persistence (schema design)
- Multi-table orchestration
- Rate-limiting / ToS considerations

## Screenshots

Reference DOM captures of the target site live under `screenshots/`:

- ![Lobby (final view)](screenshots/lobby_final.png)
- ![Target table (spectator)](screenshots/success_target_table.png)
