# Changelog

All notable changes to the ContrAI workspace are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
All four workspace packages (`contrai-core`, `contrai-engine`, `contrai-analyzer`,
`contrai-scraper`) are versioned in lockstep — a single version covers the whole workspace.

## [Unreleased]

### Added

- (core) `RuleConfig` — frozen dataclass of the 22 table-rule knobs of §9 with its three enums, the `classic` preset and `InvalidRuleConfigError`. No knob changes behaviour yet. See [core docs](docs/core/index.md).
- (core) `PlayState.rules` — the play state carries its `RuleConfig` (default `RuleConfig()`) through `start`, `apply` and `with_hands`; not consulted yet.
- (engine) `Game` and `Round` accept a `RuleConfig` and thread it down to `PlayState`; `score_round` reads it off the round. The default reproduces today's behaviour exactly.
- (engine) `contrai --rules FILE` / `--preset classic` — load the table ruleset from a TOML file or by name (mutually exclusive); unknown keys are rejected. See [engine docs](docs/engine/index.md).
- (core) `AllTrumpRules` — all trump is a playable regime: every suit ranks as trump on the §3.3 scale, only the led suit competes. See [core docs](docs/core/index.md).
- (core) `Auction` runs under a `RuleConfig`: `extended_trump_choices` gates no trump and all trump, and each mode caps at its §5.2 ladder top.
- (engine) No trump and all trump are biddable at a table with `extended_trump_choices = true`; type `nt` / `at` at the prompt. See [engine docs](docs/engine/index.md).
- (engine) All-trump rounds track every K + Q pair and mark 20 each, under the `none` / `single` / `four` regime. See [engine docs](docs/engine/index.md).
- (engine) `mark_made_points`, `mark_announced_points` and `only_announced_points_multiplied` decide what a round marks. See [engine docs](docs/engine/index.md).
- (engine) `any_failure_marks_160`, the two `failed_slam_marks_*` switches and `unannounced_slam_substitute` reshape what a failed or swept round marks.
- (engine) `belote_counts_toward_contract` and `belote_lost_when_contract_fails` decide whether a belote makes the contract and who keeps it.
- (engine) `rounding` — marks are written exact, to the nearest 10, or to the nearest 5; halves round up. Contract success stays exact.
- (engine) `win_on_belote_points_alone` — switched off, the Belote of the crossing round does not carry a team past the target.

### Changed

- (core) **BREAKING:** `ContractBid` accepts all six contract trumps and values to 240; which are bookable is now `Auction`'s call. Use `bookable_suits(rules)`, not `ContractBid.VALID_SUITS`.
- (engine) **BREAKING:** `Round.belote_holder` is gone. Read `belote_pairs` (holder → suits) or `belote_counts_by_side`; `belote_state` is keyed by `(player, suit)`.
- (engine) Round scoring is built from §7.2's made-points and announced-points components; `RoundScore` carries both per side. See [engine docs](docs/engine/index.md).
- (engine) **BREAKING:** `Round.round_scores` / `contract_made` / `unannounced_slam` are read-only properties over the new `Round.round_score`. `scoring.unannounced_slam_substitute` is now `sweep_substitute`.
- (engine) The round recap reads its breakdown off the scored round, so its Scoring rows are the two components and track the table ruleset. See [engine docs](docs/engine/index.md).
- (engine) **BREAKING:** The attack must now out-score the defense to make its contract (§7.5 default); an exact tie fails it. Set `attack_must_outscore_defense = false` for the old behaviour.

### Fixed

- (engine) The recap's Outcome table credits a belote to the side that *held* it, so a transfer to the defense no longer rewrites the play tally.
- (core) The over-trump obligation ranks candidates with `trick_rank`, so an off-suit discard can no longer raise the bar at all trump.
- (engine) A no-trump contract renders as `NT` / `No Trump` instead of `NoTrump No Trump`, and its bid cell no longer overflows the history column.

## [0.3.0] - 2026-08-15

Typed-and-sealed release: seats, sides and contract trumps become values rather than strings (`Position`, `TeamSide`, `Suit`/`TrumpVariant`), the trick rules sit behind a single `TrumpRules` seam, `PlayObservation` is fully sealed and the played-out round lives entirely on the core `PlayState`; the CLI gains `--debug`/`--seed`/`--autoplay`, and no-trump and Slam-family scoring are brought in line with the domain reference.

### Added

