# contrai-engine

Game engine for Coinche / Contrée. MVC architecture, sits on top of `contrai-core` for all shared types.

## Layout

Source at `packages/contrai-engine/src/contrai_engine/`:

- `model/` — engine-side model layer:
  - `player.py` — `Player`, `HumanPlayer`, `AiPlayer` (all extending `BasePlayer` from `contrai-core`)
  - `game.py` — `Game` (fires `view.on_round_dealt(...)` after the deal and `view.on_all_pass_redeal(...)` when nobody contracts)
  - `round.py` — `Round` (publishes `view.on_bid_made(...)`, `view.on_card_played(...)`, `view.on_trick_complete(...)`, and `view.on_belote_announced(...)` so the view can pace and narrate AI turns)
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

`uv run contrai` (or `python -m contrai_engine.cli`) launches a six-screen Rich-based terminal UI driven by `RichView` and wired in `cli.py`:

| Screen        | Trigger                                 | Notes                                                                                                                                                                                       |
| ------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Landing**   | program start, `n` from end-game        | Target-score picker (500 / 1000 / 1500 / 2000 / 3000). Hardcoded seating today: South = `HumanPlayer`, N/E/W = `AiPlayer` (medium).                                                         |
| **Bidding**   | `Round.manage_bidding` → human turn     | Game-score + Round panels (title shows `Round N`), bid history with ` - ` separator between bidding rounds, hand + prompt. Accepts `pass`, `double`, `redouble`, `<value> <suit>` (English only — the FR aliases `coinche`/`surcoinche` are rejected). When an opponent has doubled the contractor's contract, the hint switches to `(pass / redouble)`. |
| **Mid-trick** | `Round.play_trick` → human turn         | Diamond seating (N top, E right, S bottom, W left). Live winner gets the gold pill. Hand row dim/green for legal vs. illegal plays. Once the holder plays a K or Q of trump, a persistent `★ Belote` badge appears under their seat for the rest of the round (the Belote / Rebelote distinction is kept in the event log). |
| **Trick won** | `Round` fires `view.on_trick_complete`  | Four-card diamond with the winner highlighted; "Press [Enter] to continue…". The hook is gated on `hasattr(view, 'on_trick_complete')`.                                                     |
| **Round recap** | `cli.py` calls `view.show_round_recap` after `view.on_round_complete` | Between-round panel: contract (or "All passed"), made / failed badge, per-team round points, running totals, target line, belote advisory. Waits for Enter; skipped when the game just ended (the end-game banner takes over). |
| **Game over** | `Game.check_game_over(target)` true     | Double-line gold banner, round-by-round summary table. `[n]` new game · `[r]` rematch · `[q]` quit.                                                                                          |

Every in-game screen also carries a rolling **event log** panel (5 lines, "Log") slotted between the hand and the prompt. It captures the last few engine events — deal, all-pass redeal, every bid, every card play, every trick winner, belote / rebelote announcements — so the user always sees the narrative continuity, even when AI players act faster than they can read.

Per-round summaries shown on the end-game scoreboard are tracked **view-side** (`RichView.history: list[RoundSummary]`), so `Game` itself stays free of UI state.

**Pacing.** AI bids and card plays each fire a view hook that re-renders the state with the new action visible, then sleeps for a tunable interval before the next player acts. Defaults: `1.4 s` between bids, `0.9 s` between card plays and after a belote announcement. Override via env vars (any positive float; garbage falls back to the default, negatives clamp to zero):

```bash
CONTRAI_AI_BID_DELAY=0.5  CONTRAI_AI_CARD_DELAY=0.3  uv run contrai
```

The pure helpers (bid parser, card parser, hand sorter, current-winner, constraint hint, redouble-availability check, delay resolver, bid-to-legacy converter) live at module scope and are covered by `tests/test_view/test_rich_view.py`. The `Panel`/`Table` builders are validated end-to-end by smoke-running `uv run contrai`.

```mermaid format="svg" source="state_cli_screens.mmd"
```

The screen flow above is rendered from [`state_cli_screens.mmd`](../diagrams/state_cli_screens.mmd) — the canonical source — and shows every transition the view drives, including the `on_trick_complete` callback edge and the new between-rounds recap.

