# contrai-engine

Game engine for Coinche / Contrée. MVC architecture.

## Layout

- `src/contrai_engine/model/` — `Card`, `Deck`, `Hand`, `Bid`, `Contract`, `Trick`, `Round`, `Game`, `Player`, `Team`, `exceptions`
- `src/contrai_engine/controller/` — `GameController` (stub)
- `src/contrai_engine/view/` — `CliView` (stub)
- `tests/` — pytest suite for the model layer

## AI players

`AiPlayer` implements the expert bidding table (80–160, Capot) and card-play strategy from the functional specs (SF-09, SF-10).

> TODO: per-class one-liners; MVC sequence diagram.
