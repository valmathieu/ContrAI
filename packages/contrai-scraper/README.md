# contrai-scraper

Playwright spectator-mode scraper for online Contrée games — the data-collection member of the
[ContrAI](../../README.md) workspace.

It is a passive observer: it logs in with a dedicated account, opens a tournament table in
spectator mode and records what happens there. It does not play and it does not validate
moves — checking an observed game against the rules is the engine's job, through
`contrai verify`.

## Site specifics stay out of the repository

The target URL, the account credentials, every selector the browser clicks and every token the
wire speaks live in a local `profile.toml` that is never committed; `profile.example.toml` is
the committed schema, placeholders only. The remaining literals in `config.py` are the last
ones, and the profile-driven browser half replaces them in the next scraper release. Code and
docs describe *what* each step does, not *where* it clicks — and the test suite runs against an
invented vocabulary, so no tracked file has to spell the real one. Do not add the site's name,
its DOM ids or screenshots of it to any tracked file.

## Layout

The importable package lives under `src/contrai_scraper/`:

| Module | Role |
| ------ | ---- |
| `profile` | `profile.toml` → frozen, validated sections. The only place the site is named. |
| `exceptions` | `ScraperError` / `ProfileError` / `WireError` / `ParseError`. |
| `frames` | `RawFrame` and the two frame sources: a live Playwright page, or a stored raw log. |
| `rawlog` | The verbatim per-session log: `RawLogWriter`, `read_raw_log`, `raw_path`. |
| `wire` | Envelope, keepalive, de-duplication, composite key → `WireEvent`. |
| `lzstring` | The LZ-String base64 codec the deal payload arrives in. |
| `parse/` | `translate`, `deal`, `snapshot`, `live`, `session` — wire events → a record. |
| `cli` | `contrai-scrape`: `run` (the v1 browser flow, still the default) and `parse`. |
| `config`, `session`, `observer` | The v1 browser flow, kept until the replacement lands. |

## Usage

From the workspace root, after `uv sync` and `uv run playwright install chromium`:

```bash
uv run contrai-scrape                                       # watch a table (the v1 flow)
uv run contrai-scrape parse RAW... --profile profile.toml   # re-parse stored raw logs
```

The browser runs headed with a small slow-motion delay so a run can be watched. `parse` needs no
browser at all: it replays a raw log through the same pipeline a live session uses and writes a
`contrai-data` record per game, which `contrai verify` then checks.

## Status

The wire half is done: profile, raw log, envelope, and the parser that turns a session into a
record. The browser half — profile-driven navigation, the recorder loop, the health log — is
next, and so is pseudonymisation; records currently carry raw ids, names and account fields,
which makes them personal data and local-only. See the
[scraper docs](../../docs/scraper/index.md).
