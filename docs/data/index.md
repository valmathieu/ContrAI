# contrai-data

The ContrAI **game-record format** — one append-only JSONL file per game of contrée, shared by
everything that produces or consumes one.

## Module map

Source lives at `packages/contrai-data/src/contrai_data/`:

| Module          | Contents                                                                                  |
| --------------- | ----------------------------------------------------------------------------------------- |
| `exceptions.py` | `RecordError` (base), `RecordFormatError`, `UnsupportedFormatError`, `VerdictFormatError`, and `CatalogError` — all of them both a `ContraiError` and a `ValueError` |
| `events.py`     | One frozen dataclass per event (`Header`, `GameStarted`, `RoundDealt`, `BidMade`, `CardPlayed`, `BeloteHeld`, `RoundScored`, `GameEnded`), the five value objects (`Seat`, `ObservedFrom`, `Ruleset`, `SideMark`, `ContractTerms`), and the eight closed vocabularies |
| `tokens.py`     | Domain value ⇄ ASCII token, both ways and strictly — seats, sides, cards, contract suits and values, whole bids, whole rulesets, and the UTC timestamp check |
| `codec.py`      | `encode` / `decode` — one event ⇄ one JSON line — plus `FORMAT` and the major-version gate |
| `store.py`      | Records on disk: `RecordWriter`, `read_events` / `ReadResult`, `records_root` / `games_dir` / `game_path`, `new_game_id` |
| `projection.py` | `GameRecord` / `RoundRecord`, and the `project` / `load_game` fold that re-derives the contract, the tricks and their winners |
| `catalog.py`    | `build_catalog` / `CatalogSummary` / `SkippedFile` — the SQLite index over a records root — and `player_games` / `PlayerReport` / `PlayerGame` to read one player back |
| `verdict.py`    | What `contrai verify` concluded: `Verdict` (`verified` / `partial` / `suspect`), the five `MismatchKind` classes, `Mismatch` / `RoundVerdict` / `GameVerdict`, `verdicts_dir` / `verdict_path` / `write_verdict`, and the strict `read_verdict` |

Everything above is re-exported from `contrai_data/__init__.py` and is part of the public API.

## Class structure

```plantuml format="svg" source="class_data.puml"
```

The diagram encodes the layering. Read it left to right and the arrows only ever point *down* the
stack: `store.py` depends on `codec.py`, which depends on `tokens.py`, which is the only module that
ever meets an ASCII token — `events.py` sits at the bottom holding nothing but domain values, and
has no arrow into any of them. The blue boxes are `contrai-core`'s, and every one of them is a
*boundary*: `Card`, `Position`, `TeamSide`, `Bid` and `RuleConfig` are what the events are made of,
while `Auction`, `TrickRecord`, `ObservedPlay` and `ObservedContract` are what the projection derives
*through*. There is no arrow out of this package to anything but core. See
[Diagrams](../diagrams/) for the colour convention.

## Why a fifth package

The format has two producers and several consumers, and none of them may depend on each other.
The **engine** writes a record when it plays a game itself; the **scraper** writes one when it
watches an online table; the **verifier** reads what the *scraper* wrote and replays it through
the **engine**; the replay screens and every learner further up the AI ladder read both.

Putting the format inside `contrai-engine` would force the scraper to depend on the engine —
dragging Rich, the CLI and the whole orchestration layer into a process whose only job is to watch
a table. Putting it inside `contrai-scraper` would force the engine to depend on Playwright.
Either way the verifier's edge turns into a dependency cycle. So the format is its own package and
the edge becomes `core ← data ← {engine, scraper}`.

**The verdict model lives here for the same reason.** `contrai verify` is the engine's — it replays
a record through the real `Game` — but what it *concludes* is plain data about a record, and the
corpus tooling that reads those conclusions back must not import the engine to do it. So
`Verdict`, `GameVerdict` and `write_verdict` are `contrai-data`'s, and `contrai_engine.replay`
re-exports them unchanged.

`contrai-data` depends on `contrai-core` and on **nothing else at runtime** — no third-party wheel,
not even for JSON. That is not an aesthetic preference: a corpus outlives the code that wrote it,
and a reader that needs a pinned version of somebody else's parser to open a five-year-old game is
a reader that will eventually fail to open it. A test walks every module in the package and asserts
the edge rather than trusting review to notice.

## File shape

One UTF-8 file per game, one event per line, `.jsonl`. The first line is always the header; the
file is **append-only**, so a producer never rewrites what it has already written and a reader can
follow a game as it happens.

The header carries a `format` field spelled `family/major` — `contrai-record/1`. A loader that
meets a major it does not implement refuses the file outright with `UnsupportedFormatError`, rather
than working through it and producing a pile of confusing field errors. That is the whole point of
putting a version in the file: a *format change* and a *corrupt file* must not look alike.

## The event vocabulary

Eight events, and no more.

| Event           | What it records                                                        |
| --------------- | ---------------------------------------------------------------------- |
| `header`        | What this file is, who wrote it, and the game's identity                |
| `game_started`  | The table: its ruleset, its four seats, and where a mid-game join landed |
| `round_dealt`   | A deal — the round number, the dealer, all four hands                   |
| `bid`           | One bid, in auction order, with its 1-based gapless `seq`               |
| `card_played`   | One card, with its trick number and whether the play was reconstructed  |
| `belote`        | A King and Queen of one suit held by one seat, and whether it was called |
| `round_scored`  | The round's score line, as its source stated it                         |
| `game_ended`    | Why the record stops, and where the score stood                         |

