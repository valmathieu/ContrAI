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
| `contrai_scraper.accounts` | `accounts.toml` → the labelled spectator accounts a fleet logs in with, one per worker. |
| `contrai_scraper.exceptions` | `ScraperError` / `ProfileError` / `WireError` / `ParseError`. |
| `contrai_scraper.frames` | `RawFrame` and the two frame sources: a live Playwright page, or a stored raw log. |
| `contrai_scraper.rawlog` | The verbatim per-session log: `RawLogWriter`, `read_raw_log`, `raw_path`. |
| `contrai_scraper.wire` | Envelope, keepalive, de-duplication, composite key → `WireEvent`. |
| `contrai_scraper.lobby` | `LobbyWatcher` — the lobby's socket events → a `LobbyRoster` the moment a tournament game starts. |
| `contrai_scraper.lzstring` | The LZ-String base64 codec the deal payload arrives in. |
| `contrai_scraper.parse` | `translate`, `deal`, `snapshot`, `live`, `session` — wire events → a record. |
| `contrai_scraper.browser` | `Spectator` — the only module that touches a page. Login, the walk, the hop, the two panels. `open_browser` / `open_session` — one Chromium, one isolated context per session. |
| `contrai_scraper.recorder` | `Recorder` — the table loop: seat, gate, watch, write, hop. Imports no Playwright. |
| `contrai_scraper.registry` | `TableRegistry` — a fleet's claims: a chase by roster, a table by id, and the census of tables seen. |
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
- **The pre-game draw is not a round.** Before the first deal each player draws a card to seat the
  table, and each draw arrives keyed at round 0 with a real card and its place in the deck — cards
  that belong to no hand. It never wears a deal's key, so it cannot pass for one, but a stage taking
  "every keyed event of the table" would fold four stray cards into the record. `collect_rounds`
  leaves round 0 out by name, and the draw's verb (`[wire].draw_verb`) wherever it is addressed; the
  draw itself goes unremarked, and anything *else* the rule sweeps up is a parse note. It matters
  only for a game watched from its first card, which `run` never sees and a fleet always does. Do
  not add a "require a bid before accepting a deal" guard instead: the first bid has been seen
  1.8 s and 24.6 s after the deal.

One thing the wire leaves out entirely is a **forced pass**. The table skips a seat whose only
legal bid is a pass — the doubler's partner, the partner of a Slam bidder, everyone after a
redouble — spending its sequence number and transmitting nothing, so an auction read literally
ends short and projects as unfinished. `restore_forced_passes` puts each one back where core's
own legality says it falls and checks it against the gap it left in the numbering; a gap on a
seat that had a choice is a lost bid, and that round is skipped with a note instead of repaired.

The raw log is what makes all of this correctable. Frames are stored verbatim *before* anything
is interpreted, so a parser fix applies to games already watched — `contrai-scrape parse` is that
re-run, and it is the same code path a live session takes.

A panel read is filed in that same timeline, and stamped with `FrameSource.elapsed` — the clock
frames themselves are stamped on, so the two are comparable. It matters because a DOM reading is
taken *away* from the socket: the gap between a panel's stamp and the frames on either side of it
is how far behind the page the reader was when it looked, which is the one thing an unstamped line
could never say. A replay reports the stamp of the last frame it handed out, so a re-parse files
its readings on the instants the live session saw rather than on the speed of the machine
re-reading it.

It is the same path only once the log is cut the way a live session cuts it. A session hops, so its
log holds every table it looked at — two to twelve of them in the logs measured on 2026-09-16 —
while the parser assembles exactly one game out of whatever it is handed. `split_visits` makes that
cut first: a join snapshot naming a new table opens a visit, and an in-game event is filed by the
game its own key names rather than by when it arrived, because a table's last frames can still be
in flight when the hop lands. `parse` then writes one record per visit that held a game, and says
how many visits a log carried. Without the cut a re-parse merged tables — rounds whose plays
belonged elsewhere were dropped as undealable, records took whichever game id came first, and
records appeared for games no recorder ever accepted.