- (core) `TrumpRules` — one sealed rules object per contract regime, resolved by `rules_for(contract_suit)`, answering trumpness, points, in-suit ranking, trick competition and belote. A future variant is a new leaf, not a sweep over call sites. See [core docs](docs/core/index.md).
- (core) `TrickRecord` — the completed-trick value: an immutable four-record tuple that knows its `led_suit` and its `winner(trump_suit)`, returning the winning play record rather than just the seat.
- (core) `is_trump(card_suit, contract_suit)` and `trump_suits(contract_suit)` — the single answer to "is this card trump under this contract", plus `Card.is_trump(trump_suit)` sugar.
- (core) **BREAKING:** `Position` seat enum — anticlockwise `next` / `partner` / `opponents` / `is_teammate`, strict parsing, French seat names. `BasePlayer.position` is a `Position`, no longer a free-form string.
- (core) `TeamSide` enum (`NS` / `EW`) and `Position.team_side` — team identity as a value, the key every score mapping uses. Not a `StrEnum`, so a leftover name key misses loudly instead of resolving by accident.
- (core) `PlayState.card_points_by_side` and `trick_counts_by_side` — what each side has captured, derived from the play history and scored through the contract's `TrumpRules`. The raw pile only, so a finished round sums to 152, not 162.
- (core) `card_queries.py` — `count_suit` / `cards_of_suit` / `has_suit` / `has_card` over any `Iterable[Card]`, so `Hand` and the play path's frozen tuples read through one implementation.
- (core) `seal_bid(bid)` — projects a bid onto its bidder's seat. `Bid` is now generic over its actor, so an auction holds `Bid[BasePlayer]` and an observation `Bid[Position]`.
- (core, engine) A game can be built from an unseated roster: `Game([p1, p2, p3, p4])` seats four positionless players in anticlockwise order. A half-seated list is refused rather than completed.
- (engine) `contrai --debug / --seed / --autoplay` — face-up debug view, diagnostics logged to `contrai-debug.log`, reproducible deals, and unattended 4-AI autoplay. See [engine docs](docs/engine/index.md).

### Changed

- (core) **BREAKING:** Card suits and contract trumps are two types. `Suit` holds the four card-bearing suits, `NO_TRUMP` / `ALL_TRUMP` move to the new `TrumpVariant`, and `ContractSuit` is their union. `Card` rejects a non-`Suit` with `InvalidCardError`.
- (core) **BREAKING:** All-trump is no longer bookable — `ContractBid.VALID_SUITS` drops it, so the auction offers 65 opening contracts instead of 78.
- (core) **BREAKING:** `Card` is pure identity. `Card.is_trump` / `get_points` / `get_order` and its class-level tables are gone; ask the contract's `TrumpRules` via `rules_for(contract_suit)` instead.
- (core) **BREAKING:** `PlayObservation` is fully sealed — no live player is reachable from it by any path. `observation.player` becomes `observation.position`, `contract` is an `ObservedContract` naming seats, trick records are `ObservedPlay`, and `bids` holds `Bid[Position]`.
- (core) `PlayState.completed_tricks` and `PlayObservation.completed_tricks` hold `TrickRecord` values instead of bare four-play tuples. `TrickRecord` subclasses `tuple`, so sequence consumers keep working unchanged.
- (engine) **BREAKING:** Every team score is keyed by `TeamSide` instead of the team's name string — `RoundScore.scores`, `Round.round_scores`, `Game.scores`, `GameOverStatus`. Rewording a display label can no longer break a lookup.
- (engine) **BREAKING:** Seating, dealer rotation, team formation and the AI's partner reasoning flow through `Position`; the seat-order list, hardcoded partner map and position-string scans are gone.
- (engine) **BREAKING:** The view's trick hooks receive a sequence of core `Play` records instead of a `Trick`. A `Play` unpacks exactly like `Trick.get_plays()` returned, so in-progress and completed tricks reach the screens as one type.
- (engine) The card-point rule is implemented once: round scoring, the in-game "Round pts" line and the recap all read `PlayState.card_points_by_side`. Every number is unchanged; the running total now refreshes a frame earlier.
- (engine) Round scoring reads the authoritative core play state rather than the view-facing mirrors. A malformed trick history now fails loudly instead of silently scoring 0–0.
- (analyzer) **BREAKING:** The probability and bidding stack is an importable `contrai_analyzer` package — import `contrai_analyzer.models` / `.engine` / `.bidding`, not `src.*`. Dashboard behaviour is unchanged, and it stays independent of `contrai-core`.
- (scraper) **BREAKING:** The scraper is an importable `contrai_scraper` package launched by the `contrai-scrape` console script, split into `session` / `observer` / `config` / `cli`. `python main.py` is gone.

