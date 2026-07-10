# Changelog

All notable changes to the ContrAI workspace are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
All four workspace packages (`contrai-core`, `contrai-engine`, `contrai-analyzer`,
`contrai-scraper`) are versioned in lockstep — a single version covers the whole workspace.

## [Unreleased]

### Added

- (engine) AI card tracking is now live: `Round.play_trick` feeds every played card to
  each AI seat's tracker and `Round.deal_cards` resets the counters at every deal, so
  the expert bot's mid-round leads, master-card detection and trump accounting operate
  on real data — the trackers existed but were never fed by the engine. The trump-void
  inference is sound: a seat discarding behind its master partner is not marked void.

### Changed

- (engine) Unify the internal bid representation on core `Bid`/`Auction` objects and
  remove the legacy wire-format bridge — the rule-based AI and the bidding view now
  operate directly on typed `Bid` objects. Behaviour is unchanged.
- (engine) The expert AI now resolves its best (contract, suit) pair once per bidding
  turn — the duplicated best-contract scans in the overbid check and the initial-bid
  builder are folded into a single helper that also owns the suit tie-break.

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