What is **not** an event is as deliberate as what is. Three facts that a naive format would store
are absent because they are *derived*:

- **The established contract.** Re-derived by folding the round's `bid` events through core's
  `Auction` and reading `last_contract_bid` / `double_player` / `redouble_player` off it.
- **Who won each trick.** Re-derived through core's `TrickRecord.winner`, under the round's trump.
- **The all-pass redeal.** A round that was passed out is exactly a `round_dealt` followed by four
  passes; nothing else needs to say so.

A derived fact stored twice is a fact that can disagree with itself, and a record that disagrees
with its own derivations is a corpus nobody can trust.

## Events hold domain values, not tokens

An event's `position` field is a `Position`, not `"N"`. Its `card` is a `Card`, not `"10S"`. Its
`bid` is a core `Bid[Position]`. Its `ruleset.config` is a real `RuleConfig`.

Two things follow. **The format's spelling and its meaning move independently** — if `10S` ever
becomes `TS`, exactly one module changes and nothing above it notices; had events carried tokens,
the spelling would be baked into the projection, the verifier, the replay screens and every feature
extractor. And **consumers get the types they already reason in** — the projection hands
`Bid[Position]` values straight to `Auction` and `ObservedPlay` values straight to `TrickRecord`,
instead of every consumer re-parsing the same strings and each getting a chance to parse them
slightly differently.

The cost is one hop on the way in and out. That hop is `tokens.py`, and it is the only place a
domain value meets its ASCII spelling.

## Tokens

Deliberately narrow ASCII, so a record stays greppable and diffable and owes nothing to any wire's
glyphs.

| Thing            | Token                                                           |
| ---------------- | --------------------------------------------------------------- |
| Seat             | `N` `W` `S` `E` — the four initials, which happen to be distinct |
| Side             | `NS` `EW`                                                        |
| Card             | rank then suit: `7C`, `JH`, `AD`, and `10S` for the tens         |
| Contract trump   | `S` `H` `D` `C`, plus `NT` (no trump) and `AT` (all trump)       |
| Contract value   | the number itself — `80`…`240` — or the word `slam` / `solo_slam` |
| Timestamp        | ISO-8601, UTC, verbatim                                          |

**The tens are the reason `parse_card` splits from the right.** `10` is the only two-character
rank, so a parser slicing at a fixed offset reads the suit off the wrong character for all four of
them — and gets a *valid-looking* card back, not an error.

**Parsing is strict.** An unrecognised token raises rather than resolving to a default. A record is
read long after the run that wrote it, so a token quietly read as something else produces a
plausible game that never happened, and no downstream check can tell.

**The ruleset is the one asymmetric case.** An **unknown** knob is refused: it names a rule this
build has no field for, and dropping it would replay the game under rules it was never played
under — exactly the divergence the verifier cannot catch, since the verifier's whole method is to
replay under the rules the record names. A **missing** knob takes its `RuleConfig` default: the
record predates that knob, so its producer was by construction playing the default. Refusing a
missing knob instead would retire the entire existing corpus every time a knob is added.

**Timestamps are validated but returned verbatim.** The record is the archive; re-rendering an
instant on the way in would let a round-trip change bytes the producer wrote. What *is* checked is
the one thing that cannot be checked later — that the instant carries UTC. A naive or
locally-offset timestamp silently mis-orders events the moment two machines contribute to one
corpus.

## The line

One event, one JSON object, one line. A `round_dealt` in full:

```json
{"event": "round_dealt", "round": 1, "dealer": "E", "hands": {"N": ["7S", "8S", "9S", "10S", "JS", "QS", "KS", "AS"], "W": ["7H", "8H", "9H", "10H", "JH", "QH", "KH", "AH"], "S": ["7D", "8D", "9D", "10D", "JD", "QD", "KD", "AD"], "E": ["7C", "8C", "9C", "10C", "JC", "QC", "KC", "AC"]}, "hands_derivation": "self_play", "ts": "2026-09-10T18:18:15Z"}
```

**The event name is the first key.** `head` on a record then says what each line is without
scrolling sideways, which is the difference between a format you can inspect with ordinary tools
and one that needs a viewer.

**`ensure_ascii` is off.** A player named *Zoé* is stored as their name, not as `Zoé`. The file
is UTF-8; escaping would only make it unreadable to a human and no safer to a parser.

**Decoding is total.** Every field of a line is consumed, and every field an event needs must be
present. A line carrying a field this build does not know is *refused*, not ignored — a producer
writing fields we drop is a producer we are only half reading, and the half we dropped is exactly
the half that would have told us the corpus had drifted. The same check in reverse catches a
producer that stopped writing a field.

**The major version is a gate, not a label.** A header naming `contrai-record/2` raises
`UnsupportedFormatError` immediately; an unknown *family* raises the same. A malformed `format`
field — no slash, a non-numeric major — raises `RecordFormatError` instead, because that is a
corrupt line rather than a future format. Keeping the two apart is the whole point: a format change
must not be readable as damage, and damage must not be readable as a format change.

## What an event validates

