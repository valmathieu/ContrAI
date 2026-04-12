# ContrAI

AI research project studying the French card game *Coinche* (a.k.a. *Contrée*).

## Layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with four packages under `packages/`:

| Package | Description |
|---|---|
| `contrai-core` | Shared domain model — `Card`, `Deck`, `Hand`, `Bid`, `Contract`, `Trick`, `Round`. *Populated in phase 2.* |
| `contrai-engine` | Game engine: model layer, AI players, CLI controller. |
| `contrai-analyzer` | Streamlit dashboard for hand-strength analysis. |
| `contrai-scraper` | Playwright spectator-mode scraper for online games. |

## Setup

Requires **Python 3.14**. Dependency management via [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                            # install all workspace deps
uv run --package contrai-engine main.py            # run the engine CLI
uv run --package contrai-analyzer streamlit run main.py
```

## Documentation

See [`docs/`](docs/) for architecture overview, per-package documentation, and PlantUML diagrams.
