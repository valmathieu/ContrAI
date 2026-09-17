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
the committed schema, placeholders only. No tracked file names the site at all any more — not
code, not a test, not an error message, which is why a failed step reports the profile *key* that
did not resolve rather than the selector behind it. Code and docs describe *what* each step does,
not *where* it clicks, and the test suite runs against an invented vocabulary. Do not add the
site's name, its DOM ids or screenshots of it to any tracked file.

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
| `browser` | `Spectator` — the only module that touches a page: login, the walk, the hop, the two panels. |
| `recorder` | `Recorder` — the table loop: seat, gate, watch, write, hop. Imports no Playwright. |
| `schedule` | `Schedule` — daily ranges in a named timezone; answers "is it open now" and "when does that change". |
| `egress` | `EgressGate` — exit address, country and route device, checked before the site is touched. |
| `shift` | `Shift` — the outer loop: schedule gate, egress gate, one browser session and raw log per window, failure budgets. |
| `health` | `HealthLog` and `Counters` — one JSON line per transition, on stderr. |
| `cli` | `contrai-scrape`: `run` (the default), `check-profile` and `parse`. |

## Usage

From the workspace root, after `uv sync` and `uv run playwright install chromium`:

```bash
uv run contrai-scrape run --profile profile.toml --headless   # watch tables
uv run contrai-scrape check-profile profile.toml              # validate before a shift
uv run contrai-scrape parse RAW... --profile profile.toml     # re-parse stored raw logs
```

`run` takes `--max-games N` and `--minutes N`, and `--headless` / `--headed` override
`[browser].headless` — headed with a slow-motion delay makes a run auditable, headless makes it
unattended. `check-profile` walks the site once and prints one line per check, exiting 1 on any
failure, so it can gate a shift before it starts. It checks the egress first and opens no browser
when it is refused. `parse` needs no browser at all: it replays a
raw log through the same pipeline a live session uses and writes a `contrai-data` record per
game, which `contrai verify` then checks.

`run` now runs shifts — no browser outside `[schedule]`, the egress checked before each session,
exit code 3 when a failure budget is spent; see the [scraper docs](../../docs/scraper/index.md).

## Status

Both halves are in place: the profile, the raw log, the wire parser, the profile-driven browser
walk, the table loop, the health log, and shifts with their schedule and egress gates. What is left
is multi-table orchestration and pseudonymisation — records currently carry raw ids, names and
account fields, which makes them personal data and local-only. See the
[scraper docs](../../docs/scraper/index.md).

Deployment: `deploy/` (Docker Compose, VPN sidecar), see `deploy/install.md`.
