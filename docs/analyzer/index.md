# contrai-analyzer

Streamlit dashboard for hand-strength analysis (hypergeometric distribution + bidding truth table).

## Layout

- `main.py` — Streamlit UI only, no logic
- `src/contrai_analyzer/models/` — `Card`, `Deck`, `Hand`
- `src/contrai_analyzer/engine/probability_engine.py` — hypergeometric distribution math
- `src/contrai_analyzer/bidding/evaluator.py` — bidding truth-table → suggestion

**Strict UI/logic split.** All math and game logic in `contrai_analyzer`; `main.py` is pure UI glue.

```bash
uv run --package contrai-analyzer streamlit run main.py
```

## Class structure

```plantuml format="svg" source="class_analyzer.puml"
```

The probability + bidding stack is deliberately decoupled from `contrai-core` — `SuitSlot` (TRUMP / BLUE / GREEN / PURPLE) is a suit-agnostic abstraction for the combinatorial math, not a duplicate of core's `Suit` enum: it slots a hand by each suit's *role* relative to trump, where core's `Suit` names the four concrete card suits (and `TrumpVariant` carries the trump options that name none). See [Diagrams](../diagrams/) for the colour convention.

> TODO: bidding truth-table reference; probability formulas.
