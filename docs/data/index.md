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

*One player's name history:*

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
