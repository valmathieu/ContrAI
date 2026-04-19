# Diagrams

Architecture, sequence, class, and state diagrams illustrating ContrAI components.

**Format:** [PlantUML](https://plantuml.com/) source, versioned alongside markdown.

## Rendering

- **VS Code:** install the *PlantUML* extension (`jebbs.plantuml`)
- **Web:** paste source into [planttext.com](https://www.planttext.com/) or [plantuml.com/uml](https://www.plantuml.com/plantuml/uml/)
- **CLI:** `plantuml -tpng diagram.puml`

## Conventions

- One `.puml` per diagram; descriptive filename (`engine_class.puml`, `scraper_sequence.puml`, …)
- Reference spec IDs (e.g. `SF-09`) where applicable
- Keep diagrams minimal and traceable to spec or package documentation

> TODO: initial diagrams — architecture overview, engine MVC class, scraper sequence.
