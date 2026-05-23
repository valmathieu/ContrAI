# Diagrams

Architecture, sequence, class, state, and flow diagrams illustrating ContrAI components.

Per-package diagrams live next to the package they describe; this page is the conventions hub and catalogue.

## Two-tool policy

- **[PlantUML](https://plantuml.com/)** — *only* for **sequence** and **class** diagrams. Sources are `.puml` files.
- **[Mermaid](https://mermaid.js.org/)** — for **everything else** (component, state, flowchart, ER, Gantt, mindmap, deployment, …). Sources are `.mmd` files.

## Colour convention

Colour encodes **which package owns the element**, reused consistently across every diagram. Light backgrounds keep things printable/report-friendly.

| Package              | Header fill | Body fill   | Border    |
|----------------------|-------------|-------------|-----------|
| `contrai-core`       | `#7AAEE3`   | `#E1F0FF`   | `#3D6FA5` |
| `contrai-engine`     | `#E89A4F`   | `#FFEFD9`   | `#B26A28` |
| `contrai-analyzer`   | `#7AC178`   | `#E8F5E9`   | `#3F8C3D` |
| `contrai-scraper`    | `#9B7FCC`   | `#EDE7F6`   | `#5E4495` |
| Stub / unimplemented | `#9E9E9E`   | `#EEEEEE`   | `#616161` |
| `<<future>>`         | greyed      | greyed      | dashed    |

Stubbed elements (e.g. the engine's `GameController` / `CliView` today) use the grey palette plus a `<<stub>>` stereotype. Planned-but-unwired elements (e.g. SQLite persistence in the scraper) use dashed arrows and the `<<future>>` stereotype.

## Rendering

MkDocs renders both PlantUML and Mermaid **inline at site-build time**:

- PlantUML via the [`plantuml-markdown`](https://pypi.org/project/plantuml-markdown/) extension (`format: svg`, `base_dir: docs/diagrams`). Requires the `plantuml` CLI on PATH (Java jar).
- Mermaid via the [`mkdocs-mermaid2-plugin`](https://pypi.org/project/mkdocs-mermaid2-plugin/) (no CLI dependency).

So `mkdocs serve` / `mkdocs build` is enough — no pre-rendering step.

A **rendered PNG is committed alongside each `.puml`** in `docs/diagrams/` so the diagrams are previewable offline (in a file browser, an IDE, slides, the LaTeX report) without spinning up `mkdocs serve`. The MkDocs site itself does not read those PNGs — it re-renders from the `.puml` source — so the canonical source of truth is still the `.puml` file. Re-render the PNG whenever the source changes and commit both together in the same atomic commit:

```bash
plantuml -tpng docs/diagrams/file.puml         # → docs/diagrams/file.png
mmdc      -i docs/diagrams/file.mmd -o docs/diagrams/file.png
```

VS Code: install the *PlantUML* (`jebbs.plantuml`) and *Markdown Preview Mermaid Support* extensions for in-editor previews.

## Conventions

- **Source location:** all `.puml` / `.mmd` sources live in `docs/diagrams/`, even when the rendered diagram is embedded on a per-package page. The `plantuml_markdown` extension's `base_dir` lets per-package pages embed by bare filename (e.g. `source="class_analyzer.puml"`).
- **Embed location:** per-package diagrams are embedded on that package's overview page (`docs/{core,engine,analyzer,scraper}/index.md`); workspace-spanning diagrams go on `docs/architecture.md`. This catalogue page links to each.
- **Naming:** kind-prefixed filenames — `class_*.puml`, `seq_*.puml`, `comp_*.mmd`, `state_*.mmd`, …
- **Honest portrayal:** mark unimplemented elements with `<<stub>>` / `<<future>>` stereotypes plus the grey/dashed styling above. The diagram should describe what the code *is*, not what we wish it were.
- **Traceability:** reference spec IDs (e.g. `SF-09`) where applicable.

## Catalogue

Each row links to the canonical `.puml` source, the rendered `.png` preview, and the topical page where the diagram is embedded.

| Diagram                | Kind     | Scope                | Source                                | PNG preview                          | Embedded on                                                |
|------------------------|----------|----------------------|---------------------------------------|--------------------------------------|------------------------------------------------------------|
| `class_core.puml`      | Class    | contrai-core         | [source](class_core.puml)             | [png](class_core.png)                | [Core overview](../core/#class-structure)                  |
| `class_engine.puml`    | Class    | contrai-engine + MVC | [source](class_engine.puml)           | [png](class_engine.png)              | [Engine overview](../engine/#class-structure)              |
| `class_analyzer.puml`  | Class    | contrai-analyzer     | [source](class_analyzer.puml)         | [png](class_analyzer.png)            | [Analyzer overview](../analyzer/#class-structure)          |
| `class_workspace.puml` | Class    | Workspace overview   | [source](class_workspace.puml)        | [png](class_workspace.png)           | [Architecture](../architecture/#package-map)               |
| `seq_round.puml`       | Sequence | Engine round flow    | [source](seq_round.puml)              | [png](seq_round.png)                 | [Engine — round lifecycle](../engine/#round-lifecycle)     |
| `seq_bidding.puml`     | Sequence | Bidding cycle zoom   | [source](seq_bidding.puml)            | [png](seq_bidding.png)               | [Engine — bidding cycle zoom](../engine/#round-lifecycle)  |
| `seq_trick.puml`       | Sequence | Single trick zoom    | [source](seq_trick.puml)              | [png](seq_trick.png)                 | [Engine — single trick zoom](../engine/#round-lifecycle)   |
| `seq_scraper.puml`     | Sequence | contrai-scraper      | [source](seq_scraper.puml)            | [png](seq_scraper.png)               | [Scraper overview](../scraper/#current-flow-v1)            |
| `state_cli_screens.mmd`| State    | RichView screen flow | [source](state_cli_screens.mmd)       | [png](state_cli_screens.png)         | [Engine — CLI](../engine/#cli)                             |