Each event checks the invariant only *it* can see. None of them checks a rule of the game — that is
`PlayState`'s job in core, and the verifier's for a whole observed round. What these catch is a
**producer bug**: a mis-parsed capture that would otherwise land in the corpus looking entirely
plausible.

| Event          | Invariant                                       | The bug it catches                                    |
| -------------- | ----------------------------------------------- | ----------------------------------------------------- |
| `GameStarted`  | `seats` names all four positions                | A seat map that lost or doubled a seat                |
| `RoundDealt`   | Four seats, eight cards each, **32 distinct**   | A mis-decoded deal blob — a repeated or dropped packet |
| `BidMade`      | `seq >= 1`, and `bid.player is position`        | A bid filed under the wrong seat                       |
| `CardPlayed`   | `1 <= trick <= 8`                               | A trick counter that ran off its ladder                |
| `BeloteHeld`   | Exactly the King and Queen of one suit          | A pair assembled from two suits, or a K + J            |
| `RoundScored`  | Declarer ⇔ contract; `all_pass` ⇔ no contract; every side-keyed field names both sides | A score line that contradicts itself |

The 32-distinct-cards check is the one worth singling out. A parser that repeats a packet produces
four hands that are the right *shape* — four seats, eight cards each — and wrong. Without this
check the error surfaces three steps later in a legality check, where the symptom no longer names
the cause.

## Storage

```
$CONTRAI_HOME/records/          (or ~/.contrai/records)
├── games/
│   ├── engine-20260910T181815Z-a1b2c3.jsonl
│   └── obs-0a1b2c3d.jsonl
├── verdicts/                   (contrai verify's conclusions, one per game)
│   ├── engine-20260910T181815Z-a1b2c3.json
│   └── obs-0a1b2c3d.json
├── raw/                        (the scraper's verbatim wire frames)
└── catalog.sqlite              (derived index, rebuilt by contrai catalog; local only)
```

The engine roots its records at `$CONTRAI_HOME/records`; the scraper uses its profile's output
root. `records_root()`, `games_dir()`, `game_path()` and `new_game_id()` live in one module so the
two producers cannot spell the layout differently.

**Verdict files.** `contrai verify` writes one `verdicts/<game_id>.json` per record it checks, a
sibling of `games/` so a corpus and its verdicts move together. `verdicts_dir()`, `verdict_path()`
and `write_verdict()` spell that layout; the file is pretty-printed JSON with sorted keys, so
re-verifying a corpus produces a diff that shows only what changed. It carries the game's id,
source and preset, the game verdict, per-verdict round counts, the notes, and one object per round.

**Reading one back is as strict as reading a record.** `read_verdict()` (and the `from_json`
classmethods under it) refuses anything `write_verdict` would not have written: an unknown or
missing key, a repeated key, a `null` where an inapplicable field should simply be absent, a `bool`
where a number belongs, an unknown verdict, mismatch-kind or source token, a repeated round number.
It also **re-derives every conclusion the file stores** — each round's verdict from its own
mismatches and unchecked checks, the game's verdict and the per-verdict counts from the rounds — and
refuses a file that disagrees with itself. Whatever indexes a corpus trusts what it reads, so a
hand-edited or half-written verdict must surface as a `VerdictFormatError` naming the file rather
than as a plausible row. `VerdictFormatError` is a `RecordError`: a verdict file is part of the
corpus and is read with the same distrust.

**One line, one flush.** `RecordWriter` appends a single encoded event and flushes it, so a
producer that dies mid-game — a crashed scraper, an interrupted autoplay — leaves a file that is
complete up to its last full line. `fsync` is deliberately *not* called: the failure being guarded
against is a process ending, not a machine losing power, and a disk sync per card would make the
writer the slowest thing in the play loop.

**The reader is the other half of that bargain, and it is asymmetric on purpose.** A **final** line
that is not JSON is exactly what a torn write looks like, so it is dropped and reported through
`ReadResult.truncated`. A line that fails to parse **anywhere else** raises: only the last line can
be a crash artefact, and swallowing a bad line from the middle would silently drop an event from a
game. A final line that *is* well-formed JSON but carries an invalid token raises too — a crash
cannot produce valid JSON with a bad token, so that is a producer bug, not a torn write.

**Line endings are pinned to LF.** Records travel between the Windows development machine and the
Debian box that runs the scraper. Python's default newline translation would write CRLF on Windows,
and the stray CR would land inside the last token of every line for whichever machine did not write
it.

**Game ids.** `engine-<UTC stamp>-<6 hex>` for a self-played game — the random tail is what keeps
two games started in the same second apart. An observed game takes the table's own opaque id
(`obs-0a1b2c3d`), which is what lets a re-observed game be recognised rather than duplicated; it
is an opaque handle, not personal data. Because both producers take the id from outside the
process, `game_path` checks it is exactly one path segment before letting it become a file name.

## The projection

`load_game(path)` reads a file and folds it; `project(events)` folds a stream already in memory.
Both return a `GameRecord` — the header, the table's preset and `RuleConfig`, the four seats, where
a mid-game join landed, the rounds, the closing event, and two flags (`truncated`, `complete`).
Each `RoundRecord` carries the deal, the auction, the contract, the completed tricks and their
`derived` flags, any trailing partial trick, the belotes, the score line, the outcome, and
`complete`; `trump_suit` and `trick_winners` are computed on read.

