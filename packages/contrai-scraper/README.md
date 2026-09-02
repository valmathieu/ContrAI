# contrai-scraper

Playwright spectator-mode scraper for online Contrée games — the data-collection member of the
[ContrAI](../../README.md) workspace.

It is a passive observer: it logs in with a dedicated account, opens a tournament table in
spectator mode and records what happens there. It does not play and it does not validate
moves — materializing observations into `contrai_core` types and checking them against the
rules is the engine's job.

## Site specifics stay out of the repository

The target URL, the account credentials and every selector the browser clicks are being moved
into a local configuration file that is never committed; the remaining literals in `config.py`
are the last ones and are replaced by that profile in the next scraper release. Code and docs
describe *what* each step does, not *where* it clicks. Do not add the site's name, its DOM ids
or screenshots of it to any tracked file.

## Layout

The importable package lives under `src/contrai_scraper/`:

| Module | Role |
| ------ | ---- |
| `config` | Target URL and scraping-account credentials. |
| `session` | Lobby navigation: `log_in` → `open_spectator_mode` → `find_tournament_table`. |
| `observer` | Watches a seated table: `get_players`, `get_current_round`, `observe_game`. |
| `cli` | `contrai-scrape` console script wiring the two phases together. |

## Usage

From the workspace root, after `uv sync` and `uv run playwright install chromium`:

```bash
uv run contrai-scrape
```

The browser runs headed with a small slow-motion delay so a run can be watched.

## Status

v1 reaches a seated tournament table and polls the round counter. Bidding and card-play
observation, persistence and multi-table orchestration are not wired yet — see the
[scraper docs](../../docs/scraper/index.md).
