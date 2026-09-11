# contrai-scraper

Playwright spectator-mode scraper for online Contrée games (auth required).

**Stack:** Playwright async, Python 3.14, uv. Storage: SQLite (default, schema TBD).

Site specifics — the target URL, the scraping account, every selector the browser clicks — are
being moved out of the repository into a local configuration file that is never committed. Code
and docs describe *what* each step does, not *where* it clicks; do not add the site's name, its
DOM ids or screenshots of it to any tracked file.

## Layout

| Module | Role |
| ------ | ---- |
| `contrai_scraper.config` | Target URL and scraping-account credentials (the last literals; replaced by the local profile in the next scraper release). |
| `contrai_scraper.session` | Lobby navigation: `log_in` → `open_spectator_mode` → `find_tournament_table`. |
| `contrai_scraper.observer` | Watches a seated table: `get_players`, `get_current_round`, `observe_game`. |
| `contrai_scraper.cli` | `contrai-scrape` console script wiring the two phases together. |

```bash
uv run contrai-scrape
```

## Current flow (v1)

login → online mode → spectator list → Contrée variant → tournament table → identify the four seats from their name badges → poll the round counter for new rounds.

```plantuml format="svg" source="seq_scraper.puml"
```

`FUTURE LOGIC` placeholders (bidding observation, gameplay observation, SQLite persistence, DB-based de-duplication of already-scraped players) appear as dashed `<<future>>` arrows on the diagram and map to the comment block inside `observer.observe_game`.

## Pending

- Bidding observation
- Card-play observation
- Game persistence (schema design)
- Multi-table orchestration
- Rate-limiting / ToS considerations