### Removed

- (engine) **BREAKING:** `Round`'s trick mirror — `tricks`, `current_trick`, `team_tricks`, `last_trick_winner`, and `play_all_tricks`' return value. The played-out round lives entirely on `Round.play_state`. Core's `Trick` is untouched.
- (core) **BREAKING:** `Hand.copy()`, `Hand.count_rank()`, `Hand.is_complete()` and `Hand.__getitem__` — callerless once `PlayState.legal_actions` became the legality oracle. A hand is a bag, not a sequence; write `list(hand)` for a positional view.
- (core) **BREAKING:** `Team.get_partner`, `Team.contains_player`, `Team.total_score` and `Team.add_points`. `Team` is now the roster plus its display name; ask `Position` for pairings and `Game.scores` for totals.
- (core) **BREAKING:** `CARD_SUITS` — `tuple(Suit)` is the same tuple now. `CONTRACT_SUITS` is the new constant for the wider "anything a contract can name" set.

### Fixed

- (core) No-trump rounds score on their own card-point table. `NoTrumpRules` reused the plain off-trump scale, so a no-trump deck held 120 card points instead of 152 and contracts above 130 were unmakeable. The ace is now worth 19 at no trump.
- (core) An all-trump contract raises instead of quietly playing out as a no-trump one. The variant is still unimplemented; it just no longer answers "no card is trump" when the right answer is "every card is".
- (core) Suits render as their display name wherever one is embedded in text — a contract reads `100 Spades by North` instead of `100 Suit.SPADES by North`.
- (engine) The in-game "Last trick" panel is numbered with the trick it actually shows; at the trick-won pause it read one too high.
- (engine) The AI no longer hands the opponents a Ten it did not have to — it discards its cheapest card instead of emptying its shortest suit. Over 300 rounds that concedes 36% fewer points on those discards.
- (engine) The AI no longer spends a trump on a trick a plain card would have taken, keeping trump back when both opponents are proven void. The inference reaches defenders too, not just the declaring side.
- (engine) Under a no-trump contract the AI no longer records seats as "void in No Trump" — a suit no card can be held in — so its void map stays clean.
- (engine) Doubled Slam-family rounds mark the right amount. Only the announced half takes the multiplier — the flat substitute that stands in for the trick pile does not — so a doubled Slam is 750 and a redoubled one 1250, against 1000 and 2000 before; Solo Slam reads 1500 and 2500 instead of 2000 and 4000.
- (engine) A declarer who sweeps all eight tricks without announcing anything now marks the 500 substitute of the Solo Slam that was there for the taking. Only a sweep split with the partner marks 250, where both did before.

## [0.2.0] - 2026-07-25

Hardening release: the play phase moves into the core as an immutable `PlayState` state machine with an imperfect-information `PlayObservation` for AI strategies, the expert AI bids and plays from sounder rules, and the whole TUI took a review pass.

### Added

- (core) `PlayState` play-phase state machine, the play-side sibling of `Auction`: an
  immutable, trick-by-trick state that owns the follow/trump legality rules, enforces turn order and the new `OUT_OF_TURN`/`CARD_NOT_IN_HAND` violation reasons through `apply`, and can be forked onto replacement hands via `with_hands` for future search-based AIs.
