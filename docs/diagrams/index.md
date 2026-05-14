# Diagrams

Architecture, sequence, class, state, and flow diagrams illustrating ContrAI components.

## Two-tool policy

Per `CLAUDE.md` §5:

- **[PlantUML](https://plantuml.com/)** — *only* for **sequence** and **class** diagrams. Sources are `.puml` files.
- **[Mermaid](https://mermaid.js.org/)** — for **everything else** (component, state, flowchart, ER, Gantt, mindmap, deployment, …). Sources are `.mmd` files.

Color is required — distinguish actors, MVC layers, package boundaries, or hot/cold paths. Each ContrAI package has a stable colour (core blue, engine orange, analyzer green, scraper purple) reused across diagrams.

## Rendering

MkDocs renders both PlantUML and Mermaid **inline at site-build time**:

- PlantUML via the [`plantuml-markdown`](https://pypi.org/project/plantuml-markdown/) extension (`format: svg`, see `mkdocs.yml`). Requires the `plantuml` CLI on PATH (Java jar).
- Mermaid via the [`mkdocs-mermaid2-plugin`](https://pypi.org/project/mkdocs-mermaid2-plugin/) (no CLI dependency).

So `mkdocs serve` or `mkdocs build` is enough — no pre-rendering step.

If you want a standalone PNG (e.g. for a slide deck, the LaTeX report, or offline preview) you can still render manually:

```bash
plantuml -tpng docs/diagrams/file.puml         # → docs/diagrams/file.png
mmdc      -i docs/diagrams/file.mmd -o docs/diagrams/file.png
```

These manual renders are **optional** and not committed by default — the canonical sources are the `.puml` / `.mmd` files.

VS Code: install the *PlantUML* (`jebbs.plantuml`) and *Markdown Preview Mermaid Support* extensions for in-editor previews.

## Conventions

- One source file per diagram with a descriptive name prefixed by kind (`class_*.puml`, `seq_*.puml`, `comp_*.mmd`, …).
- Workspace-wide diagrams live here under `docs/diagrams/`; package-local diagrams sit next to the doc that references them, under `packages/<pkg>/`.
- Reference spec IDs (e.g. `SF-09`) where applicable.
- Keep diagrams traceable to spec or package documentation, and honest about partial state (use the `<<stub>>` / `<<future>>` stereotypes for unimplemented elements).

## Current diagrams

### Class

#### `class_analyzer.puml` — analyzer stack

```plantuml format="svg" source="class_analyzer.puml"
```

The probability + bidding stack inside `contrai-analyzer`. Deliberately decoupled from `contrai-core` — `SuitSlot` is a suit-agnostic abstraction for combinatorial math, not a duplicate of core's `Suit`.

### Sequence

#### `seq_scraper.puml` — scraper observation flow

```plantuml format="svg" source="seq_scraper.puml"
```

The Playwright spectator-mode flow currently implemented in `packages/contrai-scraper/main.py`: login → mode navigation → table discovery → poll `#tour` for round changes. `FUTURE LOGIC` (bidding/play observation, SQLite persistence) appears as dashed `<<future>>` calls grouped at `main.py:105-108`.

## Roadmap

Phase 2 (deferred until this Phase 1 smoke test is validated): `class_core.puml`, `class_engine.puml`, `class_workspace.puml`, `seq_round.puml`, `seq_bidding.puml`, `seq_trick.puml`. See the plan file for scope.