See the [Rich TUI design handoff](../../ContrAI%20CLI/design_handoff_contrai_tui/README.md) for the visual spec, including all five SVG mockups (the design predates the recap screen and the event log panel, both of which build on top of the same vocabulary).

## Round lifecycle

```plantuml format="svg" source="seq_round.puml"
```

The end-to-end flow of `Game.manage_round`: setup (deal, dealer rotation, players_order, `view.on_round_dealt` notification) → bidding (delegated to `Round.manage_bidding`, which establishes the contract and snapshots the belote holder if any) → eight tricks (`Round.play_all_tricks`) → scoring (`calculate_round_scores`, with belote +20 and dix-de-der +10, applying the double / redouble multiplier). The failed-contract branch (everyone passed) returns zero scores, redistributes cards, and fires `view.on_all_pass_redeal`. After each `manage_round`, `cli.py` calls `view.on_round_complete` and then `view.show_round_recap` (skipped when the game has just crossed the target).

The two zoom diagrams below break out the dense parts.

??? note "Bidding cycle zoom — `Round.manage_bidding`"

    ```plantuml format="svg" source="seq_bidding.puml"
    ```

    The bid loop and its termination conditions. Each choice is converted from the legacy wire format (`'Pass'` / `'Double'` / `'Redouble'` / `(value, suit)`) into a proper `Bid` subclass via `Round._create_bid_from_choice`, then validated by `BidValidator.is_bid_valid`. An invalid bid is silently forced to a pass. After every commit Round fires `view.on_bid_made(player, bid, history)` so the view can log the action and pause for AI bidders. Once a contract is built, `Round._detect_belote_holder()` scans hands for the K + Q of trump (NO_TRUMP contracts skip the scan).

??? note "Single trick zoom — `Round.play_trick`"

    ```plantuml format="svg" source="seq_trick.puml"
    ```

    Leader determination → four players play in order → winner + bookkeeping → `view.on_card_played(player, card, trick)` after each landing card → optional `view.on_belote_announced(player, kind, round_)` when the trump K-or-Q is played by the holder → `view.on_trick_complete(trick, winner, round_)` callback (each hook is gated on `hasattr(view, …)`, so non-Rich callers stay unaffected). Two subtleties to know: `Trick()` is built without a `trump_suit`, so `Round._determine_trick_winner` reads `self.contract.suit` directly and duplicates the logic that lives on `Trick.get_winner()`; and an illegal `choose_card` result is silently replaced with `playable_cards[0]`. Legality (`_get_playable_cards`) now correctly forces over-trump when trump is led and keys the partner exemption on the *current master* of the partial trick — see the legality note at the foot of the diagram.

## Open work

Refreshed from `CLAUDE.md` §10:

- `Round` now has its first dedicated pytest file (`tests/test_model/test_round.py`) covering the `_get_playable_cards` legality oracle and the belote tracking helpers. The lifecycle path (`manage_bidding` / `play_all_tricks` / `calculate_round_scores`) is still un-tested end-to-end — backfill needed.
- `RichView`'s `Panel`/`Table` *rendering* methods (`_panel_*`) are now lightly covered: title/text smoke tests for the round panel, bidding-history, event-log, and round-recap panels, plus the diamond's belote badge. Layouts that aren't asserted on are still validated by `uv run contrai` smoke-running.
- `GameController` is still the lone surviving stub (see the grey box on the class diagram). It references undefined `pygame` and isn't wired to `Game` / `Round` — open question whether to delete it now that the Rich CLI doesn't need a separate controller, or keep it for a future GUI path.
- Sweep `AiPlayer` private helpers for any remaining `contract[…]` indexing residues — the four visible call sites were fixed during CLI work but a defensive pass through the strategy code would not hurt.
- `_check_double_redouble` in `AiPlayer` still uses the legacy-format `last_bid` to detect a prior Double; the inner check `last_bid == 'Double'` shadows the earlier `isinstance(last_bid, tuple)` guard so AI redouble is effectively gated on a `TODO`. Worth revisiting alongside any future AI bidding work.
