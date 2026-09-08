# contrai-scraper

Playwright spectator-mode scraper for online Contrée games (auth required).

**Stack:** Playwright async, Python 3.14, uv. Output: `contrai-data` records — one append-only
JSONL file per game, the same format the engine writes, so a scraped game and a played one are
read by the same code.

Site specifics — the target URL, the scraping account, every selector the browser clicks, every
token the wire speaks — live in a local `profile.toml` that is never committed. Code and docs
describe *what* each step does, not *where* it clicks; do not add the site's name, its DOM ids
or screenshots of it to any tracked file. The test suite runs against an invented vocabulary for
the same reason.

## Layout

| Module | Role |
| ------ | ---- |
| `contrai_scraper.profile` | `profile.toml` → frozen, validated sections. The only place the site is named. |
| `contrai_scraper.exceptions` | `ScraperError` / `ProfileError` / `WireError` / `ParseError`. |
| `contrai_scraper.frames` | `RawFrame` and the two frame sources: a live Playwright page, or a stored raw log. |
| `contrai_scraper.rawlog` | The verbatim per-session log: `RawLogWriter`, `read_raw_log`, `raw_path`. |
| `contrai_scraper.wire` | Envelope, keepalive, de-duplication, composite key → `WireEvent`. |
| `contrai_scraper.lzstring` | The LZ-String base64 codec the deal payload arrives in. |
| `contrai_scraper.parse` | `translate`, `deal`, `snapshot`, `live`, `session` — wire events → a record. |
| `contrai_scraper.cli` | `contrai-scrape`: `run` (the v1 browser flow, still the default) and `parse`. |
| `contrai_scraper.config` / `.session` / `.observer` | The v1 browser flow, kept until the profile-driven replacement lands. |

```bash
uv run contrai-scrape                                          # watch a table (v1 flow)
uv run contrai-scrape parse RAW... --profile profile.toml      # re-parse stored logs
```

## Pipeline

Everything between a socket frame and a record, in one direction, each stage knowing only the
one below it.

```mermaid format="svg" source="flow_wire.mmd"
```

Three properties of the traffic shape the wire layer. It is **double-wrapped** — the frame is
JSON whose payload is itself a JSON *string*. It is **mirrored** — a second connection repeats
every event, so half of what arrives is a duplicate. And it is **interleaved with a keepalive
that is not JSON at all**, which a parser assuming otherwise trips over roughly once a second.

Three more shape the parser, and all three are cases where the wrong reading produces a record
that is well-formed and wrong:

- **The seat map is a mirror.** The table's rotation runs the opposite way round from
  `Position`'s order, so the two side seats map crosswise. The profile owns the map and
  `Translator` asserts the *rotation*, not the names — a literal one-to-one map passes every
  spot check and still turns the table backwards.
- **A double's payload names the player being doubled**, not the doubler. The doubler is the
  event key's actor; taking the payload's owner credits the double to the side it was aimed at,
  and the round still looks legal.
- **The join snapshot describes the last *completed* round.** The round in progress when the
  session joined is skipped, never reconstructed, so an observed game's hands are always
  `dealt_from_deck`.

The raw log is what makes all of this correctable. Frames are stored verbatim *before* anything
is interpreted, so a parser fix applies to games already watched — `contrai-scrape parse` is that
re-run, and it is the same code path a live session takes.

## Profile

One TOML document, git-ignored; `profile.example.toml` in the package is the committed schema,
placeholders only. It is read strictly: an unknown section, an unknown key, a wrong type or an
incomplete token map is a load error, so a site change surfaces as a refusal rather than as
silently wrong data.

| Section | What it holds |
| ------- | ------------- |
| `[site]` | Where the site lives, and which language it answers in. |
| `[account]` | The spectator account. Values may read `env:NAME` instead of holding the secret. |
| `[browser]` | Headless or headed, slow-motion, screenshot-on-error. |
| `[selectors]` | One entry per UI step; a list means "try these in order". |
| `[wire]` | How a frame is recognised, unwrapped and keyed. |
| `[wire.events]` | The three event names the parser reacts to. |
| `[wire.fields]` | Dotted paths, one per logical field the parser reads. The set of names is fixed. |
| `[wire.tokens]` | The site's vocabulary mapped onto core values — cards, seats, team labels, bid values. |
| `[rules]` | The core preset, plus the table options the browser half checks. |
| `[output]` | Where records and raw logs go; both roots resolve relative to the profile. |
| `[privacy]` | Inputs to the pseudonymisation step, which is not built yet. |

Two exceptions are worth knowing. `ProfileError` means the document is wrong — edit it.
`ParseError` means the wire said something the document does not describe — investigate the
site. They have different fixes, which is why they are different types.

## Current flow (v1)

login → online mode → spectator list → Contrée variant → tournament table → identify the four seats from their name badges → poll the round counter for new rounds.

```plantuml format="svg" source="seq_scraper.puml"
```

This is the DOM-polling flow the `run` subcommand still drives. It is replaced by the
profile-driven browser half in the next scraper step, which drives the pipeline above instead of
reading the page.

## Pending

- The browser half: profile-driven navigation, the tournament and options gates, the per-seat
  panel read.
- The recorder loop — a live session writing its raw log and its records as it watches.
- The health log: socket counts, de-duplication ratio, watchdog.
- Multi-table orchestration.
- Pseudonymisation: records currently carry raw ids, names and account fields — personal data,
  local only.
- Rate-limiting / ToS considerations.
