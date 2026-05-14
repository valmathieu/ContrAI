# contrai-core

Shared domain model for the ContrAI workspace — pure data + invariants, no orchestration.

## Module map

Source lives at `packages/contrai-core/src/contrai_core/`:

| Module          | Contents                                                                       |
| --------------- | ------------------------------------------------------------------------------ |
| `types.py`      | `Suit`, `Rank` enums and the `CARD_SUITS` tuple                                |
| `card.py`       | `Card`                                                                         |
| `deck.py`       | `Deck`                                                                         |
| `hand.py`       | `Hand` (list-compatible API + query helpers)                                   |
| `team.py`       | `Team`                                                                         |
| `player.py`     | `BasePlayer` (engine `Player` extends it)                                      |
| `bid.py`        | `Bid`, `PassBid`, `ContractBid`, `DoubleBid`, `RedoubleBid`, `BidValidator`    |
| `contract.py`   | `Contract`                                                                     |
| `trick.py`      | `Trick`                                                                        |
| `exceptions.py` | `InvalidPlayerCountError`, `InvalidCardCountError`                             |

Everything above is re-exported from `contrai_core/__init__.py` and is part of the public API.

## Class structure

```plantuml format="svg" source="class_core.puml"
```

The full domain model in one view. Note `Contract.get_defending_team()` is annotated as a TODO — today it returns `None` and is meant to be wired up at the game level. See [Diagrams](../diagrams/) for the colour convention.

## Consumers

- **`contrai-engine`** — direct dependency. Imports core types with `from contrai_core import …` and adds `Player` / `HumanPlayer` / `AiPlayer` / `Game` / `Round` on top.
- **`contrai-scraper`** — planned consumer. Observed games will be materialized into `Card` / `Bid` / `Trick` / … instances before being persisted to SQLite.
- **`contrai-analyzer`** — **does not** depend on core. The analyzer's `SuitSlot` (TRUMP/BLUE/GREEN/PURPLE) is a suit-agnostic abstraction for probability math, intentionally separate from `Suit`. See the [analyzer overview](../analyzer/index.md).

## Conventions

- Type hints everywhere, including private helpers.
- Google-style docstrings on every public class/method/function.
- Didactic comments are welcome — this is a learning project.
- Every Model-layer addition ships with `pytest` tests under `packages/contrai-core/tests/`.

## Tests

Present: `test_card.py`, `test_deck.py`, `test_hand.py`, `test_team.py`, `test_base_player.py`.

> Backfill needed (per `CLAUDE.md` §10): `Bid` / `Contract` / `Trick` shipped without pytest coverage and still need tests.
