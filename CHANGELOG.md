# Changelog

All notable changes to the ContrAI workspace are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
All four workspace packages (`contrai-core`, `contrai-engine`, `contrai-analyzer`,
`contrai-scraper`) are versioned in lockstep — a single version covers the whole workspace.

## [Unreleased]

### Added

- (core) `PlayState` play-phase state machine, the play-side sibling of `Auction`: an
  immutable, trick-by-trick state that owns the follow/trump legality rules, enforces turn
  order and the new `OUT_OF_TURN`/`CARD_NOT_IN_HAND` violation reasons through `apply`, and
  can be forked onto replacement hands via `with_hands` for future search-based AIs.
- (core) `PlayObservation` imperfect-information view: the projection of a `PlayState` that
  a single player is allowed to see (own hand, public trick history, legal cards), the
  input surface for AI card-play strategies.
- (engine) The expert AI reasons from sound card tracking: it derives which cards have
  fallen and which seats are void in trump from the public trick history, so its
  mid-round leads, master-card detection and trump accounting operate on real data. The
  trump-void inference is careful — a seat discarding behind its master partner is not
  read as void.
- (engine) The expert AI stops pulling trumps once both opponents are known void in
  trump (inferred from plays where they were compelled to trump but couldn't), even
  while unseen trumps remain — those can only sit in partner's hand.
- (engine) The expert AI preserves master cards when following suit behind a winning
  partner: it gives the next-highest card instead (partner's Ace promotes its Ten to
  suit master — the Ten is kept to win a later trick), in trump-led tricks included.

### Changed

- (engine) Card-play strategies now receive a single frozen `PlayObservation` — the
  seat's own hand, the public trick history, and its legal cards — and derive any card
  tracking from it, so a strategy is sealed off from another seat's hand by construction
  through what it is handed; sealing the live player refs on `Play` is a noted follow-up.
- (engine) The trick loop is now driven by the core `PlayState`: `Round` seeds an
  immutable play-phase state at the start of play, derives the legal cards from it, and
  mirrors the players' hands and the current trick from it each play; card-play legality
  moved to `contrai-core` wholesale. An absent or illegal card now raises
  `IllegalPlayError` (`CARD_NOT_IN_HAND`) instead of being silently skipped.
- (engine) Unify the internal bid representation on core `Bid`/`Auction` objects and
  remove the legacy wire-format bridge — the rule-based AI and the bidding view now
  operate directly on typed `Bid` objects. Behaviour is unchanged.
- (engine) The expert AI now resolves its best (contract, suit) pair once per bidding
  turn — the duplicated best-contract scans in the overbid check and the initial-bid
  builder are folded into a single helper that also owns the suit tie-break.

### Removed

- (engine) The `initialize_card_tracking` / `update_card_tracking` hooks on
  `CardPlayStrategy` / `AiPlayer` (public since 0.1.0) — card tracking is now derived
  from the observation rather than pushed to per-strategy state each play.

### Fixed

- (engine) Expert AI partner-support escalation: every supporting turn re-added the seat's
  full complement (+10 per external ace, +10 for the trump complement) on top of the
  *standing* contract, so partners alternately raised each other far past their combined
  strength (an 80 opening ratcheting to 160+, at which point the opponents' double
  heuristic armed itself on the inflated value), and a seat could even "support" its own
  bid after an opponent overbid. Support is now capped at a team ceiling — partner's
  opening bid plus the supporter's complement, announced once — and a seat never supports
  a suit it opened itself.
- (engine) Landing screen now labels the three AI seats `AI · expert` instead of
  `AI · medium` — the bots play the expert strategy, which is the only level wired today.
- (engine) Expert AI suit tie-break: with several suits tied for the best contract, the
  AI always fell back to Spades — even when Spades never met the bidding table — and
  ignored the belote preference when more than one tied suit held a belote. It now picks
  among the tied suits only, preferring belote holders.
- (engine) The "Unrecognized bid" notice now suggests the cheapest raise the auction
  still allows (e.g. `'100 h'` once 90 stands) instead of a fixed `'80 h'`, dropping the
  numeric example entirely once only Slam-family raises remain.

## [0.1.0] - 2026-06-21

First playable release: a complete CLI Contrée engine backed by a shared domain model,
plus the standalone hand analyzer and the spectator-mode scraper.

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

[Unreleased]: https://github.com/valmathieu/ContrAI/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/valmathieu/ContrAI/releases/tag/v0.1.0
