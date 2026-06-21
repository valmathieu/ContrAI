# Changelog

All notable changes to the ContrAI workspace are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
All four workspace packages (`contrai-core`, `contrai-engine`, `contrai-analyzer`,
`contrai-scraper`) are versioned in lockstep — a single version covers the whole workspace.

## [Unreleased]

### Fixed

- (engine) Landing screen now labels the three AI seats `AI · expert` instead of
  `AI · medium` — the bots play the expert strategy, which is the only level wired today.

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
