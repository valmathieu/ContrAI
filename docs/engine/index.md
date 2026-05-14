# contrai-engine

Game engine for Coinche / Contrée. MVC architecture, sits on top of `contrai-core` for all shared types.

## Layout

Source at `packages/contrai-engine/src/contrai_engine/`:

- `model/` — engine-side model layer:
  - `player.py` — `Player`, `HumanPlayer`, `AiPlayer` (all extending `BasePlayer` from `contrai-core`)
  - `game.py` — `Game`
  - `round.py` — `Round`
- `controller/` — `GameController` (partial — see `CLAUDE.md` §2)
- `view/` — `CliView` (partial)
- `tests/` — pytest suite

Everything else (`Card`, `Deck`, `Hand`, `Suit`, `Rank`, `Bid`, `Contract`, `Trick`, `Team`, exceptions) is imported directly from `contrai_core`. There are no back-compat re-exports under the engine namespace anymore.

## Class structure

```plantuml format="svg" source="class_engine.puml"
```

`Player` extends `BasePlayer` from `contrai-core` (drawn as a blue boundary element). The two concrete subclasses are `HumanPlayer` (stubbed — `choose_bid` / `choose_card` return `None` today) and `AiPlayer` (full strategy). `GameController` and `CliView` appear in the grey stub palette: the controller still references undefined `pygame` and isn't wired to `Game` / `Round`, and `cli_view.py` is an empty file — so the `view=…` branches in `Round` are guarded but unreachable. See [Diagrams](../diagrams/) for the colour convention.

## AI players

`AiPlayer` implements the expert bidding table (80–160; no Capot row today) and the card-play strategy from the functional specs (`SF-09`, `SF-10`). The ~25 private strategy helpers are summarised on the class diagram above as a collapsed `<<strategy>>` note.

## Round lifecycle

```plantuml format="svg" source="seq_round.puml"
```

The end-to-end flow of `Game.manage_round`: setup (deal, dealer rotation, players_order) → bidding (delegated to `Round.manage_bidding`) → eight tricks (`Round.play_all_tricks`) → scoring (`calculate_round_scores`, with belote +20 and dix-de-der +10, applying the double / redouble multiplier). The failed-contract branch (everyone passed) returns zero scores and redistributes cards.

The two zoom diagrams below break out the dense parts.

??? note "Bidding cycle zoom — `Round.manage_bidding`"

    ```plantuml format="svg" source="seq_bidding.puml"
    ```

    The bid loop and its termination conditions. Each choice is converted from the legacy wire format (`'Pass'` / `'Double'` / `'Redouble'` / `(value, suit)`) into a proper `Bid` subclass via `Round._create_bid_from_choice`, then validated by `BidValidator.is_bid_valid`. An invalid bid is silently forced to a pass. The view-driven human branch is shown dashed as `<<not implemented>>` because `CliView` is empty.

??? note "Single trick zoom — `Round.play_trick`"

    ```plantuml format="svg" source="seq_trick.puml"
    ```

    Leader determination → four players play in order → winner + bookkeeping. Two subtleties to know: `Trick()` is built without a `trump_suit`, so `Round._determine_trick_winner` reads `self.contract.suit` directly and duplicates the logic that lives on `Trick.get_winner()`; and an illegal `choose_card` result is silently replaced with `playable_cards[0]`. SF-09 / SF-10 legality rules are documented in the note at the foot of the diagram.

## Open work

Pulled from `CLAUDE.md` §10:

- `TestAiPlayerTrickTaking` has 13 pre-existing failures — `MockTrick` exposes `.cards`, but `AiPlayer` indexes with `trick[1][0]`. `AiPlayer` needs to consume the real `Trick.get_plays()` API.
- `Round` shipped without `pytest` coverage; backfill needed (engine model layer convention requires tests with every addition).
- Controller and CLI view layers are still partial — see the `<<stub>>` boxes on the class diagram above.
