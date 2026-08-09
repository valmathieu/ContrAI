# Changelog

All notable changes to the ContrAI workspace are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
All four workspace packages (`contrai-core`, `contrai-engine`, `contrai-analyzer`,
`contrai-scraper`) are versioned in lockstep — a single version covers the whole workspace.

## [Unreleased]

### Added

- (core) `PlayState.card_points_by_side` and `PlayState.trick_counts_by_side` — the captured pile per side, derived from the play history like every other view on the state. Each completed trick is credited to the side of the seat that won it, scored through the contract's `TrumpRules`, so "how many points has each team taken so far" is answered in one place instead of being re-summed at every consumer. Both return every `TeamSide` member as a key, so callers index directly. The raw pile only: the last-trick and Belote bonuses are contract-conversion rules that stay with whoever scores the round, which is why a completed round sums to the deck's 152 rather than 162.
- (core) `card_queries.py` — `count_suit` / `cards_of_suit` / `has_suit` / `has_card` over any collection of cards. The same four suit questions are asked from two sides of the model that carry cards in different containers: `Hand`, the mutable list a seat plays out of, and the frozen `tuple[Card, ...]` of `PlayState.hands` / `PlayObservation.hand`, whose immutability is what makes a determinization fork safe to hand to a search. Neither can replace the other, so the queries now live on their own and both read through one implementation — `Hand`'s methods are one-line delegates, and card-play code working on an observation no longer has to re-implement the comprehensions ad hoc. `has_card` asks through membership, so `Card` value-equality stays the single source of truth for "do I hold this".
- (core) **BREAKING:** Position seat enum — anticlockwise next, partner, opponents, strict parsing, French seat-name mapping (french_name/from_french). BasePlayer.position is now a Position, not a free-form string.
- (core) `is_trump(card_suit, contract_suit)` and `trump_suits(contract_suit)` — the one place that answers "is this card trump under this contract", plus `Card.is_trump(trump_suit)` sugar. Every trick-taking rule now asks through them instead of spelling out `card.suit == trump_suit` at each boundary.
- (core) `TrumpRules` trick-rules seam: one sealed rules object per contract regime (`SingleSuitRules` per suit, `NoTrumpRules` for no-trump and no-contract), resolved as shared singletons via `rules_for(contract_suit)`. It answers trumpness, per-card points, in-suit ranking, led-suit-aware trick competition (`trick_rank`), belote applicability, and the higher-ranks ladder — so a future contract variant is a new leaf plus its tables instead of a sweep over call sites. All-trump still raises `NotImplementedError`, now from `rules_for`.
- (core) `TrickRecord`, the completed-trick value: an immutable four-record tuple that knows its `led_suit` and its `winner(trump_suit)` — returning the winning play *record* (a `Play` in state contexts, a sealed `ObservedPlay` in observations), never just the seat.
- (engine) `contrai --debug / --seed / --autoplay` — face-up debug view with still-in-play summary, stdlib-logging diagnostics to `contrai-debug.log` with recorded seed, reproducible deals, and unattended 4-AI autoplay.
- (core) `Position.is_teammate(other)` — the same-side question as a boolean, derived from `partner` so it can never disagree with the `partner` / `opponents` pairing. A seat is its own teammate, so "did my side declare this?" needs no extra self-check at the call site.
- (core) `TeamSide` enum (`NS` / `EW`) and `Position.team_side` — team identity as a value instead of a free-form name string. It answers *which side*, the question a score-dictionary key, a persisted game record or a training label asks, where `Position.is_teammate` only answers *same side*; `is_teammate` is now derived from it, so the two can never disagree. Like `Position`, it is not a `StrEnum`: `TeamSide.NS == "North-South"` is `False`, so a leftover name-keyed lookup misses loudly rather than resolving by accident.
- (core, engine) A game can be built from an unseated roster. `BasePlayer`'s `position` is now optional, and `Game([p1, p2, p3, p4])` seats four positionless players in list order against `list(Position)` — the anticlockwise turn order, so the first and third players end up partners, not the first and second. Every caller previously had to pre-assign all four seats, which a simulation or training harness has no reason to care about. Pre-seated lists keep exactly today's validation, and a half-seated list is refused rather than completed, since filling the gaps would decide partnerships the caller only specified half of. Randomised seating stays a caller-side `shuffle` of the list, keeping the RNG and its seeding where a harness reseeding per game — or a test pinning a fixed table — can control it.
- (core) `seal_bid(bid)` — projects a bid onto its bidder's seat, replacing the live player with a bare `Position`. The `Bid` hierarchy is now generic over whoever made the bid, so an auction holds `Bid[BasePlayer]` and an observation `Bid[Position]` without a parallel set of variant classes: the concrete variant survives the projection, and because the bidder is excluded from bid equality, so does equality.

### Changed

