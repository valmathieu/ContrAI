# contrai-analyzer

Streamlit dashboard for hand-strength analysis (hypergeometric distribution + bidding truth table).

## Layout

- `main.py` — Streamlit UI only, no logic
- `src/models/` — `Card`, `Deck`, `Hand`
- `src/engine/probability_engine.py` — hypergeometric distribution math
- `src/bidding/evaluator.py` — bidding truth-table → suggestion

**Strict UI/logic split.** All math and game logic in `src/`; `main.py` is pure UI glue.

Package-specific conventions live in `packages/contrai-analyzer/CLAUDE.md` (gitignored).

## Class structure

```plantuml format="svg" source="class_analyzer.puml"
```

The probability + bidding stack is deliberately decoupled from `contrai-core` — `SuitSlot` (TRUMP / BLUE / GREEN / PURPLE) is a suit-agnostic abstraction for the combinatorial math, not a duplicate of core's `Suit` enum. See [Diagrams](../diagrams/) for the colour convention.

> TODO: bidding truth-table reference; probability formulas.
