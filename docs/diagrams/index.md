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
| External / stdlib    | `#9E9E9E`   | `#EEEEEE`   | `#616161` |
| `<<future>>`         | greyed      | greyed      | dashed    |

Stubbed elements — code that exists but isn't wired in — use the grey palette plus a `<<stub>>` stereotype (the engine carries none today). Planned-but-unwired elements (e.g. SQLite persistence in the scraper) use dashed arrows and the `<<future>>` stereotype. Types the workspace does not own (Python's `Exception` / `ValueError` in `class_core.puml`) reuse the same grey with a `<<stdlib>>` stereotype.

A **boundary element owned by another package** keeps its owner's body fill inline plus a `<<from …>>` stereotype, so a core type drawn inside an engine diagram still reads blue:

```plantuml
class Trick <<from contrai-core>> #E1F0FF
```

This matters for association targets: PlantUML auto-creates any class named only in a relationship line, and the auto-created box inherits the diagram's default `skinparam class` fill — the owning package's colour, not the type's. Declare such types explicitly.

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

**PlantUML silently clips large PNGs at 4096 px** — no error, no warning, just a
truncated image. `class_core.puml` and `class_engine.puml` both exceed it today,
so render them with the limit raised, then check the result's pixel dimensions (a
side landing at exactly 4096 means it was clipped):

```bash
PLANTUML_LIMIT_SIZE=8192 plantuml -tpng docs/diagrams/class_core.puml
```

Only the raster export is affected; the MkDocs site renders SVG and is not
subject to the limit.

### Writing note text

PlantUML parses note bodies as **Creole**, not Markdown. Backticks and Sphinx
roles such as `:class:` render literally — use `""x""` for inline code.
Emphasis and monospace markers must **open and close on the same line**: a `**`
or `""` left open at a line break is read as a nested-bullet marker (or leaves a
stray delimiter) instead of spanning to the next line. Likewise, `<<label>>`
arrow stereotypes only become guillemets when the `<<…>>` pair contains no `\n`.

VS Code: install the *PlantUML* (`jebbs.plantuml`) and *Markdown Preview Mermaid Support* extensions for in-editor previews.

## Conventions

- **Source location:** all `.puml` / `.mmd` sources live in `docs/diagrams/`, even when the rendered diagram is embedded on a per-package page. The `plantuml_markdown` extension's `base_dir` lets per-package pages embed by bare filename (e.g. `source="class_analyzer.puml"`).
- **Embed location:** per-package diagrams are embedded on that package's overview page (`docs/{core,engine,analyzer,scraper}/index.md`); workspace-spanning diagrams go on `docs/architecture.md`. This catalogue page links to each.
- **Naming:** kind-prefixed filenames — `class_*.puml`, `seq_*.puml`, `comp_*.mmd`, `state_*.mmd`, …
- **Honest portrayal:** mark unimplemented elements with `<<stub>>` / `<<future>>` stereotypes plus the grey/dashed styling above. The diagram should describe what the code *is*, not what we wish it were.
- **Traceability:** reference spec IDs (e.g. `SF-09`) where applicable.
- **Titles:** every diagram opens with `title <package> — <scope> (<qualifier>)`, e.g. `contrai-core — domain model (current state)` or `contrai-engine — single trick zoom (Round.play_trick)`. The qualifier names the entry point for a sequence diagram and the honesty caveat for a class diagram.

## Notation

The diagrams use a consistent shorthand that is deliberately *Python-flavoured* rather than strict UML: a reader who knows the type hints should be able to read a box without a UML refresher. This section is the legend.

### Class-diagram members

Members read as `<visibility> name : Type` — the same order as a Python annotation.

| Notation | Means | Python it stands for |
| -------- | ----- | -------------------- |
| `+ name` | public | `self.name` |
| `- _name` | private | `self._name` |
| `Type?` | **optional / nullable** | <code>Optional[Type]</code>, <code>Type &#124; None</code> |
| `arg: Type? = None` | optional parameter | <code>arg: Type &#124; None = None</code> |
| `list<Card>`, `dict<str, int>`, `tuple<Bid>` | generic container | `list[Card]`, `dict[str, int]`, `tuple[Bid, ...]` |
| `(BasePlayer, Card)` | tuple of fixed shape | `tuple[BasePlayer, Card]` |
| <code>int &#124; SlamLevel</code> | union | same |
| `{static}` | class-level | `ClassVar`, `@classmethod`, `@staticmethod` |
| `{abstract}` | must be overridden | `@abstractmethod` |
| `<<property>>` | computed on access | `@property` |

`Type?` is the one that trips people up: strict UML would write multiplicity instead (`dealer : Player [0..1]`), which is why the *relationship* lines already use `"0..1"`. Both notations appear and always agree — `?` is used on member lines because `[0..1]` on every optional field makes the boxes far noisier.

Class bodies are divided by separators: a bare `--` splits attributes from methods, and `.. label ..` opens a named group (`.. query helpers ..`, `.. engine hooks ..`) when a class is large enough that the method list needs signposting.

### Stereotypes

`<<…>>` (rendered as guillemets) carries three different jobs:

- **What kind of thing this is** — `<<enum>>`, `<<frozen dataclass>>`, `<<dataclass>>`, `<<namedtuple>>`, `<<StrEnum>>`, `<<abstract>>`, `<<mixin>>`, `<<module>>`, `<<modules>>`, `<<base>>`.
- **Who owns it** — `<<from contrai-core>>` on a boundary type, `<<engine>>` / `<<scraper>>` on sequence participants, `<<stdlib>>` on a Python built-in. Paired with the owner's fill from the palette table.
- **What it does / when** — a short annotation on a member (`<<no-op>>`, `<<delegates>>`, `<<validates value + suit>>`, `<<1, 2, or 4>>`) or on a relationship (`<<raises …>>`, `<<builds>>`, `<<materialises>>`, `<<observe>>`).

A **module pseudo-class** models a file that holds free functions rather than a class: `class "levels.py" as p_levels <<module>>`, with the function signatures as its members. `<<modules>>` (plural) collapses a group of sibling files into one box when their individual contents belong in a note instead.

### Class-diagram relationships

| Arrow | Means | Read as |
| ----- | ----- | ------- |
| <code>A &lt;&#124;-- B</code> | inheritance | B subclasses A |
| `A *-- B` | composition | A owns B; B does not outlive A |
| `A o-- B` | aggregation | A references B; B has its own lifetime |
| `A --> B` | association | A holds/uses B as a field |
| `A ..> B` | dependency | A mentions B transiently — a parameter, a return, a raise |
| `A .. B` | plain link | anchors a free-standing note to A |

Multiplicities are quoted on both ends and use the domain's real numbers, not generic `*` — `Deck "1" *-- "32" Card`, `Team "1" o-- "2" BasePlayer`, `Hand "1" *-- "0..8" Card`. `"0..N"` is the escape hatch for genuinely unbounded collections. The label after `:` names the attribute (`: cards`, `: play_state`) or the operation (`: get_current_winner(trump_suit)`).

Boxes are grouped in `package` blocks named after **the source file or subpackage they live in** — `package "contract.py / trick.py"`, `package "model/round/ package"` — so a box's position on the diagram tells you which file to open.

### Sequence diagrams

Participants are declared with an alias and an owner stereotype (`participant "Round" as R <<engine>>`); a human is an `actor`. Arrows from the frame edge mark entry and exit (`[-> R : manage_bidding(view)` / `[<-- R : return contract`). Section dividers (`== Build the Contract ==`) chapter a long flow, `loop` / `alt` / `opt` headers state their real Python condition (`loop **while not auction.is_terminal()**`), and every `activate` has a matching `deactivate`.

### Mermaid diagrams

Node shape encodes role: stadium `Start(["score_round(round)"])` for entry and terminal nodes, diamond `X{"made?"}` for decisions, rectangle `Y["…"]` for steps. Labels containing brackets or parentheses **must be quoted** or the render aborts, line breaks are `<br/>` (not `\n`), and emphasis is HTML (`<b>…</b>`). Colour is applied through `classDef` groups named for their role (`entry`, `step`, `decision`) or for the screen they represent (`screen0`…`screen5`), assigned in a palette block at the bottom of the file.

## Catalogue

Each row links to the canonical `.puml` source, the rendered `.png` preview, and the topical page where the diagram is embedded.

| Diagram                | Kind     | Scope                | Source                                | PNG preview                          | Embedded on                                                |
|------------------------|----------|----------------------|---------------------------------------|--------------------------------------|------------------------------------------------------------|
| `class_core.puml`      | Class    | contrai-core         | [source](class_core.puml)             | [png](class_core.png)                | [Core overview](../core/#class-structure)                  |
| `class_engine.puml`    | Class    | contrai-engine + MVC | [source](class_engine.puml)           | [png](class_engine.png)              | [Engine overview](../engine/#class-structure)              |
| `class_analyzer.puml`  | Class    | contrai-analyzer     | [source](class_analyzer.puml)         | [png](class_analyzer.png)            | [Analyzer overview](../analyzer/#class-structure)          |
| `class_workspace.puml` | Class    | Workspace overview   | [source](class_workspace.puml)        | [png](class_workspace.png)           | [Architecture](../architecture/#package-map)               |
| `seq_cli.puml`         | Sequence | Engine CLI game loop | [source](seq_cli.puml)                | [png](seq_cli.png)                   | [Engine — game loop](../engine/#game-loop)                 |
| `seq_round.puml`       | Sequence | Engine round flow    | [source](seq_round.puml)              | [png](seq_round.png)                 | [Engine — round lifecycle](../engine/#round-lifecycle)     |
| `seq_bidding.puml`     | Sequence | Bidding cycle zoom   | [source](seq_bidding.puml)            | [png](seq_bidding.png)               | [Engine — bidding cycle zoom](../engine/#round-lifecycle)  |
| `seq_trick.puml`       | Sequence | Single trick zoom    | [source](seq_trick.puml)              | [png](seq_trick.png)                 | [Engine — single trick zoom](../engine/#round-lifecycle)   |
| `flow_scoring.mmd`     | Flowchart| Round scoring tree   | [source](flow_scoring.mmd)            | [png](flow_scoring.png)              | [Engine — scoring](../engine/#scoring)                     |
| `seq_scraper.puml`     | Sequence | contrai-scraper      | [source](seq_scraper.puml)            | [png](seq_scraper.png)               | [Scraper overview](../scraper/#current-flow-v1)            |
| `state_cli_screens.mmd`| State    | RichView screen flow | [source](state_cli_screens.mmd)       | [png](state_cli_screens.png)         | [Engine — CLI](../engine/#cli)                             |
