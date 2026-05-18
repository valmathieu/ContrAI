# contrai-engine

Game engine for Coinche / Contrée. MVC architecture, sits on top of `contrai-core` for all shared types.

## Layout

Source at `packages/contrai-engine/src/contrai_engine/`:

- `model/` — engine-side model layer:
  - `player.py` — `Player`, `HumanPlayer`, `AiPlayer` (all extending `BasePlayer` from `contrai-core`)
  - `game.py` — `Game`
  - `round.py` — `Round` (now publishes a `view.on_trick_complete(...)` callback between tricks)
- `controller/` — `GameController` (partial — see `CLAUDE.md` §2)
- `view/` — `RichView` (terminal UI, see [CLI](#cli) below)
- `cli.py` — `contrai` console-script entry point: landing → game-loop → end-game
- `tests/` — pytest suite (`test_model/`, `test_view/`)

Everything else (`Card`, `Deck`, `Hand`, `Suit`, `Rank`, `Bid`, `Contract`, `Trick`, `Team`, exceptions) is imported directly from `contrai_core`. There are no back-compat re-exports under the engine namespace anymore.

## Class structure

```plantuml format="svg" source="class_engine.puml"
```

`Player` extends `BasePlayer` from `contrai-core` (drawn as a blue boundary element). The two concrete subclasses are `HumanPlayer` (whose `choose_bid` / `choose_card` still return `None` — the `RichView` is what actually services human input through `Round`'s `view.request_*_action` hooks) and `AiPlayer` (full bidding + strategy). `GameController` remains in the grey stub palette: it still references undefined `pygame` and isn't wired to `Game` / `Round`. `RichView` is the live engine view; the old `CliView` placeholder has been removed. See [Diagrams](../diagrams/) for the colour convention.

## AI players

`AiPlayer` implements the expert bidding table (80–160 plus Capot) and the card-play strategy from the functional specs (`SF-09`, `SF-10`). The ~25 private strategy helpers are summarised on the class diagram above as a collapsed `<<strategy>>` note. `choose_card` lazy-initialises card tracking on first call (no need for callers to remember `initialize_card_tracking`), and consumes the real `Contract` object from `Round` rather than the legacy `(player, value, suit)` tuple some older tests once passed.

## CLI

`uv run contrai` (or `python -m contrai_engine.cli`) launches a five-screen Rich-based terminal UI driven by `RichView` and wired in `cli.py`:

| Screen        | Trigger                              | Notes                                                                                                                                       |
| ------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Landing**   | program start, `n` from end-game     | Target-score picker (500 / 1000 / 1500 / 2000 / 3000). Hardcoded seating today: South = `HumanPlayer`, N/E/W = `AiPlayer` (medium).         |
| **Bidding**   | `Round.manage_bidding` → human turn  | Game-score + Round panels, bid history, hand + prompt. Accepts `pass`, `coinche`/`double`, `surcoinche`/`redouble`, `<value> <suit>`.        |
| **Mid-trick** | `Round.play_trick` → human turn      | Diamond seating (N top, E right, S bottom, W left). Live winner gets the gold pill. Hand row dim/green for legal vs. illegal plays.         |
| **Trick won** | `Round` fires `view.on_trick_complete` | Four-card diamond with the winner highlighted; "Press [Enter] to continue…". The hook is gated on `hasattr(view, 'on_trick_complete')`. |
| **Game over** | `Game.check_game_over(target)` true  | Double-line gold banner, round-by-round summary table. `[n]` new game · `[r]` rematch · `[q]` quit.                                          |

Per-round summaries shown on the end-game scoreboard are tracked **view-side** (`RichView.history: list[RoundSummary]`), so `Game` itself stays free of UI state.

The pure helpers (bid parser, card parser, hand sorter, current-winner, constraint hint) live at module scope and are covered by `tests/test_view/test_rich_view.py`. The `Panel`/`Table` builders are validated end-to-end by smoke-running `uv run contrai`.

```mermaid format="svg" source="state_cli_screens.mmd"
```

The five-screen flow above is rendered from [`state_cli_screens.mmd`](../diagrams/state_cli_screens.mmd) — the canonical source — and shows every transition the view drives, including the `on_trick_complete` callback edge.

See the [Rich TUI design handoff](../../ContrAI%20CLI/design_handoff_contrai_tui/README.md) for the visual spec, including all five SVG mockups.

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

    Leader determination → four players play in order → winner + bookkeeping → `view.on_trick_complete(trick, winner, round_)` callback (gated on `hasattr(view, …)`, so non-Rich callers stay unaffected). Two subtleties to know: `Trick()` is built without a `trump_suit`, so `Round._determine_trick_winner` reads `self.contract.suit` directly and duplicates the logic that lives on `Trick.get_winner()`; and an illegal `choose_card` result is silently replaced with `playable_cards[0]`. SF-09 / SF-10 legality rules are documented in the note at the foot of the diagram.

## Open work

Refreshed from `CLAUDE.md` §10:

- `Round` still ships without `pytest` coverage; backfill needed (engine convention now requires tests for any branching engine code, Model or View).
- `RichView`'s `Panel`/`Table` *rendering* methods (`_panel_*`) lack unit tests — only their pure helpers do. Consider snapshot-style tests if the layouts churn.
- `GameController` is still the lone surviving stub (see the grey box on the class diagram). It references undefined `pygame` and isn't wired to `Game` / `Round` — open question whether to delete it now that the Rich CLI doesn't need a separate controller, or keep it for a future GUI path.
- Sweep `AiPlayer` private helpers for any remaining `contract[…]` indexing residues — the four visible call sites were fixed during CLI work but a defensive pass through the strategy code would not hurt.
