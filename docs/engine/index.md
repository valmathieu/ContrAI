# contrai-engine

Game engine for Coinche / Contrée. MVC architecture, sits on top of `contrai-core` for all shared types.

## Layout

Source at `packages/contrai-engine/src/contrai_engine/`:

- `model/` — engine-side model layer:
  - `player/` — the player subpackage (all classes extend `BasePlayer` from `contrai-core`); `player/__init__.py` re-exports the public names so external imports (`from contrai_engine.model.player import …`) are unchanged:
    - `base.py` — `Player` (abstract) and `HumanPlayer`
    - `wire.py` — the temporary `wire_to_bid` / `bid_to_wire` bridge between the legacy wire format and `Bid` objects
    - `strategy.py` — the `BiddingStrategy` / `CardPlayStrategy` abstract interfaces and the `_PlayerStrategy` mix-in (live read access to the owning player's `hand` / `team` / `position`)
    - `rule_based.py` — `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy`, the first concrete level (the expert `SF-09` / `SF-10` rules)
    - `ai.py` — `AiPlayer`, which injects a bidding and a card-play strategy and delegates to them
    - `levels.py` — the `AI_LEVELS` registry + `make_ai_player()` factory for difficulty selection
  - `game.py` — `Game` (fires `view.on_round_dealt(...)` after the deal and `view.on_all_pass_redeal(...)` when nobody contracts)
  - `round.py` — `Round` (publishes `view.on_bid_made(...)`, `view.on_contract_established(...)`, `view.on_card_played(...)`, `view.on_trick_complete(...)`, and `view.on_belote_announced(...)` so the view can pace and narrate AI turns)
- `controller/` — `GameController` (partial stub — see [Open work](#open-work))
- `view/` — the terminal UI, split into focused modules (see [CLI](#cli) below):
  - `rich_view.py` — `RichView`, the stateful orchestrator: console + per-game state, the engine hooks (`request_*_action`, `on_*`, `show_*`), the input loops, and `_render_in_game` (the single seam that pulls state off `self` and feeds the pure builders). `RoundSummary` lives here too. Re-exported from `view/__init__.py`, so both `from contrai_engine.view.rich_view import RichView` (used by `cli.py` / `model/game.py`) and `from contrai_engine.view import RichView` work.
  - `theme.py` — design tokens (colour palette) and lookup tables (target-score options, position / team labels, bid aliases, valid contract values)
  - `formatting.py` — stateless text / glyph / label builders (seat & suit labels, the shared contract / trump / legacy-bid labels)
  - `parsing.py` — human-input parsers (`_parse_bid_input`, `_parse_card_input`)
  - `bidding_rules.py` — messaging-only mirrors of the auction rules that gate the bid-prompt hint (`_double_available_to`, `_redouble_available_to`, `_min_legal_contract_value`, `_illegal_bid_reason`, `_bid_to_legacy`)
  - `state_helpers.py` — small game-state readers (`_current_winner`, `_explain_constraint`, `_sort_hand_for_display`, `_belote_by_position`, `_resolve_delay`)
  - `layout.py` — cross-screen layout (`_two_column`, the Prompt panel, the event-log panel)
  - `screens/` — one module per screen of the five-screen design: `landing.py`, `bidding.py`, `trick.py`, `recap.py`, `endgame.py`. Each exposes pure `(data) -> Panel/Text` builders; `RichView` composes and prints them.
- `cli.py` — `contrai` console-script entry point: landing → game-loop → end-game
- `tests/` — pytest suite (`test_model/`, `test_view/`)

Everything else (`Card`, `Deck`, `Hand`, `Suit`, `Rank`, `Bid`, `Contract`, `Trick`, `Team`, exceptions) is imported directly from `contrai_core`. There are no back-compat re-exports under the engine namespace anymore.

## Class structure

```plantuml format="svg" source="class_engine.puml"
```

`Player` extends `BasePlayer` from `contrai-core` (drawn as a blue boundary element). The two concrete subclasses are `HumanPlayer` (whose `choose_bid` / `choose_card` still return `None` — the `RichView` is what actually services human input through `Round`'s `view.request_*_action` hooks) and `AiPlayer`, which holds an injected `BiddingStrategy` and `CardPlayStrategy` and delegates to them. `GameController` remains in the grey stub palette: it still references undefined `pygame` and isn't wired to `Game` / `Round`. `RichView` is the live engine view; the old `CliView` placeholder has been removed. See [Diagrams](../diagrams/) for the colour convention.

## AI players

`AiPlayer` owns no strategic logic of its own. It holds two strategy objects behind the abstract `BiddingStrategy` / `CardPlayStrategy` interfaces (`strategy.py`) and routes `choose_bid` / `choose_card` / the card-tracking hooks to them. Strategies are supplied at construction as **factories** (`player -> strategy`, i.e. the strategy class itself), resolving the chicken-and-egg of a strategy that needs a back-reference to the player while the player is still being built; the `_PlayerStrategy` mix-in then gives each strategy live read access to the player's `hand` / `team` / `position`. The defaults reproduce today's bot, so `AiPlayer("Bot", "South")` is unchanged.

The first concrete level is the rule-based pair (`rule_based.py`): `RuleBasedBiddingStrategy` implements the expert bidding table (80–160 plus Slam and Solo Slam) and `RuleBasedCardPlayStrategy` the card-play strategy from the functional specs (`SF-09`, `SF-10`). Future AI levels (MCTS, learned policies — AI roadmap §6) are new strategy classes, never edits to `AiPlayer`; a thin `AI_LEVELS` registry + `make_ai_player(name, position, level="expert")` factory (`levels.py`) gives ergonomic difficulty selection on top, while the raw `AiPlayer(..., bidding=…, cardplay=…)` form stays available for mix-and-match (e.g. rule-based bidding + a learned card-play). The strategy object is also the natural home for a future explainability rule-trace (§6.1). `choose_card` lazy-initialises card tracking on first call (no need for callers to remember `initialize_card_tracking`), and consumes the real `Contract` object from `Round` rather than the legacy `(player, value, suit)` tuple some older tests once passed.

When the AI's team is currently winning the trick (`_play_when_team_winning`) and the AI cannot follow the led suit, the rule is *don't waste trumps*: prefer a non-trump discard over playing a trump card, even though a trump would add more points to the pile. Within the non-trump discard pool the AI prefers non-master cards (preserving cards that can still win their suit later) and picks the highest-points to maximise this trick's value. Only when the hand has nothing left but trumps does the AI play one — and it picks the lowest trump in that case, so the Jack or 9 of trump aren't dumped onto an already-won trick.

## CLI

`uv run contrai` (or `python -m contrai_engine.cli`) launches a six-screen Rich-based terminal UI driven by `RichView` and wired in `cli.py`:

| Screen        | Trigger                                 | Notes                                                                                                                                                                                       |
| ------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Landing**   | program start, `n` from end-game        | Target-score picker (500 / 1000 / 1500 / 2000 / 3000). Hardcoded seating today: South = `HumanPlayer`, N/E/W = `AiPlayer` (medium).                                                         |
| **Bidding**   | `Round.manage_bidding` → human turn     | Game-score + Round panels (title shows `Round #N`), bid history with ` - ` separator between bidding rounds, hand + prompt. Accepts `pass`, `double`, `redouble`, `<value> <suit>` (English only — the FR aliases `coinche`/`surcoinche` are rejected). When an opponent has doubled the contractor's contract, the hint switches to `(pass / redouble)`. When the player's **partner** has just doubled (or redoubled), the prompt is skipped entirely and the engine auto-passes them — pass is the only legal action and the human shouldn't have to confirm it. |
| **Mid-trick** | `Round.play_trick` → human turn         | Diamond seating (N top, E right, S bottom, W left). Live winner gets the gold pill. Hand row dim/green for legal vs. illegal plays. Once the holder plays a K or Q of trump, a persistent `★ Belote` badge appears under their seat for the rest of the round (the Belote / Rebelote distinction is kept in the event log). |
| **Trick won** | `Round` fires `view.on_trick_complete`  | Four-card diamond with the winner highlighted; "Press [Enter] to continue…". The hook is gated on `hasattr(view, 'on_trick_complete')`.                                                     |
| **Round recap** | `cli.py` calls `view.show_round_recap` after `view.on_round_complete` | Between-round panel: contract (or "All passed"; the label names the taker and any Coinche/Surcoinche caller, spelled out verbose as `doubled`/`redoubled` here — see below), a `Trump:` recall line (the contract suit, since the contract label omits it), made / failed badge, then **two stacked sub-tables** sharing the N-S / E-W columns. The **Outcome** table reports the factual play tally — `Tricks won` (count) and `Round points` (trump-aware pile + last-trick 10 + belote 20 each side captured, always the real total regardless of scoring shape). The **Scoring** table then traces how the contract converts that into the engine-computed round score — `Contract` (the bonus from the contract being made or failed), `Tricks` (the card pile), `Last trick`, `Belote` (label uses the actual trump glyph), a divider, then the `Round score` subtotal. When the engine substitutes a flat formula (doubled-made attacker, failed defender), the Scoring cards / last-trick / belote rows show em-dashes so the addition stays honest — but the Outcome table still surfaces the points genuinely captured. A final `Running` line carries the game totals and target, its numbers aligned under the team columns. Waits for Enter; shown after *every* round — when the same round just crossed the target, the prompt flips to "Press [Enter] to see the final score…" and the end-game banner is the next screen. |
| **Game over** | `Game.check_game_over(target)` true     | Double-line gold banner, round-by-round summary table. `[n]` new game · `[r]` rematch · `[q]` quit.                                                                                          |

Every in-game screen also carries a rolling **event log** panel (5 lines, "Log") slotted between the hand and the prompt. It captures the last few engine events — deal, all-pass redeal, every bid, the *contract-set* bookmark when bidding ends on a deal, every card play, every trick winner, belote / rebelote announcements — so the user always sees the narrative continuity, even when AI players act faster than they can read.

Per-round summaries shown on the end-game scoreboard are tracked **view-side** (`RichView.history: list[RoundSummary]`), so `Game` itself stays free of UI state.

**Contract label.** A single helper (`_format_contract_short`) renders the contract everywhere it appears — the in-game Round panel, the round recap, and the event-log *contract-set* line. It reads `VALUE by <taker>  ×2/×4 by <caller>`, e.g. `110 by E  ×2 by S`: the taker (`contract.player`) and any Coinche/Surcoinche caller are shown as single-letter seats colored by team (blue N-S, orange E-W). The caller identities ride on `Contract.double_player` / `redouble_player`, which `Auction.contract()` lifts off the bid history when it materialises the contract; the multiplier still renders if the caller is unknown. The recap passes `verbose=True`, which spells the markers out in full prose (`110 by N  doubled by E`, `120 by N  redoubled by N`) since the after-round summary has the room and reads better than the compact glyph.

**Pacing.** AI bids and card plays each fire a view hook that re-renders the state with the new action visible, then sleeps for a tunable interval before the next player acts. Defaults: `1.4 s` between bids, `0.9 s` between card plays and after a belote announcement. Override via env vars (any positive float; garbage falls back to the default, negatives clamp to zero):

```bash
CONTRAI_AI_BID_DELAY=0.5  CONTRAI_AI_CARD_DELAY=0.3  uv run contrai
```

**Play legality at the play boundary.** `Round.play_trick` plays a card only if it is in the `_get_playable_cards` legal set; a truthy-but-illegal card now raises `IllegalPlayError` (carrying the offending card, a `PlayRuleViolation` reason, and the legal alternatives) instead of being **silently corrected** to a legal fallback. The reason is computed by `_classify_play_violation`, which mirrors `_get_playable_cards`'s branch order and must stay in sync with it until the deferred `Ruleset` unifies the two. Both `AiPlayer.choose_card` and `RichView.request_card_action` are contracted to only ever return a card from `playable_cards`, so the raise is a safety net surfacing wiring bugs (cf. the `AiPlayer` cleanup in the open work) rather than a path hit in normal play — the headless 4-AI smoke run confirms it never fires.

**Bid legality at the input boundary.** `request_bid_action` parses raw input for *shape* (`_parse_bid_input`) and then validates it against `Auction.is_legal` before returning. An illegal-but-parseable bid — e.g. doubling your own partner's contract — re-prompts with a specific reason (`_illegal_bid_reason`) instead of escaping to `Auction.apply` and crashing the CLI. The model keeps its strict hard-raise contract; the human-input layer is where unvalidated input is filtered. The bid prompt hint is likewise adaptive: `double` / `redouble` are only advertised when `_double_available_to` / `_redouble_available_to` say they're legal for the seat to act, and the worked contract example tracks the auction via `_min_legal_contract_value` — it offers the cheapest legal raise (`100 H` once a `90` stands, not the bare `80` floor), and is dropped past `180` where only Slam outranks the standing contract.

The pure helpers (bid parser, card parser, hand sorter, current-winner, constraint hint, double- and redouble-availability checks, minimum-legal-contract floor, illegal-bid reason, delay resolver, bid-to-legacy converter) are module-level functions in their respective modules (`parsing`, `state_helpers`, `bidding_rules`, `formatting`), and the per-screen `Panel` / `Table` builders are pure functions under `screens/`. The test suite mirrors that split — `tests/test_view/test_{formatting,parsing,bidding_rules,state_helpers,layout,recap,endgame}.py` test the extracted modules, while `test_rich_view.py` keeps the stateful `RichView` behaviour (hooks, input loops, in-game frame). The shared `four_players` fixture lives in `tests/test_view/conftest.py`. The deepest `Panel` / `Table` layouts not asserted on are validated end-to-end by smoke-running `uv run contrai`.

```mermaid format="svg" source="state_cli_screens.mmd"
```

The screen flow above is rendered from [`state_cli_screens.mmd`](../diagrams/state_cli_screens.mmd) — the canonical source — and shows every transition the view drives, including the `on_trick_complete` callback edge and the new between-rounds recap.

See the [Rich TUI design handoff](../../ContrAI%20CLI/design_handoff_contrai_tui/README.md) for the visual spec, including all five SVG mockups (the design predates the recap screen and the event log panel, both of which build on top of the same vocabulary).

## Round lifecycle

```plantuml format="svg" source="seq_round.puml"
```

The end-to-end flow of `Game.manage_round`: setup (deal, dealer rotation, players_order, `view.on_round_dealt` notification) → bidding (delegated to `Round.manage_bidding`, which establishes the contract and snapshots the belote holder if any) → eight tricks (`Round.play_all_tricks`) → scoring (`calculate_round_scores`, with belote +20 and dix-de-der +10, applying the double / redouble multiplier). The failed-contract branch (everyone passed) returns zero scores, redistributes cards, and fires `view.on_all_pass_redeal`. After each `manage_round`, `cli.py` calls `view.on_round_complete` and then `view.show_round_recap(round_, scores, is_final=…)` — shown for every round, including the one that just clinched the game (the prompt flips to "see the final score…" so the end-game banner is what follows).

The two zoom diagrams below break out the dense parts.

??? note "Bidding cycle zoom — `Round.manage_bidding`"

    ```plantuml format="svg" source="seq_bidding.puml"
    ```

    The bid loop drives a `contrai_core.Auction` through `itertools.cycle(players_order)`. Each turn looks up `auction.legal_actions(player)`; when the only legal action is `PassBid` (partner just doubled or redoubled, or a pass closed the redouble window) the engine auto-applies it without prompting the player or the view. Otherwise `_gather_bid` consults `player.choose_bid(auction)` and — for the human seat — `view.request_bid_action(player, auction)`, both of which now return real `Bid` instances. The chosen bid is applied via `auction.apply(bid)`, which raises `IllegalBidError` rather than silently downgrading an illegal bid to a Pass. After every commit Round fires `view.on_bid_made(player, bid, history)` so the view can log the action and pause for AI bidders. Once `auction.is_terminal()`, the final `Contract` is materialised by `auction.contract()` and `Round._detect_belote_holder()` scans hands for the K + Q of trump (NO_TRUMP contracts skip the scan). The legacy `'Pass'` / `'Double'` / `'Redouble'` / `(value, suit)` wire format only survives inside the AI's expert table and the Rich view's renderer; the `wire_to_bid` / `bid_to_wire` helpers in `contrai_engine.model.player` bridge between the wire and the `Bid` boundary.

??? note "Single trick zoom — `Round.play_trick`"

    ```plantuml format="svg" source="seq_trick.puml"
    ```

    Leader determination → four players play in order → winner + bookkeeping → `view.on_card_played(player, card, trick)` after each landing card → optional `view.on_belote_announced(player, kind, round_)` when the trump K-or-Q is played by the holder → `view.on_trick_complete(trick, winner, round_)` callback (each hook is gated on `hasattr(view, …)`, so non-Rich callers stay unaffected). Two subtleties to know: `Trick()` is built bare (it stores no trump), so `Round.play_trick` passes `self.contract.suit` into `Trick.get_current_winner(trump_suit)` to pick the winner — there is no engine-side duplicate of that rule; and an illegal `choose_card` result is silently replaced with `playable_cards[0]`. Legality (`_get_playable_cards`) now correctly forces over-trump when trump is led and keys the partner exemption on the *current master* of the partial trick — see the legality note at the foot of the diagram.

## Open work

- `Round` now has its first dedicated pytest file (`tests/test_model/test_round.py`) covering the `_get_playable_cards` legality oracle, the `_classify_play_violation` reason classifier and `play_trick`'s `IllegalPlayError` raise on an illegal card, the belote tracking helpers, and the auction-driven integration test that the human seat is never prompted when their partner has doubled. The lifecycle path (`manage_bidding` end-to-end / `play_all_tricks` / `calculate_round_scores`) is still un-tested past that single integration scenario — backfill needed.
- The screen `Panel`/`Table` *builders* (now pure functions under `view/screens/` and `view/layout.py`, no longer `RichView` methods) are lightly covered: title/text smoke tests for the round panel, bidding-history, event-log, recap tables, and the diamond's belote badge, in the matching `test_view/test_*.py` files. Layouts that aren't asserted on are still validated by `uv run contrai` smoke-running.
- `GameController` is still the lone surviving stub (see the grey box on the class diagram). It references undefined `pygame` and isn't wired to `Game` / `Round` — open question whether to delete it now that the Rich CLI doesn't need a separate controller, or keep it for a future GUI path.
- Sweep the rule-based strategy private helpers for any remaining `contract[…]` indexing residues — the four visible call sites were fixed during CLI work but a defensive pass through `rule_based.py` would not hurt.
- `_check_double_redouble` in `RuleBasedBiddingStrategy._choose_wire` still uses the legacy-format `last_bid` to detect a prior Double; the inner check `last_bid == 'Double'` shadows the earlier `isinstance(last_bid, tuple)` guard so AI redouble is effectively gated on a `TODO`. Worth revisiting alongside the next AI bidding refactor — the cleanest fix is to read `auction.has_double` / `auction.has_redouble` directly instead of inferring from the wire history.
- The rule-based strategy consumes `Auction.legal_actions(self)` indirectly today (through the wire-format adapter). A future cleanup should make the expert helpers (`_get_last_bid`, `_get_partner_bid`, `_check_double_redouble`) query the Auction directly and let `wire_to_bid` / `bid_to_wire` retire.
