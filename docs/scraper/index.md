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
| `contrai_scraper.browser` | `Spectator` — the only module that touches a page. Login, the walk, the hop, the two panels. |
| `contrai_scraper.recorder` | `Recorder` — the table loop: seat, gate, watch, write, hop. Imports no Playwright. |
| `contrai_scraper.schedule` | `Schedule` — daily ranges in a named timezone; answers "is it open now" and "when does that change". |
| `contrai_scraper.egress` | `EgressGate` — exit address, country and route device, checked before the site is touched. |
| `contrai_scraper.shift` | `Shift` — the outer loop: schedule gate, egress gate, one browser session and raw log per window, failure budgets. |
| `contrai_scraper.health` | `HealthLog` and `Counters` — one JSON line per transition, on stderr. |
| `contrai_scraper.cli` | `contrai-scrape`: `run` (the default), `check-profile` and `parse`. |

```bash
uv run contrai-scrape run --profile profile.toml --headless    # watch tables
uv run contrai-scrape check-profile profile.toml               # validate before a shift
uv run contrai-scrape parse RAW... --profile profile.toml      # re-parse stored logs
```

`run` takes `--max-games N` and `--minutes N`, and `--headless` / `--headed` override
`[browser].headless`. A bare `contrai-scrape` is still `contrai-scrape run`, but `run` needs a
profile, so it now fails with usage rather than launching anything.

## Pipeline

Everything between a socket frame and a record, in one direction, each stage knowing only the
one below it.

```mermaid format="svg" source="flow_wire.mmd"
```

Three properties of the traffic shape the wire layer. It is **double-wrapped** — the frame is
JSON whose payload is itself a JSON *string*. It is **mirrored** — a second connection repeats
every event, so half of what arrives is a duplicate. And it is **interleaved with a keepalive
that is not JSON at all**, which a parser assuming otherwise trips over roughly once a second.

Four more shape the parser, and all four are cases where the wrong reading produces a record
that is well-formed and wrong:

- **The seat map is checked by rotation, not by name.** The observed tables turn clockwise,
  which is what the `tournament` preset says, so each on-screen seat maps to the compass seat
  it shows. The profile owns the map and `Translator` asserts that the site's rotation walks
  the seats in the preset's direction — a map with its side seats crossed passes every spot
  check and still turns the table backwards.
- **A double's payload names the player being doubled**, not the doubler. The doubler is the
  event key's actor; taking the payload's owner credits the double to the side it was aimed at,
  and the round still looks legal.
- **The join snapshot describes the last *completed* round.** The round in progress when the
  session joined is skipped, never reconstructed, so an observed game's hands are always
  `dealt_from_deck`.