The live path makes the same cut, as a check rather than a split. A session buffers one table
because it seats one table, but that is true only by construction: a seat taken one table behind
the page once filled a buffer with 108 events of another table against 59 of its own, and the
parser merged both into a single record. So `Recorder._write` runs `split_visits` over the buffer
and refuses the record, logged as `record_refused`, when it holds more than one game key
(`several_games`) or another table's snapshot (`several_tables`). A snapshot alone is enough: it
would lend the record its seats and its score rows. It refuses rather than splits because a second
game that arrived without a snapshot of its own leaves nothing to say which half was the table
seated, and the raw log still holds every frame for `parse` to cut apart. The pre-game draw is keyed
to the game it opens, so a table caught from its first card is still one game.

One limit on "the same path" is worth knowing before a log is used as evidence. The log is
de-duplicated by frame identity and never reset, so the mirrored connection's copy of a frame is
not in the file — a replay is faithful for the parser, which drops those copies anyway, but it
cannot reproduce a fault whose trigger *is* a mirrored copy.

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
| `[selectors]` | One entry per UI step; a list means "try these in order". `seat_element` is a template filled with a seat token; `scoreboard_cell` is looked up inside each scoreboard row; `options_id_element` and `options_state_element` inside each option row. `rail_show` is optional: it names the control that brings a table's collapsible panel rails back, filtered on visibility so it is clicked only while they are away, and it is clicked again before every attempt at a panel control. The seven lobby keys (`mode_new_games`, `lobby_*`) are optional as a group — all or none — since only a fleet goes to the lobby. |
| `[wire]` | How a frame is recognised, unwrapped and keyed. `draw_verb`, optional, names the pre-game draw's verb. |
| `[wire.events]` | The three event names the parser reacts to, plus the lobby's own, `lobby_table` — optional, and only together with the lobby's paths below. |
| `[wire.fields]` | Dotted paths, one per logical field the parser reads. The set of names is fixed, bar the lobby's four (`lobby_hash`, `lobby_seats`, `lobby_seat_account`, `lobby_full`), which are all or none. |
| `[wire.tokens]` | The site's vocabulary mapped onto core values — cards, seats, team labels, bid values. |
| `[rules]` | The core preset, plus the table options the browser half checks. |
| `[recorder]` | The loop's own thresholds — hop, watchdog, heartbeat, seat timeout. Policy, not site vocabulary. |
| `[schedule]` | When the scraper may watch: a timezone, daily ranges that may cross midnight, and how long a closing range lets the game in hand run on. |
| `[egress]` | The gate before any site traffic: the home address (through the environment), the expected country, an echo service, and the tunnel device the route must use. |
| `[output]` | Where records and raw logs go; both roots resolve relative to the profile, and both name the same directory. |
| `[privacy]` | Inputs to the pseudonymisation step, which is not built yet. |

A fleet logs in with several accounts, and they do not multiply the profile. They live in a second
git-ignored document, `accounts.toml` beside `profile.toml` (`accounts.example.toml` is its
committed schema): one table per account, named by its **label**, holding the same `email` and
`verification_code` as `[account]` and read as strictly, `env:NAME` included. The label is what a
worker's health lines carry instead of the address, so it must be a plain token such as `bot01`,
and a label that is not one is refused by its position rather than repeated. Two labels on one
address are refused too: two sessions on one account would sign each other out. The profile's own
`[account]` stays, and `run` still reads it.

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

A session *is* a browser context — its own cookies, so its own login, and its own sockets, so its
own frames — opened by `open_session` on a browser `open_browser` launched. The browser is the
expensive half and the context the cheap one, so several sessions can share one Chromium and
rebuilding one of them costs the others nothing; `open_spectator`, the single-session path `run`
takes, is simply one of each. The order inside a session is load-bearing: the script that keeps the
page's sockets is added to the context before its page exists, and the frame source listens before
anything navigates, or the join snapshot describing the first table is gone.

The walk follows the site's timing, not only its markup. It lets the landing page settle before
probing for the first-visit tutorial, opens the address form through its own entry, and, because
the first-use pledge can be drawn a moment after it was looked for, answers it and retries once
when the spectator menu refuses a click.