- (core) `PlayObservation` imperfect-information view: the projection of a `PlayState` that a single player is allowed to see (own hand, public trick history, legal cards), the input surface for AI card-play strategies.
- (engine) The expert AI's card play now reasons from sound card tracking: fallen cards and per-seat voids (trump and plain suits alike) are derived from the public trick history, with careful inference — any failure to follow records a void, but a seat discarding behind its master partner is not read as trump-void. On that foundation the AI stops pulling trumps once both opponents are known void in trump (remaining unseen trumps can only sit in partner's hand), preserves master cards behind a winning partner by giving the next-highest card instead(partner's Ace promotes its Ten to suit master — the Ten is kept for a later trick, trump-led tricks included), and anticipates opponent ruffs — when an opponent still to play is proven void in the led suit and may still hold trump, it stops piling points behind a winning partner and contests a losing trick with the smallest card that beats the current best rather than the fattest.

### Changed

- (engine) Card-play strategies now receive a single frozen `PlayObservation` — the seat's own hand, the public trick history, and its legal cards — and derive any card  tracking from it, so a strategy is sealed off from another seat's hand by construction through what it is handed; sealing the live player refs on `Play` is a noted follow-up.
- (engine) The trick loop is now driven by the core `PlayState`: `Round` seeds an  immutable play-phase state at the start of play, derives the legal cards from it, and mirrors the players' hands and the current trick from it each play; card-play legality moved to `contrai-core` wholesale. An absent or illegal card now raises  `IllegalPlayError` (`CARD_NOT_IN_HAND`) instead of being silently skipped.
- (engine) Unify the internal bid representation on core `Bid`/`Auction` objects and  remove the legacy wire-format bridge — the rule-based AI and the bidding view now operate directly on typed `Bid` objects. Behaviour is unchanged.
- (engine) The expert AI now resolves its best (contract, suit) pair once per bidding turn — the duplicated best-contract scans in the overbid check and the initial-bid builder are folded into a single helper that also owns the suit tie-break.

### Removed

- (engine) The `initialize_card_tracking` / `update_card_tracking` hooks on  `CardPlayStrategy` / `AiPlayer` (public since 0.1.0) — card tracking is now derived from the observation rather than pushed to per-strategy state each play.

### Fixed

- (engine) Expert AI partner-support escalation: every supporting turn re-added the seat's full complement (+10 per external ace, +10 for the trump complement) on top of the *standing* contract, so partners alternately raised each other far past their combined strength (an 80 opening ratcheting to 160+, at which point the opponents' double heuristic armed itself on the inflated value), and a seat could even "support" its own bid after an opponent overbid. Support is now capped at a team ceiling — partner's opening bid plus the supporter's complement, announced once — and a seat never supports a suit it opened itself.
- (engine) Landing screen now labels the three AI seats `AI · expert` instead of `AI · medium` — the bots play the expert strategy, which is the only level wired today.
- (engine) Expert AI suit tie-break: with several suits tied for the best contract, the AI always fell back to Spades — even when Spades never met the bidding table — and ignored the belote preference when more than one tied suit held a belote. It now picks among the tied suits only, preferring belote holders.
- (engine) The "Unrecognized bid" notice now suggests the cheapest raise the auction still allows (e.g. `'100 h'` once 90 stands) instead of a fixed `'80 h'`, dropping the numeric example entirely once only Slam-family raises remain.
- (engine) A game no longer ends without a winner when both teams finish a round level at or above the target score: the tie is sudden death — tiebreaker rounds are dealt until one team leads, the round recap announcing each one — so the game-over banner always names a winning team.
- (core) Illegal-play errors now name the acting seat: `PlayState.apply` attaches a `<position> card play` context to every `IllegalPlayError`, so rejection diagnostics say who misplayed as well as which card and why.

## [0.1.0] - 2026-06-21

First playable release: a complete CLI Contrée engine backed by a shared domain model, plus the standalone hand analyzer and the spectator-mode scraper.

### Added

- (core) Shared domain model and the single source of truth for game types: `Suit`/`Rank` enums with point values, frozen value-object `Card`, `Deck`, `Hand` query API, `Team`, `BasePlayer`, the `Bid` hierarchy, `Contract`, `Trick` (with `get_current_winner`), the typed `SlamLevel` enum, and a `ContraiError`-rooted exception hierarchy.
- (engine) Playable CLI game engine — `Player`/`HumanPlayer`/`AiPlayer` over `BasePlayer`, `Game`/`Round` orchestration, an `Auction`-driven bidding flow, the expert `AiPlayer` bidding table (80–160) and card-play strategy (trump coverage, over-trump-when-led, partner-master trump conservation), and round scoring with the Belote/Rebelote bonus.
- (engine) Rich terminal UI — round/trick panels, bidding-history and event-log views, the hand panel, and a round recap split into a factual Outcome table and rolled-up Scoring.
- (analyzer) Streamlit opening-hand strength dashboard built on the suit-agnostic `SuitSlot` abstraction — hypergeometric distribution plots and a bidding truth-table.
- (scraper) Playwright spectator-mode scraper v1 for `app.belote-rebelote.fr`: login, Online → Spectator → Contree → Tournament navigation, seat identification, and `#tour` round polling.

[Unreleased]: https://github.com/valmathieu/ContrAI/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/valmathieu/ContrAI/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/valmathieu/ContrAI/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/valmathieu/ContrAI/releases/tag/v0.1.0
