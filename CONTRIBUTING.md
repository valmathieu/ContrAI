# Contributing to ContrAI

ContrAI is a personal research project on AI for the French card game
*Contrée*. It's a learning vehicle as much as an engineering project, so the
conventions here lean toward "make it easy for future-Valentin (or any
collaborator) to pick up the thread six months later" rather than ceremony for
its own sake.

If you've forked or cloned this repo to play around, build on top of it, or
contribute back: welcome. This file is the handbook.

## What's in the repo

ContrAI is a [uv](https://docs.astral.sh/uv/) workspace with four packages:

- **`contrai-core`** — Shared domain types (cards, suits, contracts, players).
  No game logic, no UI. Other packages depend on this.
- **`contrai-engine`** — The Contrée game engine. CLI, MVC architecture,
  rule-based AI players, pytest-tested.
- **`contrai-analyzer`** — A Streamlit dashboard for hand analysis using
  hypergeometric probabilities. Deliberately independent of `contrai-core`
  (different abstractions for a different question — don't try to unify them).
- **`contrai-scraper`** — A Playwright-based spectator scraper for an online
  Contrée site, persisting observed games to SQLite.

Specs and the LaTeX report live in a **separate** repo, `contrai-docs`. Don't
propose changes to `Specs_fonctionnelles.md` or `Specs_logicielles.md` in PRs
here — those live there.

## Getting started

Requirements:

- Python 3.14 (uv will install it for you)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Clone and set up:

```bash
git clone https://github.com/<your-fork>/contrai.git
cd contrai
uv sync --all-packages --all-extras
```

Run the tests:

```bash
uv run --package contrai-core pytest
uv run --package contrai-engine pytest
```

Run the CLI engine:

```bash
uv run --package contrai-engine python -m contrai_engine
```

Run the analyzer dashboard:

```bash
uv run --package contrai-analyzer streamlit run src/contrai_analyzer/app.py
```

(Adjust the entrypoint paths once the actual modules are in place.)

## Branching

ContrAI follows **GitHub Flow**: `main` is always working, every non-trivial
change lives on a short-lived feature branch, and changes land via pull
requests.

Branch names mirror the Conventional Commit type and the scope of the work:

```
<type>/<scope>-<short-description>
```

Examples:

```
feat/engine-bidding-double
fix/core-suit-equality
refactor/engine-extract-trick-class
test/core-deck-shuffle-edge-cases
ci/add-ruff-check
```

Types and scopes come from the Conventional Commits convention (see below).

Don't commit directly to `main`. The only exception is the very first
scaffolding commit on an empty repo; after that, everything goes through a
branch.

## Commits

Every commit follows
[Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

[optional body explaining what and why]
```

Allowed types:

- `feat` — A new feature
- `fix` — A bug fix
- `refactor` — Code change that neither fixes a bug nor adds a feature
- `docs` — Documentation only
- `test` — Adding or fixing tests
- `chore` — Build, tooling, maintenance
- `perf` — Performance improvement
- `style` — Formatting only (no code change)
- `build` — Build system or dependency changes
- `ci` — CI configuration

Scopes usually map to packages: `core`, `engine`, `analyzer`, `scraper`. In
the `contrai-docs` repo, scopes are `specs` or `report`.

**Atomic commits.** One logical change per commit. The rule of thumb: if
you'd want to revert two things together, they belong in one commit; if you'd
ever want to revert one without the other, split them. Adding a feature with
its tests is one logical change. Refactoring AND adding a feature is two.

**No AI co-authorship trailers.** Commits are attributed to the human author
only, regardless of whether an AI assistant helped draft the change.

## Pull requests

Open a PR for anything beyond a one-line fix or typo. Yes, even when you're
the only person on the project — the PR is your **self-review ritual**.

What a self-review catches:

- Forgotten `print()` or `breakpoint()`
- Stale docstrings
- Commented-out code that should have been deleted
- Inconsistent naming
- Missing tests
- Files you forgot to add to the staging area

Process:

1. Push your branch to GitHub.
2. Open a PR with a description that explains *what* and *why* (the *how* is
   in the diff).
3. Use **draft PRs** for work-in-progress you want CI to run on without
   claiming it's ready.
4. Review your own diff in the GitHub UI before merging.
5. Wait for CI to pass.
6. Merge.

PR titles should match the eventual commit subject — Conventional Commits
format.

## Merging

The default merge strategy is **rebase-merge**: each commit on the feature
branch is replayed onto `main`. This keeps the history linear and preserves
the atomic commits you carefully crafted.

Use **squash-merge** when the branch has messy intermediate commits (`wip`,
`fix typo`, `actually fix it`) or when the whole PR is one logical change and
the individual commits aren't worth keeping.

Avoid **merge commits**. They add noise without information for a project
this size.

## Tests

`contrai-core` and `contrai-engine` use pytest. Tests are mandatory for:

- New Model-layer code in the engine (non-negotiable per project rules)
- New types or invariants in `contrai-core`
- Bug fixes — write a test that fails before the fix and passes after; it's
  the cheapest insurance against regression

`contrai-analyzer` and `contrai-scraper` don't currently have test suites.
When they grow them, this section gets updated and they join the CI matrix.

CI runs pytest on `core` and `engine` on every PR. The merge button is
blocked until tests pass.

Run locally before pushing:

```bash
uv run --package contrai-core pytest
uv run --package contrai-engine pytest
```

## Code style

- **Type hints everywhere.** Function signatures, class attributes, return
  types. `ruff`, `mypy`, or `pyright` may join CI later.
- **Google-style docstrings** on every public class, method, and function.
- **Didactic comments are welcome.** This is a learning project — explain
  non-trivial logic, especially anything probability-, combinatorics-, or
  ML-related. Comments that explain *why* (not *what*) are gold.
- **English in code.** Identifiers, comments, docstrings — all English.
  Reports and specs live in the `contrai-docs` repo and may be bilingual.

## Architecture rules

The engine uses **MVC**: Model (game state, rules), View (CLI), Controller
(orchestration). Don't bypass it silently. If a feature seems to require
crossing the boundaries — for instance, a Gym-style env wrapper for RL
training accessing internal state — raise it in an issue or PR description
first. MVC is explicitly on the table for re-discussion when ML training
arrives; until then, respect it.

The **analyzer** has its own conventions and abstractions (e.g., `SuitSlot`).
Don't try to unify them with engine abstractions like `Suit` — they answer
different questions, and the deliberate separation is the point.

## Dependencies

Don't add a dependency to any `pyproject.toml` without flagging it first
(open an issue or mention it in the PR description before adding). Every
dependency is a long-term commitment — preferable to reach for the stdlib or
a small focused library before pulling in a heavy one.

When you do add a dependency: `uv add --package <pkg> <dep>` and commit the
updated `pyproject.toml` and `uv.lock` together.

## Diagrams

- **PlantUML** for sequence and class diagrams. Source files end in `.puml`.
- **Mermaid** for everything else (component, state, flowchart, ER,
  deployment, mindmap, etc.). Source files end in `.mmd`.
- **Use color.** Distinguish MVC layers, package boundaries, actors,
  hot/cold paths. Plain black-and-white renders are dispreferred.

Rendering commands:

```bash
plantuml -tpng diagram.puml          # → diagram.png
mmdc -i diagram.mmd -o diagram.png   # → diagram.png
```

Commit the source (`.puml` or `.mmd`) and the rendered `.png` **together in
the same commit**. The PNG is what readers see on GitHub and in the report;
the source is what gets edited.

## Releases and versioning

ContrAI is pre-1.0, so versions are `0.x.y`. Breaking changes are allowed in
minor bumps. All four packages move in **lockstep** — same version, same
release — until something external (the planned multiplayer web server, a
published artifact, a friend's fork pinning a specific version) forces
independence.

Releases are **milestone tags**:

```bash
git tag -a v0.1.0 -m "First playable CLI engine"
git push --tags
```

Keep `CHANGELOG.md` current: every user-facing change adds a bullet under
`## [Unreleased]` in the **same commit** (`feat` → Added, `fix` → Fixed,
`refactor`/`perf` → Changed; internal `test`/`chore`/`ci`/`style`/`docs` commits are
skipped). Lead each bullet with the package scope, e.g. `- (engine) …`. Before tagging,
close out the changelog: rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a
fresh empty `## [Unreleased]`, bump the four packages' `version` fields, and update the
link references at the bottom of the file.

GitHub Releases (with notes) for the bigger milestones — these double as
report material later. There's no fixed release cadence; tag when something
worth tagging happens.

## Questions

Open an issue. Tag it with the package involved, or `meta` for cross-cutting
questions.

# 