**Four things are re-derived rather than stored**, each through the core type that already owns the
rule:

| Fact | Re-derived through |
| --- | --- |
| The established contract | `Auction.last_contract_bid` / `double_player` / `redouble_player` |
| The completed tricks | `TrickRecord`, cut where the `trick` field changes |
| Who won each trick | `TrickRecord.winner(trump)` — the same call the live play path makes |
| Whether a round was passed out | the auction: terminal, with no contract bid |

Because the winner rule is core's, a replayed record and a played game cannot disagree about who
took a trick.

**Two ordering rules, and they are not the same rule.**

*Rounds keep file order.* Round numbers are the source's deal count — monotonic, but not
necessarily starting at one and not necessarily contiguous, because a spectator joining mid-game
skips the round it walked in on. Nothing does arithmetic on a round number.

*Events attach to a round by number.* The observed state snapshot describes the last **completed**
round, so a score read while round N+1 is being played carries round N and lands in the file *after*
round N+1's deal. Attaching by "the round currently being built" would file it under the wrong
round — and nothing in the file would look wrong.

**`contract` is an `ObservedContract`, not a `Contract`.** Core's `Contract` reads
`contract_bid.player.team`, so building one needs a live `BasePlayer` with a `Team` behind it. A
record's auction holds bids seated on bare `Position` values, so a `Contract` is unbuildable from a
record without inventing four players — and those players would carry reachable (empty) hands,
exactly the leak `ObservedPlay` exists to prevent. `ObservedContract` carries the identical terms
and is already what `PlayObservation` hands a strategy.

**`complete` is about structure, not about scoring.** A round is complete when its auction closed
and either it was passed out with no cards played, or all eight tricks were played out. A *score is
not part of it* — a round with no score source is still a complete round (D3). A game is complete
when it ends, was not truncated, and every round is complete. Correspondingly, `outcome` is `None`
for a contracted round with no score line: made-or-failed is a scoring question and scoring lives
in the engine, so the projection says "unknown" rather than guessing. `ALL_PASS` is the one outcome
it can derive on its own.

**Structure, never legality.** Whether a card could lawfully be played is `PlayState`'s question;
whether a whole observed round obeys the rules is the verifier's. This layer only asks whether the
events fold into rounds at all. In particular it never calls `Auction.is_legal` — a seatless
auction has no teams to compare, so it could not decide who was entitled to call *coinche*, and a
double that really happened at the table must not be refused here (D4).

The seat-visible projection (`RoundRecord.visible_to(seat)`) is deliberately deferred to the
supervised-learning step. Because events already hold whole hands keyed by seat, hiding what a seat
cannot see is a *filter* over this structure rather than a transform of it — which is why it costs
nothing to postpone.

## Nullability the observations forced

Three places where the format says "unknown" rather than inventing a value. All three come from the
P-A dry run against real observed tables, and all three are load-bearing.

**`game_ended.ts` is nullable**, and it is the only `ts` that is. A game we walked away from has no
wire timestamp for its end, because the wire never sent one — we simply stopped listening. Stamping
the observer's own clock would put a timestamp describing *our process* among timestamps that all
describe the game, with no way for a reader to tell the two apart.

**`game_ended.totals` is nullable** too: the end-of-game event on the wire carries no scores.

**`round_scored` is absent, not zeroed,** when no score source covered the round. A game joined
mid-play, or left before the scoreboard updated, simply has rounds whose score is unknown. A
zero-filled score line would be a claim; no line at all is the truth.

**`observed_from` records where a mid-game join landed** — the round in progress, the phase, and
the running score at that moment. A spectator sitting down mid-game cannot rescue the round it
walked in on: the wire's state snapshot describes the last *completed* round, so the round in
progress is unrecoverable and is skipped entirely. Recording starts at the next deal, and
`observed_from` is what says so.

## The catalog