**A selector that matches translated text is a latent fault, and it fires the first time the site
is reached from somewhere else.** The site chooses the interface language for the visitor, so the
same login page that renders in French to one exit renders in English to another — measured on
2026-09-18, when a deployment behind a Swiss exit could not find a field the laptop had been
typing into for days, because the profile matched that field by its French placeholder alone. The
DOM had it all along, under a different word. Prefer a stable attribute, an id, or the site's own
i18n key, all of which survive the change; where a text match really is the only handle, give it a
locale-independent alternative, because `[selectors]` takes a **list** and tries each in turn. The
entry beside it was already written that way and walked straight through the same page.

Panel controls get more than that, because they sit on rails the table slides away on its own.
Playwright already retries a click for its whole timeout, so a control covered for a moment by one
of the table's transient dialogs needs no help. What it cannot survive is the two together: the
click waits on the covered control, the rails leave under it mid-wait, and the button it is still
waiting for is now off-screen with no way to ask for it back. So a panel control is offered
`PANEL_ATTEMPTS` shorter attempts instead of one long one, and `rail_show` is clicked again at the
top of each — the reveal was never wrong, it was simply asked once, in front of a wait it could
not reach into. Measured on 2026-09-16: the options button covered by a modal host while the rails
were in, dead after 10 s; the same click landed 0.9 s after the rails were revealed a second time.
The table hop is one of these controls. It reads like a menu step and took the single-shot path
until 9B's 24-hour run ended two sessions on it: a recorder that hops promptly finds the rails
still out, and one that watches a table to its end does not.

When a browser step does fail, `[browser].screenshot_on_error` saves a PNG and the page's DOM
beside that session's raw log. An error names the profile key it was on and nothing else, which
says *which* selector stopped matching but never *why* — and the why is routinely something no
selector can express: a dialog over the control, a rail that slid away, a font that did not load.

`check-profile` keeps the same evidence, under `<raw_root>/raw/check-profile-<id>.png` and `.html`,
and the failed line says where. It matters more there than in a run: `check-profile` is what is
reached for when a deployment will not start, often on a host administered through a console, where
the browser is gone by the time the line is read and the walk cannot be repeated by hand. The line
itself is never replaced by the diagnosis — an unwritable raw root or a page that has already gone
leaves the failure reported exactly as it was.

A hop moves the page long before the reader hears about it, and a gate judging the wrong snapshot
reads one table's options and scoreboard against another's. Measured across 2026-09-16 and
2026-09-17: over twenty table joins the new table described itself between 0.05 s and 0.23 s after
the page joined its room, while a gate and its hop together took 1.8 s to 7.2 s — two DOM panel
reads against a quarter-second wire. So the snapshot waiting in the queue when a gate ends is
routinely the one for the table just left: ten of thirteen gates across those two sessions were
judging a table the page had already been moved off, and every one of those snapshots had arrived
*before* the hop that preceded its gate. The site was never the one moving the spectator — the
reader was behind its own clicks.

The lag starts at a game boundary. The snapshot answering the closing request is read by the
drain, which never offers it to a gate, so the mirrored copy still queued on the other connection
is a first sighting at the next seat: the table whose game has just been written is judged again,
and from there each gate hands the offset to the next. Both sessions show it — the first gate
after each `game_recorded` judged the table just recorded.

So the table just left is remembered by name, and a snapshot naming it is refused and logged as
`stale_snapshot` instead of judged. Frame identity cannot do that job: at a boundary the stale
snapshot is a copy of one already consumed, and at every later gate it is a table's own first and
only snapshot. The rule is "not the one we just left", never "none seen before" — the pool is
small enough that a table comes round again within a few hops — and a table genuinely re-offered
twice running is left to `snapshot_timeout_s`, which ends as a `seat_timeout` and one more hop.
None of the twenty joins measured re-offered a table immediately.

Draining the queue is the other half of it, and not a substitute: anything the page has **already**
queued is taken first, so a page several tables ahead is not followed one gate at a time, and the
newest readable table that is not the one just left is the one gated. It never waits, so a reader
that is keeping up pays nothing; what arrived after that newest snapshot is handed to the buffer
rather than dropped, because it is the beginning of the table about to be judged.