- (analyzer) **BREAKING:** The probability and bidding stack moved from a bare `src/` tree to an importable `contrai_analyzer` package, and its modules import `contrai_analyzer.models` / `.engine` / `.bidding` instead of `src.*`. The old imports resolved only when the working directory happened to be the analyzer package root — `src` was acting as an implicit namespace package — while the installed distribution published `bidding`, `engine` and `models` as top-level names that nothing imported and that `engine` in particular was liable to collide with. The dashboard's behaviour is unchanged; `contrai_analyzer` now re-exports `Card`, `Rank`, `SuitSlot`, `Hand`, `ProbabilityEngine` and `BiddingEvaluator`, and the docs site carries a real API reference for it. Still deliberately independent of `contrai-core` — `SuitSlot` is untouched.
- (scraper) **BREAKING:** The scraper is now an importable `contrai_scraper` package under `src/`, launched with the new `contrai-scrape` console script instead of `python main.py`. The v1 flow is split into `session` (login → Online → Spectator → Contrée → tournament-table hunt), `observer` (player identification and round polling), `config` (target URL and account credentials) and `cli`. Previously the whole flow sat in a root-level `main.py` and the installed distribution exposed no importable module at all, so nothing downstream — persistence, notebooks, or a future multi-table orchestrator — could reach the scraping logic without re-implementing it. Also drops an unused `prompt_toolkit` import that was resolving only transitively through `jupyter`.
- (engine) **BREAKING:** Every team score is now keyed by `TeamSide` instead of the team's name string. `RoundScore.scores`, `Round.round_scores`, `Round.team_tricks`, `Game.scores` and `GameOverStatus.winner` / `tied_teams` / `final_scores` all speak the enum, so a winner can be looked straight up in the final scores and a persisted or logged score survives any rewording of the display labels. `"North-South"` / `"East-West"` remain only as `Team.name` and as the view's `N-S` / `E-W` scoreboard mapping. Which side won a trick, holds Belote, or declared the contract is now read off the seat (`Position.team_side`) rather than the mutable `Team` roster, so scoring no longer silently drops a trick whose winner has no team wired up. Pure retyping: autoplay transcripts are identical across six seeds.
- (core) **BREAKING:** `PlayObservation`'s trick records are now sealed: `completed_tricks` / `current_trick` carry the new `ObservedPlay` `(position, card)` pairs instead of live-player `Play` records, and `current_winner` reports the winning `Position` — closing the `play.player.hand` path through which a card-play strategy could technically read another seat's cards. The expert AI's card tracking keys its void map by seat `Position` accordingly.
- (core) **BREAKING:** The rest of `PlayObservation` is sealed too, so nothing reachable from an observation by *any* object path is a live player. The observer is now `observation.position` (a `Position`, replacing the `player` field), `contract` is an `ObservedContract` naming its declarer and doublers by seat, and `bids` holds `Bid[Position]` records. Previously an observation still reached every seat's current hand through `contract.player`, `contract.team.players`, `player.team.players` and — with a full auction attached — `bids[i].player`, which would have let a learned policy read the hidden state it is supposed to infer. `PlayState.observe(player, bids=…)` is unchanged: it still takes the live player, since it needs that identity to look up the hand and legal actions; only its output is sealed. AI card play reads the declaring side via `Position.is_teammate(contract.declarer)`, with no change in behaviour — identical autoplay transcripts across six seeds.
- (engine) **BREAKING:** Seating, dealer/turn rotation, team formation, and the AI's partner reasoning now flow through core Position — the seat-order list, hardcoded partner map, and position-string scans are gone, and the terminal UI keys its seat labels, diamond slots, and belote badges on Position.
- (core) **BREAKING:** Card suits and contract trumps are now two types. `Suit` has exactly the four card-bearing members, the two options that name no suit move to the new `TrumpVariant` enum, and `ContractSuit` is the union of the two — so `Card.suit` can no longer hold something no card carries, and `tuple(Suit)` is safe to iterate anywhere a real suit is meant. `Card` rejects a non-`Suit` at construction with the new `InvalidCardError`.
- (core) **BREAKING:** All-trump is no longer bookable. `ContractBid.VALID_SUITS` drops it, so the auction offers 65 opening contracts instead of 78 and a search-based agent sampling `legal_actions` can no longer reach a contract the engine cannot play.
- (core) `PlayState.completed_tricks` and `PlayObservation.completed_tricks` now hold `TrickRecord` values instead of bare four-play tuples. `TrickRecord` is a `tuple` subclass, so every consumer that iterates, unpacks, or compares a completed trick as a plain sequence keeps working unchanged.
- (engine) Round scoring now reads the authoritative core play state: card points come from each completed trick's pile credited to its winner's team, and trick counts plus the last-trick bonus come from the state's derived winners — never from the view-facing mirror lists. A malformed trick history now fails loudly instead of silently scoring 0–0.
- (core) **BREAKING:** `Card` is pure identity — the call-time trump API is retired. `Card.is_trump(trump_suit)`, `Card.get_points(trump_suit)`, `Card.get_order(trump_suit)` and the four class-level point/order tables are gone; every trumpness, scoring, and ranking question is answered by the contract's `TrumpRules` object from `rules_for(contract_suit)` (the tables now live in `contrai_core.rules`). The module-level `is_trump(card_suit, contract_suit)` / `trump_suits(contract_suit)` predicates remain as thin delegates over the seam.

