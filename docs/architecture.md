# Architecture

Overview of how the five ContrAI packages fit together.

## Workspace layout

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with five members under `packages/`:

- **`contrai-core`** — shared domain model. Owns `Suit`/`TrumpVariant`/`ContractSuit`/`Rank`, `Card`, `Deck`, `Hand` (and the `card_queries` functions it and the play path share), `Team`, `BasePlayer`, the frozen `Bid` sum type, the `Auction` state-and-rule oracle, `Contract`, `Trick`, and the model-level exceptions (a `ContraiError` base, plus `IllegalBidError` / `IllegalPlayError` and friends). Pure data and invariants, no orchestration.
- **`contrai-data`** — the game-record format. One append-only JSONL file per game with a frozen dataclass per event, a strict token codec, an append-and-flush writer, a truncation-tolerant reader and a projection that folds an event stream back into rounds. Depends on `contrai-core` only; the engine and the scraper both depend on it, which is what lets the verifier read what the scraper writes. See [`data/index.md`](data/index.md).
- **`contrai-engine`** — game engine on top of `contrai-core`. Extends `BasePlayer` with `Player` / `HumanPlayer` / `AiPlayer`, owns `Game` and `Round` orchestration, and ships the Rich-based `contrai` terminal UI (the `view/` package — `RichView` orchestrator plus per-screen builders — wired in `cli.py`). See [Engine — CLI](engine/index.md#cli).
- **`contrai-analyzer`** — Streamlit dashboard for opening-hand strength (hypergeometric distribution + bidding truth-table). Deliberately independent of `contrai-core`; see [`analyzer/index.md`](analyzer/index.md) for the rationale behind the `SuitSlot` abstraction.
- **`contrai-scraper`** — Playwright spectator-mode scraper for online Coinche games. v1 ships login + table navigation + per-round polling; bidding/play observation and persistence are still to be wired up.

## Package map

```plantuml format="svg" source="class_workspace.puml"
```

Headline types per package plus cross-package dependency direction. Five packages on three levels: `contrai-core` at the bottom, `contrai-data` on top of it, and the engine and the scraper on top of *that* — the `core ← data ← {engine, scraper}` edge. The engine also `<<extends>>` core's `BasePlayer` directly. The scraper now reaches core only *through* `contrai-data`: it writes observed games in the record format rather than materializing core types itself, which is what lets the verifier read what the scraper wrote without either package depending on the other. The analyzer has no arrow into core by design. The dashed note attached to the engine flags the planned multiplayer web server, which isn't in this repo yet. See [Diagrams](diagrams/) for the colour convention.

## Shared types

`contrai-core`'s public API (everything re-exported from `contrai_core/__init__.py`):

```
Suit, TrumpVariant, ContractSuit,
Rank, CONTRACT_SUITS,
is_trump, trump_suits,
Card, Deck, Hand,
count_suit, cards_of_suit, has_suit, has_card,
Team, BasePlayer,
Bid, PassBid, ContractBid, DoubleBid, RedoubleBid,
Auction,
Contract, Trick,
ContraiError,
InvalidPlayerCountError, InvalidCardCountError,
InvalidCardError,
IllegalBidError, IllegalPlayError, PlayRuleViolation,
TrickStateError, InvalidContractError
```

Consumers import these directly (`from contrai_core import Card, Suit, …`); the engine no longer re-exports them.

## Dependency direction

```
contrai-core
   ↑
   ├── contrai-data          (direct dependency)
   │      ↑
   │      ├── contrai-engine (record / replay / verify)
   │      └── contrai-scraper
   ├── contrai-engine        (direct dependency)
   └── contrai-analyzer      (independent by design — does NOT depend on core)
```

`contrai-engine`, `contrai-analyzer`, and `contrai-scraper` do not depend on each other.

> TODO: dataflow diagrams (live in [`diagrams/`](diagrams/index.md) — PlantUML `.puml` for sequence/class, Mermaid `.mmd` for everything else).