The states, in order: reset the buffer and the stream, wait for a join snapshot, refuse a table
whose snapshot this profile cannot read, refuse one another worker of a fleet already holds
(`claimed_by_other`, below), refuse one that is not a tournament or is already
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

In a fleet, several recorders share one `TableRegistry`, because none of them chooses its table and
two are routinely seated at the same one. That is worse than waste: both would write
`games/obs-<game_id>.jsonl`, and a record is opened for appending, so two streams would interleave
into one file that is well formed and wrong. So the registry's refusal, `claimed_by_other` with the
holder's label, is the first gate — the only one that prevents corruption rather than waste — and
every other gate still runs after it. A recorder holds the table it watches, refreshing the claim on
every frame, gives it up when it next seats or stops for any reason, and adds every table it judges
to the fleet's census. Two keys do two jobs and are kept apart: the lobby names who is about to play
but not where, so a chase is claimed by its **roster**; a seated table carries its own id, so
exclusion is claimed by **table id**. Every worker runs in one event loop and no claim awaits
anything between its check and its set, so a claim is atomic without a lock. Claims also expire
after `claim_ttl_s` unrefreshed — not the mechanism, only the backstop for a worker that wedged
holding one — and a late release never frees a claim someone else has since taken.

`HealthLog` writes one JSON object per line to stderr — a transition per line plus a counter
heartbeat — so a shift is `journalctl`-readable without a parser being written for it.

Several workers write one stream, so each gets a log of its own from `HealthLog.worker(label)`:
every line it writes carries `worker` right after the event name, and its counters and its
heartbeat interval are its own. A worker that has stopped seating tables then shows as one worker's
flat counters rather than as a dip in a total. The fleet's own beat is a separate event,
`fleet_heartbeat`, carrying the workers' counters added up and how many there are, so a reader
summing `heartbeat` lines per worker never counts one twice. The label is an opaque name such as
`bot01`, never the account's address. A log with no label — `run`'s — writes exactly what it
always did.

## The lobby

`run` sits wherever the server puts it, which is always a game already under way. The lobby is
where a game can be seen *before* it starts: a screen listing table slots, one of them the
tournament's, which fills with four players, starts, and recycles for the next four. It says when a
game starts and who is in it, never where to find it — its rows carry a hash that is a
configuration's fingerprint rather than a table's id, and a table cannot be joined by it — so a
spectator still reaches the game through the observe branch and a scan.

`Spectator` walks it with the same care as the rest of the flow. `enter_lobby` takes the action
beside the observe one and the variant inside that list's own picker, meeting the first-use pledge
the way `enter_variant` does. `read_tournament_hash` reads the tournament row's hash off the page
once: the lobby's socket says everything about a row except which one is the tournament's.
`enter_table_from_lobby` backs out to the observe action and takes it; `return_to_lobby` walks from
wherever the page stands back to the list.

**There is no single back control.** The page stacks its screens — the mode menu, the online menu,
the variant picker, the list — and each carries its own back control, one icon on the menus and
another on the list. Every screen keeps its controls in the DOM with a real box, so Playwright's
`:visible`, which asks about the element, matches the controls of the three screens behind as
readily as the one in front; a hard-coded control cost the chase probe three live runs, each dying
on a control that was right for a screen other than the one shown. So `back_once` reads every
candidate in `lobby_back` together with the computed style of the screen it sits on (`lobby_layer`),
keeps the ones a click could land on, and clicks the best-ranked of those. `_back_until` asks the
same question of a menu action before each step, rather than trying a short click that a control on
a hidden screen would hold for its whole timeout.

**The overlay click is the one click that changes the site.** The lobby's first-visit overlay goes
away at any click, but without it a click at the centre of the screen lands on a table slot and
sits the account down. `dismiss_overlay` reads what is under the centre first and clicks only when
that is not the list.

**The roster comes off the socket, not the page.** The tournament row shows four players for about
a second and a half, so reading the page every three seconds caught 6 of the 11 games that started
in a measured half hour; the socket carried all 11. Every row is driven by a `lobby_table` event,
and `LobbyWatcher` reads the tournament row's (by the hash the page gave) under three rules, each of
which is a record about the wrong players when broken:

