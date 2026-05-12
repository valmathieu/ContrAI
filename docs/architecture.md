# Architecture

Overview of how the four ContrAI packages fit together.

## Workspace layout

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with four members under `packages/`:

- **`contrai-core`** — shared domain model. Owns `Suit`/`Rank`/`CARD_SUITS`, `Card`, `Deck`, `Hand`, `Team`, `BasePlayer`, the `Bid` hierarchy + `BidValidator`, `Contract`, `Trick`, and the model-level exceptions. Pure data and invariants, no orchestration.
- **`contrai-engine`** — game engine on top of `contrai-core`. Extends `BasePlayer` with `Player` / `HumanPlayer` / `AiPlayer`, owns `Game` and `Round` orchestration, and the CLI (controller / view layers).
- **`contrai-analyzer`** — Streamlit dashboard for opening-hand strength (hypergeometric distribution + bidding truth-table). Deliberately independent of `contrai-core`; see [`packages/analyzer.md`](packages/analyzer.md) for the rationale behind the `SuitSlot` abstraction.
- **`contrai-scraper`** — Playwright spectator-mode scraper for `app.belote-rebelote.fr`. v1 ships login + table navigation + per-round polling; bidding/play observation and persistence are still to be wired up.

## Shared types

`contrai-core`'s public API (everything re-exported from `contrai_core/__init__.py`):

```
Suit, Rank, CARD_SUITS,
Card, Deck, Hand,
Team, BasePlayer,
Bid, PassBid, ContractBid, DoubleBid, RedoubleBid, BidValidator,
Contract, Trick,
InvalidPlayerCountError, InvalidCardCountError
```

Consumers import these directly (`from contrai_core import Card, Suit, …`); the engine no longer re-exports them.

## Dependency direction

```
contrai-core
   ↑
   ├── contrai-engine        (direct dependency)
   ├── contrai-scraper       (planned — will materialize observed games into core types)
   └── contrai-analyzer      (independent by design — does NOT depend on core)
```

`contrai-engine`, `contrai-analyzer`, and `contrai-scraper` do not depend on each other.

> TODO: dataflow diagrams (live in [`diagrams/`](diagrams/) — PlantUML `.puml` for sequence/class, Mermaid `.mmd` for everything else).