### Removed

- (core) **BREAKING:** `Hand.copy()`, `Hand.count_rank()`, `Hand.is_complete()` and `Hand.__getitem__` are gone — no production code in the workspace called any of them; only `test_hand.py` did. `copy()` justified itself by callers that treat a hand as a list, naming the legal-plays computation specifically, but that rationale died when `PlayState.legal_actions` became the legality oracle and moved the play path onto frozen tuples. Dropping `__getitem__` with them makes the remaining shape the intended one: a hand is a bag a seat draws from and plays out of, not a sequence to address by position. A caller wanting a list-shaped or positional view writes `list(hand)` explicitly.

### Fixed

- (engine) The AI no longer hands the opponents a Ten it did not have to. When it can neither follow suit nor usefully ruff, it now gives up its cheapest card outright instead of emptying whichever suit it happened to be shortest in — that ordering walked a suit down to its Ten while a worthless 7 sat untouched in a longer one. Over 300 rounds the change concedes 36% fewer points on those discards and halves the ten-or-better cards thrown away. Ties go to the longest suit, then to a draw, so a seat's discards are no longer readable from hand order.
- (core) Suits now render as their display name wherever one is embedded in text — a contract reads `100 Spades by North` instead of `100 Suit.SPADES by North`.
- (core) An all-trump contract now raises instead of quietly playing out as a no-trump one. The variant is still unimplemented; it just no longer answers "no card is trump" when the right answer is "every card is".
- (engine) Under a no-trump contract the AI no longer records seats as "void in No Trump" — a suit no card can be held in. Its card tracking now leaves the trump entry out of a no-trump round entirely, so the trump-pull and anticipated-ruff inferences read a clean void map instead of a polluted one.
- (engine) The AI no longer spends a trump on a trick a plain card would have taken. Leading a later trick with both opponents proven out of trump, it now searches only plain suits for the ace or master to cash, keeping trump back as the winner nobody can take off it — previously the trump ace went out whenever trump happened to be the longest suit, and a hand of nothing but trump led its ace rather than its cheapest card. The inference also reaches defenders now: holding trump back against trump-void opponents is worth the same on either side of the contract, but the seat only ever asked the question when its own side had declared.

### Removed

- (core) **BREAKING:** `Team.get_partner`, `Team.contains_player`, `Team.total_score` and `Team.add_points`. `Team` is now purely the two-player roster and its display name: the partner and same-side questions are answered from the seating by `Position.partner` / `Position.is_teammate` / `Position.team_side`, and cumulative scoring has always belonged to the engine's `Game.scores`, which never called the team-side accumulator. `str(team)` and `repr(team)` no longer carry a points suffix.
- (core) **BREAKING:** `CARD_SUITS`. With `Suit` down to its four card-bearing members, `tuple(Suit)` is the same tuple — iterate the enum directly. `CONTRACT_SUITS` is the new constant for the wider "anything a contract can name" set.

## [0.2.0] - 2026-07-25

Hardening release: the play phase moves into the core as an immutable `PlayState` state machine with an imperfect-information `PlayObservation` for AI strategies, the expert AI bids and plays from sounder rules, and the whole TUI took a review pass.

### Added

- (core) `PlayState` play-phase state machine, the play-side sibling of `Auction`: an
  immutable, trick-by-trick state that owns the follow/trump legality rules, enforces turn order and the new `OUT_OF_TURN`/`CARD_NOT_IN_HAND` violation reasons through `apply`, and can be forked onto replacement hands via `with_hands` for future search-based AIs.
- (core) `PlayObservation` imperfect-information view: the projection of a `PlayState` that a single player is allowed to see (own hand, public trick history, legal cards), the input surface for AI card-play strategies.
- (engine) The expert AI's card play now reasons from sound card tracking: fallen cards and
  per-seat voids (trump and plain suits alike) are derived from the public trick history, with
  careful inference — any failure to follow records a void, but a seat discarding behind its
  master partner is not read as trump-void. On that foundation the AI stops pulling trumps once
  both opponents are known void in trump (remaining unseen trumps can only sit in partner's
  hand), preserves master cards behind a winning partner by giving the next-highest card instead
  (partner's Ace promotes its Ten to suit master — the Ten is kept for a later trick, trump-led
  tricks included), and anticipates opponent ruffs — when an opponent still to play is proven
  void in the led suit and may still hold trump, it stops piling points behind a winning partner
  and contests a losing trick with the smallest card that beats the current best rather than the
  fattest.

