# Diagrams

Architecture, sequence, class, state, and flow diagrams illustrating ContrAI components.

## Two-tool policy

Per [CLAUDE.md §5](../../CLAUDE.md):

- **[PlantUML](https://plantuml.com/)** — *only* for **sequence** and **class** diagrams. Sources are `.puml` files.
- **[Mermaid](https://mermaid.js.org/)** — for **everything else** (component, state, flowchart, ER, Gantt, mindmap, deployment, …). Sources are `.mmd` files.

Color is required — distinguish actors, MVC layers, package boundaries, or hot/cold paths. Avoid pure black-and-white renders.

## Rendering

PlantUML (system CLI / Java jar on PATH):

```bash
plantuml -tpng path/to/diagram.puml         # outputs path/to/diagram.png
```

Mermaid ([`@mermaid-js/mermaid-cli`](https://github.com/mermaid-js/mermaid-cli)):

```bash
mmdc -i path/to/diagram.mmd -o path/to/diagram.png
```

VS Code users can preview both with the *PlantUML* (`jebbs.plantuml`) and *Markdown Preview Mermaid Support* extensions.

## Conventions

- One source file per diagram with a descriptive name (`engine_class.puml`, `scraper_sequence.puml`, `workspace_components.mmd`, …).
- Commit the source (`.puml` / `.mmd`) **and** the rendered `.png` in the same atomic commit. Re-render whenever the source changes.
- Workspace-wide diagrams live here under `docs/diagrams/`; package-local diagrams sit next to the doc that references them, under `packages/<pkg>/`.
- Reference spec IDs (e.g. `SF-09`) where applicable.
- Keep diagrams minimal and traceable to spec or package documentation.

> TODO: baseline diagrams — workspace components (`.mmd`), engine MVC class (`.puml`), engine round-flow sequence (`.puml`), scraper navigation sequence (`.puml`).
