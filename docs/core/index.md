# contrai-core

Shared domain model for the ContrAI workspace — pure data + invariants, no orchestration.

## Module map

Source lives at `packages/contrai-core/src/contrai_core/`:

| Module          | Contents                                                                       |
| --------------- | ------------------------------------------------------------------------------ |
| `types.py`      | `Suit`, `Rank` enums and the `CARD_SUITS` tuple                                |
| `card.py`       | `Card`                                                                         |
| `deck.py`       | `Deck`                                                                         |
| `hand.py`       | `Hand` (list-compatible API including `copy()` + query helpers)                |
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

The full domain model in one view. Two trick-winner methods sit on `Trick`: `get_winner()` (uses the trump bound at construction time) and `get_current_winner(trump_suit)` (takes trump as an argument and works on partial tricks). The engine constructs `Trick()` without binding trump — the authoritative trump suit lives on the contract — and consumes `get_current_winner` for the partner-master legality check and the view's live winner highlight. `ContractBid.is_valid_after` enforces the *auction freezes after a Double* rule from `contree-domain.md §5.3` — a Double or Redouble between the previous ContractBid and now rejects the new ContractBid regardless of its value. Note `Contract.get_defending_team()` is annotated as a TODO — today it returns `None` and is meant to be wired up at the game level. See [Diagrams](../diagrams/) for the colour convention.

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

Coverage is now complete across every module:
`test_types.py`, `test_card.py`, `test_deck.py`, `test_hand.py`, `test_team.py`, `test_base_player.py`, `test_bid.py`, `test_contract.py`, `test_trick.py`, `test_exceptions.py`.

The earlier `CLAUDE.md` §10 backfill note (Bid / Contract / Trick) is closed. The remaining engine-side gap is `Round` — see [`engine/index.md`](../engine/index.md#open-work).