- **An event's seats are the whole seat map, never a change to it.** Nothing in the stream vacates
  a seat, so adding events up leaves a ghost wherever a player changed chairs — that reading agreed
  with the page 4.7% of the time and reported 104 complete rosters where the page showed 6, while
  looking as though it worked.
- **The roster is the one the row held when its game started.** Seats change constantly before a
  start (74 changes in half an hour, swaps included) and the row recycles right after. The row
  raises a flag of its own (`lobby_full`) in the event that follows the fourth seat by milliseconds;
  that event, with its four accounts, is the roster, announced once. Waiting for the row to empty
  instead would wait for the next player to sit down — 35 s after the start in the first case
  measured. Re-read on the stored logs, the watcher announces the clean run's 11 starts, every
  roster the page-polling caught among them, and in each of the four chases exactly the roster that
  was then found at its table.
- **Accounts are matched as the site spells them**: six digits, zero-padded, kept as strings. A seat
  whose account is not one does not count. `LobbyRoster.digest` names a roster in log lines without
  naming its players.

**A chase is a recorder with a target.** Handed a `ChaseTarget` — the roster, a budget of distinct
tables and a deadline — `Recorder` holds every table it lands on against the roster before reading
anything off the page: a scan is mostly refusals, and the page costs seconds a wire comparison does
not. The match is all four accounts or nothing (`roster_mismatch`, with how many matched): across
every roster and every wrong table in the corpus the best a wrong table scored was two of four, so
"most of them" would have found a wrong table forty times. A match is announced as `chase_matched`,
with how many distinct tables it took and how long since the roster was read, and then passes every
other gate — a chase is not a way round the tournament, options or orientation checks. The recorder
stops after that one game (`chase_ended`) and never asks for another table: the next one is the
lobby's to announce. The scan is budgeted on *distinct* tables judged and on a deadline, never on
hops, because the server's walk re-offers tables it has already given and is not a cycle — a hop
count shrinks silently as repeats eat it. Running out, or finding the table and seeing a gate refuse
it, is `chase_gave_up` with its reason (`distinct_budget`, `deadline`, `target_refused`): an outcome,
not an error. Log lines name a roster by its digest, never by its players.

Two consequences for the registry. The `claimed_by_other` check still comes first, but the claim
itself is taken only when a table is *accepted*, after its other gates: a scan passing through a
table never holds it, so it can never keep out the worker that came to find it, and a table accepted
elsewhere while the page was being read is still refused at the end. And a chase is fast but not
instant: in one probe run the match came 26.2 s after the roster, after the first deal had gone out,
and a record joined then starts at round 2. Nothing at the gate can tell, so `game_recorded` carries
`first_round`, which is what says how often a chase arrives in time.

`return_to_lobby` is the one route nothing has measured: the probes only ever went from the lobby
to a table. It is written to fail fast — at most `LOBBY_BACK_STEPS` steps back, then a
`BrowserError` naming the key it was waiting for — so that the caller can rebuild the session, the
one recovery measured to work. `check-profile` walks the lobby in a session of its own when the
profile describes one: in, the tournament row, the first lobby event, and back out to a table. The
lobby speaks only when a seat changes — its first event after arriving came 27 s and 82 s later in
the two runs timed — so a wait that hears nothing passes and says it proved nothing; an event that
arrives and cannot be read is what fails, naming the path that read nothing.

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

Several workers behind one tunnel share one gate, `SharedEgressGate`, because "before every hop"
multiplies: ten workers scanning would send the echo service about fifty requests in twenty seconds
from one exit address, which is how a free-tier service starts answering `429` — read by the gate
as a refusal. Asks that overlap a probe in flight wait for it and take its answer, good or bad, and
a passing reading answers later asks for `max_age_s`. A refusal is never reused: it answers only the
asks that were waiting on it, so the next caller probes again and fail-closed is unchanged. The
window is short next to `stale_after_s`, so a tunnel that died before a table went quiet has no
passing reading left to vouch for it when the watchdog asks.

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
