# contrai-data

The ContrAI **game-record format** — one append-only JSONL file per game of contrée, shared by
everything that produces or consumes one.

## Module map

Source lives at `packages/contrai-data/src/contrai_data/`:

| Module          | Contents                                                                                  |
| --------------- | ----------------------------------------------------------------------------------------- |
| `exceptions.py` | `RecordError` (base), `RecordFormatError`, `UnsupportedFormatError` — all of them both a `ContraiError` and a `ValueError` |
| `events.py`     | One frozen dataclass per event (`Header`, `GameStarted`, `RoundDealt`, `BidMade`, `CardPlayed`, `BeloteHeld`, `RoundScored`, `GameEnded`), the five value objects (`Seat`, `ObservedFrom`, `Ruleset`, `SideMark`, `ContractTerms`), and the eight closed vocabularies |
| `tokens.py`     | Domain value ⇄ ASCII token, both ways and strictly — seats, sides, cards, contract suits and values, whole bids, whole rulesets, and the UTC timestamp check |
| `codec.py`      | `encode` / `decode` — one event ⇄ one JSON line — plus `FORMAT` and the major-version gate |
| `store.py`      | The only module that touches the filesystem: `RecordWriter`, `read_events` / `ReadResult`, `records_root` / `games_dir` / `game_path`, `new_game_id` |
| `projection.py` | `GameRecord` / `RoundRecord`, and the `project` / `load_game` fold that re-derives the contract, the tricks and their winners |

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
│   └── obs-56630b35.jsonl
└── raw/                        (the scraper's verbatim wire frames)
```

The engine roots its records at `$CONTRAI_HOME/records`; the scraper uses its profile's output
root. `records_root()`, `games_dir()`, `game_path()` and `new_game_id()` live in one module so the
two producers cannot spell the layout differently.

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
(`obs-56630b35`), which is what lets a re-observed game be recognised rather than duplicated; it
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
