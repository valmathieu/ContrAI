# ContrAI

AI research project studying the French card game *Coinche* (a.k.a. *Contrée*).

## Layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with four packages under `packages/`:

| Package            | Description                                                                                                                                                                       |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `contrai-core`     | Shared domain model — `Suit`/`Rank` enums, `Card`, `Deck`, `Hand`, `Team`, `BasePlayer`, the `Bid` hierarchy (`PassBid`/`ContractBid`/`DoubleBid`/`RedoubleBid`), `Contract`, `Trick`, and exceptions. |
| `contrai-engine`   | Game engine on top of `contrai-core`: `Player`/`HumanPlayer`/`AiPlayer`, `Game` / `Round` orchestration, CLI controller and view.                                                 |
| `contrai-analyzer` | Streamlit dashboard for opening-hand strength analysis. Independent of `contrai-core` by design (uses its own `SuitSlot` abstraction).                                            |
| `contrai-scraper`  | Playwright spectator-mode scraper for online games on `app.belote-rebelote.fr`.                                                                                                   |

## Setup

Requires **Python 3.14**. Dependency management via [uv](https://docs.astral.sh/uv/).

The workspace `pyproject.toml` is virtual (no top-level project), so after `uv sync` the workspace members must be editable-installed explicitly:

```bash
uv sync
uv pip install -e packages/contrai-core -e packages/contrai-engine -e packages/contrai-analyzer -e packages/contrai-scraper

uv run --package contrai-engine main.py            # run the engine CLI
uv run --package contrai-analyzer streamlit run main.py
```

## Documentation

See [`docs/`](docs/) for the architecture overview, per-package documentation, and diagram sources (PlantUML for sequence/class diagrams, Mermaid for everything else).

## Docs site

The narrative docs and per-package API reference are published with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/).

```bash
uv run mkdocs serve   # live preview at http://127.0.0.1:8000
uv run mkdocs build   # render the static site into site/ (gitignored)
```