### Changed

- (engine) Card-play strategies now receive a single frozen `PlayObservation` — the
  seat's own hand, the public trick history, and its legal cards — and derive any card
  tracking from it, so a strategy is sealed off from another seat's hand by construction through what it is handed; sealing the live player refs on `Play` is a noted follow-up.
- (engine) The trick loop is now driven by the core `PlayState`: `Round` seeds an
  immutable play-phase state at the start of play, derives the legal cards from it, and
  mirrors the players' hands and the current trick from it each play; card-play legality
  moved to `contrai-core` wholesale. An absent or illegal card now raises
  `IllegalPlayError` (`CARD_NOT_IN_HAND`) instead of being silently skipped.
- (engine) Unify the internal bid representation on core `Bid`/`Auction` objects and
  remove the legacy wire-format bridge — the rule-based AI and the bidding view now operate directly on typed `Bid` objects. Behaviour is unchanged.
- (engine) The expert AI now resolves its best (contract, suit) pair once per bidding
  turn — the duplicated best-contract scans in the overbid check and the initial-bid
  builder are folded into a single helper that also owns the suit tie-break.

### Removed

- (engine) The `initialize_card_tracking` / `update_card_tracking` hooks on
  `CardPlayStrategy` / `AiPlayer` (public since 0.1.0) — card tracking is now derived
  from the observation rather than pushed to per-strategy state each play.

### Fixed

- (engine) Expert AI partner-support escalation: every supporting turn re-added the seat's full complement (+10 per external ace, +10 for the trump complement) on top of the *standing* contract, so partners alternately raised each other far past their combined strength (an 80 opening ratcheting to 160+, at which point the opponents' double heuristic armed itself on the inflated value), and a seat could even "support" its own bid after an opponent overbid. Support is now capped at a team ceiling — partner's opening bid plus the supporter's complement, announced once — and a seat never supports a suit it opened itself.
- (engine) Landing screen now labels the three AI seats `AI · expert` instead of `AI · medium` — the bots play the expert strategy, which is the only level wired today.
- (engine) Expert AI suit tie-break: with several suits tied for the best contract, the
  AI always fell back to Spades — even when Spades never met the bidding table — and ignored the belote preference when more than one tied suit held a belote. It now picks among the tied suits only, preferring belote holders.
- (engine) The "Unrecognized bid" notice now suggests the cheapest raise the auction still allows (e.g. `'100 h'` once 90 stands) instead of a fixed `'80 h'`, dropping the numeric example entirely once only Slam-family raises remain.
- (engine) A game no longer ends without a winner when both teams finish a round level at or above the target score: the tie is sudden death — tiebreaker rounds are dealt until one team leads, the round recap announcing each one — so the game-over banner always names a winning team.
- (core) Illegal-play errors now name the acting seat: `PlayState.apply` attaches a `<position> card play` context to every `IllegalPlayError`, so rejection diagnostics say who misplayed as well as which card and why.

## [0.1.0] - 2026-06-21

First playable release: a complete CLI Contrée engine backed by a shared domain model, plus the standalone hand analyzer and the spectator-mode scraper.

### Added

- (core) Shared domain model and the single source of truth for game types: `Suit`/`Rank`
  enums with point values, frozen value-object `Card`, `Deck`, `Hand` query API, `Team`,
  `BasePlayer`, the `Bid` hierarchy, `Contract`, `Trick` (with `get_current_winner`), the
  typed `SlamLevel` enum, and a `ContraiError`-rooted exception hierarchy.
- (engine) Playable CLI game engine — `Player`/`HumanPlayer`/`AiPlayer` over `BasePlayer`,
  `Game`/`Round` orchestration, an `Auction`-driven bidding flow, the expert `AiPlayer`
  bidding table (80–160) and card-play strategy (trump coverage, over-trump-when-led,
  partner-master trump conservation), and round scoring with the Belote/Rebelote bonus.
- (engine) Rich terminal UI — round/trick panels, bidding-history and event-log views,
  the hand panel, and a round recap split into a factual Outcome table and rolled-up Scoring.
- (analyzer) Streamlit opening-hand strength dashboard built on the suit-agnostic `SuitSlot`
  abstraction — hypergeometric distribution plots and a bidding truth-table.
- (scraper) Playwright spectator-mode scraper v1 for `app.belote-rebelote.fr`: login,
  Online → Spectator → Contree → Tournament navigation, seat identification, and `#tour`
  round polling.

[Unreleased]: https://github.com/valmathieu/ContrAI/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/valmathieu/ContrAI/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/valmathieu/ContrAI/releases/tag/v0.1.0
