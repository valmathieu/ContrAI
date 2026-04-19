# Architecture

> Overview of how the four ContrAI packages fit together. To be filled in.

## Workspace layout

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) with four members under `packages/`:

- `contrai-core` — shared domain model (populated in phase 2)
- `contrai-engine` — game engine, AI players, CLI
- `contrai-analyzer` — Streamlit dashboard for hand analysis
- `contrai-scraper` — Playwright spectator-mode scraper

## Dependency direction (target)

```
contrai-core
   ↑
   ├── contrai-engine
   ├── contrai-analyzer
   └── contrai-scraper
```

`contrai-engine`, `contrai-analyzer`, and `contrai-scraper` all depend on `contrai-core` for shared types (`Card`, `Deck`, `Hand`, `Bid`, `Contract`, …). They do not depend on each other.

> TODO: dataflow diagrams (live in [diagrams/](diagrams/)).