`build_catalog(root)` folds every record and verdict file under a records root into
`<root>/catalog.sqlite`, a SQLite database the corpus questions are asked of: *which games did this
player play*, *which rounds are clean enough to train on*, *how many rounds a day are landing*,
*which suspect rounds share a mismatch class*. Each of those is a join over thousands of files, and
a directory of JSONL cannot answer a join without reading all of it. `contrai catalog` runs it
from the engine CLI (see [engine docs](../engine/index.md#cli)).

This section starts with how to *use* the catalog — building it, reading its summary, following a
player and replaying their games, querying it — and then explains how it is built and what each
table means.

### Quick start

A corpus is a **records root**: a directory holding `games/` (one `.jsonl` record per game) and,
once `contrai verify` has run, `verdicts/` (one `.json` verdict per game). The catalog is written
next to them as `catalog.sqlite`.

| Games | Records root |
| --- | --- |
| Played and recorded by the engine (`contrai --record`) | `$CONTRAI_HOME/records` — `~/.contrai/records` when the variable is unset. It is also the default when `ROOT` is left out. |
| Recorded with `contrai --record DIR` | `DIR` |
| Watched by the scraper | the `[output].root` directory of the scraper's profile |

Two commands, in this order, every time new games have landed:

```bash
uv run contrai verify ROOT      # 1. judge every game — writes ROOT/verdicts/<game_id>.json
uv run contrai catalog ROOT     # 2. rebuild ROOT/catalog.sqlite and print what it holds
```

The order matters. The catalog *reads* verdicts, it does not compute them, and a game whose verdict
is missing or older than its record contributes no clean round (see
[Verdict status](#verdict-status-and-clean-rounds)). Both commands are safe to re-run as often as you
like: `verify` overwrites each verdict file, and `catalog` rebuilds from scratch. `ROOT/games` is
accepted in place of `ROOT`.

**Work on a copy of a remote corpus, and keep its file times.** Whether a verdict is still fresh is
decided by comparing file modification times, so copy a corpus off the box with a tool that
preserves them — `cp -a` / `rsync -a`, or on Windows `robocopy SRC DST /E /COPY:DAT /DCOPY:T`. A
plain copy re-stamps every file in copy order, and some verdicts then look older than their records.
If that happens, re-running `contrai verify` on the copy fixes it.

### Reading the summary

`contrai catalog ROOT` prints what the new catalog holds. On the 135 games of the first box week:

```text
catalog: ROOT/catalog.sqlite  (built 2026-09-24T17:59:04Z)
games: 135 (observed 135)
rounds: 685, complete 685, clean 679
game verdicts: partial 129, suspect 6
verdict status: fresh 135
round verdicts: partial 679, suspect 6
players: 324
```

| Line | What it counts |
| --- | --- |
| `games` | Indexed games, split by source — `engine` (played here) or `observed` (watched online). |
| `rounds` | Every round of every indexed game; those whose structure finished (auction closed, all eight tricks played or a pass-out); and the **clean** ones — finished, up-to-date verdict, replayed, not suspect. Clean rounds are what a training set is drawn from. |
| `game verdicts` | Games per verdict, among games with a readable verdict. A game takes the worst verdict of its rounds. |
| `verdict status` | Games per status: `fresh`, `stale`, `missing`, `unreadable`. Anything but `fresh` is followed by a line such as `without a fresh verdict: 3 — run: contrai verify ROOT`. |
| `round verdicts` | Rounds per verdict, among rounds a readable verdict covers. |
| `players` | Distinct player ids. Engine seats have none, so a corpus of engine games reports 0. |

For observed games `partial` is the normal, clean verdict — every check that could run passed, and
`verified` is out of reach because the table never says which side took the last trick. A
`skipped: N` block, when present, lists every file that could not be indexed and why; everything
else was indexed regardless.

### Following a player

**Ids and names.** An observed seat carries two things. The **player id** is the table's own
account identifier: stable across games, and the key everything joins on. The **name** is the
display name shown in that game: a player can change it, and several players can share one — five
ids share a single name in the first box week. Engine seats carry no id at all; their names are
`human` for you and `ai:<level>` for an AI (`ai:expert`, say).

`--player` accepts either. Look a player up by name first, read the id off the output, then keep
using the id:

```bash
uv run contrai catalog ROOT --player "Some Name"   # every seat that carried this name
uv run contrai catalog ROOT --player PLAYER_ID     # every game of this player
```

The lookup **only reads** the catalog: it never rebuilds it, and it never creates one. Games
recorded since the last build are not in it, which is why the header names the build time — run
`contrai catalog ROOT` first to include them. The output has one line per game, oldest first:

```text
PLAYER_ID — games: 10 (catalog built 2026-09-24T17:59:04Z)
2026-09-18  obs-0a1b2c3d  PLAYER_ID  Some Name  seat S  partner Other Name  won  NS 2010 – EW 1500  partial
2026-09-18  obs-5e6f7a8b  PLAYER_ID  Some Name  seat S  partner Third Name  lost  NS 1690 – EW 2030  partial
```

| Field | Meaning |
| --- | --- |
| date | The day the record was opened, in UTC. |
| game id | The record's id — normally its file name under `games/`. |
| player id | The seat's id, or `-` for an engine seat. |
| name | The name the seat carried *in that game*. |
| `seat N/W/S/E` | Where the player sat — the seat to follow when replaying. |
| `partner` | The partner's name in that game (`-` if unknown). |
| outcome | `won` or `lost` when the winner is known; otherwise why the record stops — `observer_left`, `abandoned`, `interrupted`, or `target_reached` when the game ended but its winner cannot be derived — both sides over the target, say (see [The winner](#the-winner)); `unfinished` when the record has no end at all. |
| score | The final totals, `NS a – EW b`, with `?` when the record does not say. |
| verdict | `verified`, `partial` or `suspect`; `partial (stale)` when the verdict predates the record; `no verdict` or `unreadable verdict` otherwise. |

When a name sits in more than one seat of a game — an engine game has four `ai:expert` seats — the
header counts games and seats apart: `games: 1, seats: 4`, with one line per seat.

The command exits `1`, with a message, when there is no catalog under `ROOT` yet, or when it holds
no game for that id or name.

For more than a list, query the views directly (see [Querying the catalog](#querying-the-catalog)):

```sql
-- the player's record in one row: games, wins, losses, first and last seen, every name used
SELECT * FROM players WHERE player_id = 'PLAYER_ID';

-- how their name and their level changed over time
SELECT name, games, first_seen, last_seen FROM player_names
WHERE player_id = 'PLAYER_ID' ORDER BY first_seen;
SELECT level, games, first_seen, last_seen FROM player_levels
WHERE player_id = 'PLAYER_ID' ORDER BY first_seen;

-- who they play with most, and how that goes
SELECT p.name AS partner, COUNT(*) AS games,
       COUNT(*) FILTER (WHERE s.result = 'won') AS wins
FROM seats s
JOIN seats p ON p.game_id = s.game_id AND p.side = s.side AND p.position != s.position
WHERE s.player_id = 'PLAYER_ID'
GROUP BY p.player_id ORDER BY games DESC;
```

### Replaying a player's games

The catalog finds the games; `contrai replay` shows them. It opens a record on a list of its
rounds, each with its verdict, and steps the one you pick through the game's own screens with
**every hand face up** — so watch the seat `--player` gave for that game. Everything about the
replay itself — the round list, every key, where it stops, the trick grid — is on
[Replaying a game](../engine/replay.md); the keys you need first:

| Where | Key | Does |
| --- | --- | --- |
| round list | `7` | step round 7 through the game screens |
| round list | `g 7` | show round 7 whole, as a [trick grid](../engine/replay.md#the-trick-grid) |
| round list | `q` | close the record |
| stepping | `n` or Enter | next action (a bid or a card) |
| stepping | `t` / `r` | to the end of the trick / of the round |
| stepping | `a` | skip the rest of the auction, stop on the contract |
| stepping | `p` | back one stop |
| stepping | `q` | back to the round list |

The [full key table](../engine/replay.md#the-keys) adds `g` (the round so far as a grid) and `w`
(the AI's reasons, when an engine AI held a seat).

**One game.** Take a game id from the `--player` output:

```bash
uv run contrai replay ROOT/games/obs-0a1b2c3d.jsonl             # opens on the round list
uv run contrai replay ROOT/games/obs-0a1b2c3d.jsonl --round 7   # straight into round 7
```

A game id is the file name for every record a producer wrote. If a file was renamed, ask the
catalog where it lives: `SELECT path FROM games WHERE game_id = 'obs-0a1b2c3d'` gives the path
relative to `ROOT`.

**All of a player's games, one after the other.** Save this as `replay_player.py` anywhere outside
the repository — it is a local helper, not part of the package:

```python
"""Replay every game one player sat in, oldest first."""

import sqlite3
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])  # the records root holding catalog.sqlite
player = sys.argv[2]  # a player id (or a display name)
extra = sys.argv[3:]  # passed on to contrai replay, e.g. --round 3

with sqlite3.connect(root / "catalog.sqlite") as catalog:
    games = catalog.execute(
        """
        SELECT g.path, g.game_id, s.position, s.name
        FROM seats s JOIN games g USING (game_id)
        WHERE s.player_id = ? OR s.name = ?
        ORDER BY g.created_at, g.game_id
        """,
        (player, player),
    ).fetchall()

try:
    for index, (path, game_id, position, name) in enumerate(games, start=1):
        print(f"[{index}/{len(games)}] {game_id}: {name} is seat {position}")
        subprocess.run(["contrai", "replay", str(root / path), *extra])
except KeyboardInterrupt:
    print("stopped")
```

```bash
uv run python replay_player.py ROOT PLAYER_ID
```

Each game opens on its round list; `q` there moves on to the next game, and Ctrl+C stops the
whole run. Before each game the script prints which seat the player holds in it. It goes through
`games.path`, so a renamed file is still found.

**Only some rounds.** Ask the catalog which rounds are worth watching, then open each with
`--round`:

```sql
-- the rounds where the player's game went suspect, and what the verifier found
SELECT g.path, m.round, m.kind, m.detail, s.position AS seat
FROM seats s JOIN games g USING (game_id) JOIN mismatches m USING (game_id)
WHERE s.player_id = 'PLAYER_ID'
ORDER BY g.created_at, m.round, m.n;

-- the rounds the player declared, with the contract and how it went
SELECT g.path, r.round, r.contract_value, r.contract_slam, r.trump, r.multiplier, r.outcome
FROM seats s JOIN games g USING (game_id)
JOIN rounds r ON r.game_id = s.game_id AND r.declarer = s.position
WHERE s.player_id = 'PLAYER_ID'
ORDER BY g.created_at, r.round;
```

```bash
uv run contrai replay ROOT/<path> --round <round>
```

Two things to know. `contrai replay` checks the record **live** when it opens it, so the verdicts
in its round list are always current even when the catalog calls a verdict stale. And it writes
nothing — no record, no verdict, no change to the catalog.

### Querying the catalog

The catalog is a plain SQLite file, so any SQLite client reads it. Three ways that need nothing
beyond the workspace:

```bash
uv run python -m sqlite3 ROOT/catalog.sqlite                      # an interactive SQL shell
uv run python -m sqlite3 ROOT/catalog.sqlite "SELECT * FROM players ORDER BY games DESC LIMIT 10"
```

```python
import sqlite3
import pandas as pd

with sqlite3.connect("ROOT/catalog.sqlite") as catalog:
    players = pd.read_sql("SELECT * FROM players ORDER BY games DESC", catalog)
```

The `sqlite3` shell creates an empty database when the path is wrong, so check the path if every
table seems to be missing. It also prints rows as Python tuples through the console's encoding: on a
Windows console, a display name outside that code page raises `UnicodeEncodeError` — set
`$env:PYTHONIOENCODING = "utf-8"` (PowerShell) first, or use a notebook.

Start from the four views; the tables they are built on are described under [Schema](#schema).

| View | One row per | Columns |
| --- | --- | --- |
| `players` | player id | `games`, `wins`, `losses`, `first_seen`, `last_seen`, `last_name`, `last_level`, `names` (every name used, as a JSON array) |
| `player_names` | player id and name | `games`, `first_seen`, `last_seen` |
| `player_levels` | player id and level | `games`, `first_seen`, `last_seen` |
| `clean_rounds` | clean round | every `rounds` column, plus the game's `source`, `preset`, `created_at` and `path` |

Dates are the records' own `created_at`, ISO-8601 in UTC, so they sort and compare as text.

**Close your connection before rebuilding.** On Windows a catalog open in a shell or a notebook
cannot be replaced, and `contrai catalog` stops with a message saying so (below).

### When something goes wrong

| Message or symptom | Cause | What to do |
| --- | --- | --- |
| `ROOT: no games/ directory, nothing to index` | `ROOT` is not a records root. | Point at the directory that *contains* `games/`. |
| `… cannot be replaced (…). Close the program holding it open …` | Windows: a shell, notebook or DB browser still has `catalog.sqlite` open. | Close it and run again. The old catalog is untouched. |
| `no catalog under ROOT — build it first: contrai catalog ROOT` | `--player` before any build. | Run `contrai catalog ROOT` once. |
| `NAME: no game in the catalog built …` | The id or name is not in the catalog — misspelt, or recorded since the last build. | Check the spelling (names are exact, case included); rebuild. |
| `… has schema version N, this build reads M; rebuild it` | The catalog was built by another version of the code. | Run `contrai catalog ROOT`. |
| `… is not a catalog` | `ROOT/catalog.sqlite` is some other file. | Delete it and rebuild. |
| `without a fresh verdict: N — run: contrai verify ROOT` | Games with no verdict yet, or a verdict older than its record. | Run the `verify` command it shows, then rebuild. |
| `skipped: N` lines | A file could not be read, a verdict has no record, or two files claim one game id. | Read each line's reason. The rest of the corpus is indexed regardless. |
| Clean rounds suddenly drop after copying a corpus | The copy re-stamped file times, so verdicts look stale. | Copy preserving times, or re-run `contrai verify` on the copy. |

`contrai catalog` exits `1` whenever it prints one of the first six messages, `2` on a usage
error, and `0` otherwise — games without a fresh verdict and skipped files are reported, not
treated as failures.

### How it is built

**It is an index, never a second copy.** Every row is derived from a record or a verdict, the
records stay the source of truth, and nothing writes to the catalog except a rebuild. A catalog can
be deleted at any time. That is also why there are no schema migrations: `PRAGMA user_version`
names the schema, a catalog of another version is simply rebuilt, and a reader refuses one with
`CatalogError`.

**It is rebuilt whole, every run.** `load_game` costs 2.0 ms a game (measured on 135 observed
records), so ten thousand games rebuild in about twenty seconds. An incremental index would need
change detection, deletion handling and a migration story, for a file that is by construction
disposable.

**It is swapped in atomically.** The build writes a temporary `catalog.sqlite.*.tmp` beside the old
catalog, with no journal and no sync — a failed build is thrown away whole — and moves it over the
old one with `os.replace`. A reader sees the old catalog or the new one, never half of either, and a
failed build leaves the previous catalog as it was. On Windows `os.replace` is refused while
another program — a `python -m sqlite3` shell, a notebook — holds the catalog open; the build then
raises `PermissionError` and removes its temporary file.

**Nothing aborts the build.** An unreadable record (not UTF-8, not the format, an impossible
ruleset), an unreadable verdict, a verdict naming another game, a verdict with no record, a second
file claiming a game id — each lands in the `skipped` table with its reason, and the rest of the
corpus is indexed regardless. Of two files claiming one id, the one named after the id is indexed
(it is what the producers' `game_path` writes), else the first in sorted order. A record whose last
line is torn is indexed, with `truncated = 1`.

**It holds personal data.** Seats carry the table's player ids and display names, unaltered, so
`catalog.sqlite*` is git-ignored and the catalog stays on the machine that built it. When the
records are pseudonymised, the catalog is simply rebuilt from them.

**Copy a corpus with its modification times.** Staleness is read off file times (below), so a copy
made with `cp -a` or `robocopy /COPY:DAT` keeps every verdict fresh, while a plain copy re-stamps
files in copy order and can make fresh verdicts look stale.

### Schema

Tables are `STRICT`, booleans are `0`/`1`, and seats, sides and trumps are the record's own tokens
(`N`/`W`/`S`/`E`, `NS`/`EW`, `S`/`H`/`D`/`C`/`NT`/`AT`).

| Table | Key | Holds |
| --- | --- | --- |
| `meta` | `key` | `schema_version`, `built_at`, `generator`, `root` |
| `games` | `game_id` | the file (`path`, relative), `source`, `generator`, `created_at`, `ended_at`, `preset`, where a mid-game join landed, round counts, `complete`, `truncated`, `end_reason`, final totals, `winner` and `winner_basis`, the game `verdict`, `verdict_status`, `verdict_notes` |
| `seats` | `game_id`, `position` | `side`, `player_id`, `name`, `account`, `kind`, `level`, `result` (`won` / `lost`) |
| `rounds` | `game_id`, `round` | `dealer`, bid / trick / derived-trick / belote counts, `complete`, the contract (`declarer`, `declarer_side`, `contract_value` or `contract_slam`, `trump`, `multiplier`), `outcome`, `slam`, `score_source`, taken / marked / total points per side, and the round's `verdict`, `replayed`, `unchecked` |
| `mismatches` | `game_id`, `round`, `n` | `kind`, `detail`, `position`, `trick`, `seq`, `expected`, `observed` |
| `skipped` | — | `path`, `kind` (`record` / `verdict`), `reason` |

Four views sit on top: `players` (one row per player id — games, wins, losses, first and last seen,
latest name and level, every name as a JSON array), `player_names` and `player_levels` (one row per
id and name, or id and level, dated), and `clean_rounds` (below).

The contract columns come from the contract the projection derives off the **auction**, not from the
score line's own claim — the derivation is what the verifier checked. `marked_ns` / `marked_ew` are
the made and announced marks together. Round verdicts are joined by the record's **round number**,
never by position, because round numbers are deal counts that may start above one and skip.

### Verdict status and clean rounds

A verdict file carries no timestamp and no hash of the record it judged, so whether it still
describes its record is inferred:

| `verdict_status` | Meaning |
| --- | --- |
| `fresh` | Readable, no older than its record, covering the same round numbers, source and preset |
| `stale` | Readable, but older than its record, or over other rounds, or naming another source or preset |
| `missing` | No verdict file — `contrai verify` has not run on this game |
| `unreadable` | The file fails `read_verdict`, or names another game (also listed in `skipped`) |

A false "stale" only keeps a game's rounds out of `clean_rounds` until the next `contrai verify`; a
false "fresh" would let an unchecked round in. Every doubt therefore goes the stale way.

A round is **clean** — a row of `clean_rounds` — when its structure finished, its game's verdict is
`fresh`, it was replayed, and its verdict is `verified` or `partial`. `partial` counts as clean on
purpose: for an observed record `verified` is unreachable, because the table folds the last-trick
bonus into the card points and never says which side took it, so `partial` with nothing wrong is
the best an observed round can be.

Three kinds of clean round are kept in the view and left to the reader to filter, because whether
they belong in a dataset depends on what it is for:

- **all-pass rounds** — no contract, no cards: `WHERE outcome IS NOT 'all_pass'`, or
  `trick_count = 8` for play-phase work (`IS NOT`, not `!=`: an unscored round's outcome is `NULL`,
  and `NULL != 'all_pass'` drops it);
- **disputed rounds** — two score sources disagreed: `WHERE outcome IS NOT 'disputed'`;
- **reconstructed tricks** — a trick deduced rather than seen. Every observed round has one today
  (685 of 685 box rounds: the wire never shows the last trick being played), so
  `WHERE derived_trick_count = 0` keeps engine rounds only; drop the reconstructed *trick* in the
  loader instead, where each `RoundRecord.derived_tricks` flag says which one it is.

### The winner

The scraper never records a winner (`game_ended.winner` is always `null` in observed records), so
the catalog derives one. The recorded winner is used when there is one (`winner_basis =
'recorded'`). Otherwise a game that ended `target_reached` with known totals is won by the side at
or above the ruleset's target — but only when **exactly one** side is (`winner_basis = 'totals'`).
Both sides over the target is a game the belote gate or sudden death decided, rules that live in the
engine; the catalog leaves it `NULL` rather than guess. `seats.result` follows from the winner.

### Recipes

The catalog is plain SQLite: `uv run python -m sqlite3 <root>/catalog.sqlite` opens a shell, and a
notebook reads it with `pandas.read_sql(query, sqlite3.connect(path))`. The ids and names below are
placeholders.

*My own engine games* — engine seats carry no player id, so match the seat name:

```sql
SELECT g.created_at, g.game_id, s.position, s.result, g.total_ns, g.total_ew
FROM seats s JOIN games g USING (game_id)
WHERE g.source = 'engine' AND s.name = 'human'
ORDER BY g.created_at;
```

*One player's name history* (more player queries under
[Following a player](#following-a-player)):

```sql
SELECT name, games, first_seen, last_seen
FROM player_names WHERE player_id = 'PLAYER_ID' ORDER BY first_seen;
```

*Clean rounds for a dataset*, then each one loaded back through the record it came from:

```sql
SELECT path, round FROM clean_rounds
WHERE outcome IS NOT 'all_pass' AND outcome IS NOT 'disputed'
ORDER BY path, round;
```

```python
from contrai_data import load_game
record = load_game(root / path)
round_ = next(r for r in record.rounds if r.number == number)
```

*Progress toward a milestone*, rounds per day:

```sql
SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS rounds
FROM clean_rounds GROUP BY day ORDER BY day;
```

*Suspect rounds by mismatch class*, for the verifier's follow-up:

```sql
SELECT m.kind, COUNT(*) AS rounds, group_concat(m.game_id || '#' || m.round, ' ') AS examples
FROM mismatches m JOIN games g USING (game_id)
WHERE g.verdict_status = 'fresh' AND m.n = 1
GROUP BY m.kind ORDER BY rounds DESC;
```
