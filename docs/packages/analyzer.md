# contrai-analyzer

Streamlit dashboard for hand-strength analysis (hypergeometric distribution + bidding truth table).

## Layout

- `main.py` — Streamlit UI only, no logic
- `src/models/` — `Card`, `Deck`, `Hand`
- `src/engine/probability_engine.py` — hypergeometric distribution math
- `src/bidding/evaluator.py` — bidding truth-table → suggestion

**Strict UI/logic split.** All math and game logic in `src/`; `main.py` is pure UI glue.

Package-specific conventions live in `packages/contrai-analyzer/CLAUDE.md` (gitignored).

> TODO: bidding truth-table reference; probability formulas.
