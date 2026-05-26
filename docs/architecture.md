# Architecture

Overview of how the four ContrAI packages fit together.

## Workspace layout

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with four members under `packages/`:

- **`contrai-core`** — shared domain model. Owns `Suit`/`Rank`/`CARD_SUITS`, `Card`, `Deck`, `Hand`, `Team`, `BasePlayer`, the frozen `Bid` sum type, the `Auction` state-and-rule oracle, `Contract`, `Trick`, and the model-level exceptions (including `IllegalBidError`). Pure data and invariants, no orchestration.
- **`contrai-engine`** — game engine on top of `contrai-core`. Extends `BasePlayer` with `Player` / `HumanPlayer` / `AiPlayer`, owns `Game` and `Round` orchestration, and ships the Rich-based `contrai` terminal UI (`view/rich_view.py` + `cli.py`). See [Engine — CLI](engine/index.md#cli).
- **`contrai-analyzer`** — Streamlit dashboard for opening-hand strength (hypergeometric distribution + bidding truth-table). Deliberately independent of `contrai-core`; see [`analyzer/index.md`](analyzer/index.md) for the rationale behind the `SuitSlot` abstraction.
- **`contrai-scraper`** — Playwright spectator-mode scraper for online Coinche games. v1 ships login + table navigation + per-round polling; bidding/play observation and persistence are still to be wired up.

## Package map

```plantuml format="svg" source="class_workspace.puml"
```

Headline types per package plus cross-package dependency direction. The engine `<<extends>>` core's `BasePlayer`; the scraper's dashed `<<future>>` arrow to core marks the planned materialization of observed games into `Card` / `Bid` / `Trick` / … instances; the analyzer has no arrow into core by design. The dashed note attached to the engine flags the planned multiplayer web server, which isn't in this repo yet. See [Diagrams](diagrams/) for the colour convention.

## Shared types

`contrai-core`'s public API (everything re-exported from `contrai_core/__init__.py`):

```
Suit, Rank, CARD_SUITS,
Card, Deck, Hand,
Team, BasePlayer,
Bid, PassBid, ContractBid, DoubleBid, RedoubleBid,
Auction,
Contract, Trick,
InvalidPlayerCountError, InvalidCardCountError, IllegalBidError
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

> TODO: dataflow diagrams (live in [`diagrams/`](diagrams/index.md) — PlantUML `.puml` for sequence/class, Mermaid `.mmd` for everything else).
