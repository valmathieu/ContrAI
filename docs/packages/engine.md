# contrai-engine

Game engine for Coinche / Contrée. MVC architecture, sits on top of `contrai-core` for all shared types.

## Layout

Source at `packages/contrai-engine/src/contrai_engine/`:

- `model/` — engine-side model layer:
  - `player.py` — `Player`, `HumanPlayer`, `AiPlayer` (all extending `BasePlayer` from `contrai-core`)
  - `game.py` — `Game`
  - `round.py` — `Round`
- `controller/` — `GameController` (partial — see [CLAUDE.md §2](../../CLAUDE.md))
- `view/` — `CliView` (partial)
- `tests/` — pytest suite

Everything else (`Card`, `Deck`, `Hand`, `Suit`, `Rank`, `Bid`, `Contract`, `Trick`, `Team`, exceptions) is imported directly from `contrai_core`. There are no back-compat re-exports under the engine namespace anymore.

## AI players

`AiPlayer` implements the expert bidding table (80–160, Capot) and the card-play strategy from the functional specs (`SF-09`, `SF-10`).

## Open work

Pulled from [CLAUDE.md §10](../../CLAUDE.md):

- `TestAiPlayerTrickTaking` has 13 pre-existing failures — `MockTrick` exposes `.cards`, but `AiPlayer` indexes with `trick[1][0]`. `AiPlayer` needs to consume the real `Trick.get_plays()` API.
- `Round` shipped without `pytest` coverage; backfill needed (engine model layer convention requires tests with every addition).
- Controller and CLI view layers are still partial.

> TODO: MVC class diagram (`.puml`) and round-flow sequence diagram (`.puml`) under [`../diagrams/`](../diagrams/).