- **A sweep is a fact about the tricks, not about the bid.** A declaring side that takes all eight
  tricks off a numeric, un-doubled contract has made an *unannounced* slam, and the record says so
  — the round's plays are replayed through core's own trick-winner rule to decide it. Reading the
  contract alone would write `none`, which the round's own plays contradict and which `contrai
  verify` calls `suspect`.

One thing the wire leaves out entirely is a **forced pass**. The table skips a seat whose only
legal bid is a pass — the doubler's partner, the partner of a Slam bidder, everyone after a
redouble — spending its sequence number and transmitting nothing, so an auction read literally
ends short and projects as unfinished. `restore_forced_passes` puts each one back where core's
own legality says it falls and checks it against the gap it left in the numbering; a gap on a
seat that had a choice is a lost bid, and that round is skipped with a note instead of repaired.

The raw log is what makes all of this correctable. Frames are stored verbatim *before* anything
is interpreted, so a parser fix applies to games already watched — `contrai-scrape parse` is that
re-run, and it is the same code path a live session takes.

It is the same path only once the log is cut the way a live session cuts it. A session hops, so its
log holds every table it looked at — two to twelve of them in the logs measured on 2026-09-16 —
while the parser assembles exactly one game out of whatever it is handed. `split_visits` makes that
cut first: a join snapshot naming a new table opens a visit, and an in-game event is filed by the
game its own key names rather than by when it arrived, because a table's last frames can still be
in flight when the hop lands. `parse` then writes one record per visit that held a game, and says
how many visits a log carried. Without the cut a re-parse merged tables — rounds whose plays
belonged elsewhere were dropped as undealable, records took whichever game id came first, and
records appeared for games no recorder ever accepted.

The join snapshot's running totals are read by the profile's team letters alone: the block holding
them may carry other things beside them, such as the per-round rows. A mid-game join whose totals
cannot be placed on a side is refused as a `ParseError`, because the record has to say what the
score was when watching began. A score row reads as made when the side it names as the winner is
the declaring side; the row's status token is only consulted when a row does not name both.

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
| `[selectors]` | One entry per UI step; a list means "try these in order". `seat_element` is a template filled with a seat token; `scoreboard_cell` is looked up inside each scoreboard row; `options_id_element` and `options_state_element` inside each option row. `rail_show` is optional: it names the control that brings a table's collapsible panel rails back, filtered on visibility so it is clicked only while they are away, and it is clicked again before every attempt at a panel control. |
| `[wire]` | How a frame is recognised, unwrapped and keyed. |
| `[wire.events]` | The three event names the parser reacts to. |
| `[wire.fields]` | Dotted paths, one per logical field the parser reads. The set of names is fixed. |
| `[wire.tokens]` | The site's vocabulary mapped onto core values — cards, seats, team labels, bid values. |
| `[rules]` | The core preset, plus the table options the browser half checks. |
| `[recorder]` | The loop's own thresholds — hop, watchdog, heartbeat, seat timeout. Policy, not site vocabulary. |
| `[schedule]` | When the scraper may watch: a timezone, daily ranges that may cross midnight, and how long a closing range lets the game in hand run on. |
| `[egress]` | The gate before any site traffic: the home address (through the environment), the expected country, an echo service, and the tunnel device the route must use. |
| `[output]` | Where records and raw logs go; both roots resolve relative to the profile, and both name the same directory. |
| `[privacy]` | Inputs to the pseudonymisation step, which is not built yet. |

Two exceptions are worth knowing. `ProfileError` means the document is wrong — edit it.
`ParseError` means the wire said something the document does not describe — investigate the
site. They have different fixes, which is why they are different types.

## The table loop

login → online mode → the variant → wherever the server seats us → gate the table → watch one
game → write the record → ask for another table.

```plantuml format="svg" source="seq_scraper.puml"
```

Three measured facts shape that loop, and each of them removes something an obvious design would
have had:

- **There is no table list.** The server decides where a spectator sits, so nothing browses;
  "another table" is a request, not a choice. The walk ends at the variant.
- **The exit control is unrecoverable.** It leaves *spectating* rather than the table, and the
  documented route back in is what breaks afterwards. So the only hop is the table control, and
  `Spectator` has no leave operation at all: a session that cannot reseat rebuilds its browser
  context.
- **The boundary score read is a wire read.** The client can ask for table state without leaving,
  and the answer arrives on the socket as a fresh join snapshot — 0.21 s against 2.58 s for the
  rendered panel, with no replay cost. The panel is the fallback, and it is evidence for the raw
  log rather than a score the parser can use.

The walk follows the site's timing, not only its markup. It lets the landing page settle before
probing for the first-visit tutorial, opens the address form through its own entry, and, because
the first-use pledge can be drawn a moment after it was looked for, answers it and retries once
when the spectator menu refuses a click.

Panel controls get more than that, because they sit on rails the table slides away on its own.
Playwright already retries a click for its whole timeout, so a control covered for a moment by one
of the table's transient dialogs needs no help. What it cannot survive is the two together: the
click waits on the covered control, the rails leave under it mid-wait, and the button it is still
waiting for is now off-screen with no way to ask for it back. So a panel control is offered
`PANEL_ATTEMPTS` shorter attempts instead of one long one, and `rail_show` is clicked again at the
top of each — the reveal was never wrong, it was simply asked once, in front of a wait it could
not reach into. Measured on 2026-09-16: the options button covered by a modal host while the rails
were in, dead after 10 s; the same click landed 0.9 s after the rails were revealed a second time.

When a browser step does fail, `[browser].screenshot_on_error` saves a PNG and the page's DOM
beside that session's raw log. An error names the profile key it was on and nothing else, which
says *which* selector stopped matching but never *why* — and the why is routinely something no
selector can express: a dialog over the control, a rail that slid away, a font that did not load.

The states, in order: reset the buffer and the stream, wait for a join snapshot, refuse a table
whose snapshot this profile cannot read, refuse one that is not a tournament or is already
`hop_after_rows` rounds old, refuse one whose options
disagree with `[rules.options]`, check that the panel's *us* is the south seat's side, then watch.
A deal opening a new round triggers the boundary read; the table's own game-over flag closes the
record; an observer-left flag alone does not, because it says the spectator stopped watching and
not that the game finished. A table that plays nothing for `stale_after_s` is written as
`abandoned`, and an interrupted process writes what it saw as `interrupted` — neither is visible
to the wire, which is why `parse_session` takes an `end_reason` the caller can state.

The orientation check compares the newest round *both* readings describe, not the last row of
each. The panel is opened a moment after the join snapshot arrives, so the table can score a round
in between; holding one reading's last row against the other's then compares two different rounds
and refuses a table whose sides line up. The health line carries the round index and both row
counts, so a mismatch says whether the numbers even describe the same round.

A snapshot that will not parse is a rejection and not a failure. The site runs variants the
`tournament` ruleset has no vocabulary for — all trump is the one that was met — and the options
gate that refuses such a table runs *after* the snapshot is read, so a table we never wanted would
otherwise end the session and spend a slot of the shift's failure budget. It is logged as
`table_rejected` with the token the reader objected to, and it counts in `tables_rejected`, so a
profile that has genuinely drifted shows itself rather than hiding as a hop.

A shift can hand the recorder a seat deadline — after it no new table is taken, while the game in
hand runs on — and an egress gate, checked before every hop and when a table goes quiet: behind a
refused egress the game is written `interrupted`, not `abandoned`.

The buffer is what keeps one parser from becoming two. A seated table's events are collected
and handed to the same batch parser `contrai-scrape parse` uses, so "a round is complete at
twenty-eight plays" and "skip the round in progress at seating" exist once. It is reset at every
seat: table discovery joins each candidate and emits one snapshot per visit, and keeping the first
would seat four players from a table we left.

`HealthLog` writes one JSON object per line to stderr — a transition per line plus a counter
heartbeat — so a shift is `journalctl`-readable without a parser being written for it.

## Shifts

`contrai-scrape run` is a shift: a process meant to stay up for weeks, watching only inside the
profile's `[schedule]` and only through the tunnel `[egress]` describes. `Shift` is the loop that
reconciles the two, one browser session at a time.

```mermaid format="svg" source="state_scraper_shift.mmd"
```

- **No browser outside the window.** A closed schedule holds nothing open — a closed browser costs
  nothing and cannot drift. The process logs `schedule_idle` once, naming the next opening, and
  asks again every `idle_poll_minutes`.
- **The egress is checked before every session, cheapest-to-leak first.** The echo service is
  asked for the exit address before the site's name is even resolved, so a leaking setup is refused
  before it has looked the site up; then the country; then, where `tunnel_interface` is set, the
  device the site's route leaves through. A refusal (`egress_blocked`) sends nothing to the site,
  and the home address is compared, never logged. The recorder asks again before every hop and
  when a table goes quiet.
- **Each session keeps its own raw log**, and logs past `[output].raw_retention_days` are pruned
  before a session opens, so a process running for weeks never holds one file past its retention.
  Records are never pruned.
- **A closing window ends seating, not the game in hand.** No table is taken after the close; with
  `finish_current_game` the game already being watched runs on for at most `max_overrun_minutes`,
  and one still running then is written `observer_left` — we stopped watching it, it did not end.
- **Budgets hand the process back.** Six refused egress checks in a row, or three failed sessions
  in a row (a browser error, or frames that simply stopped), end the process with exit code 3, so
  its supervisor starts a fresh one. Exit code 130 means it was interrupted — Ctrl+C, or the
  SIGTERM a service or container stop sends — and the game in hand was written `interrupted`.

## Deployment

The confinement is structural. On the box the scraper runs in a container with no network of its
own: Docker Compose puts it in the network namespace of a gluetun VPN container, whose firewall lets
traffic out only through the WireGuard tunnel. When the tunnel is down there is no route at all —
the scraper fails closed rather than falling back to the home connection — and nothing else on the
shared host is rerouted.

The egress gate is what makes a failure of that confinement visible rather than merely unlikely. It
runs before every session, before every hop and when a table goes quiet, and a refusal sends nothing
to the site; but it is visibility, not the guarantee.

The image, the Compose file, the environment templates and the procedures that prove the
confinement — the exit address, the refusal of the home address, a tunnel outage under a packet
capture — live in `deploy/install.md`.

```mermaid format="svg" source="deploy_scraper.mmd"
```

## Pending

- Multi-table orchestration: several browser contexts, a shared registry of tables already
  watched.
- Pseudonymisation: records currently carry raw ids, names and account fields — personal data,
  local only.
- Rate-limiting / ToS considerations.
