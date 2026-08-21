# contrai-engine

Game engine for Coinche / Contrée. Model–View architecture (the controller role is the `cli.py` game loop plus the view hooks today), sits on top of `contrai-core` for all shared types.

## Layout

Source at `packages/contrai-engine/src/contrai_engine/`:

- `model/` — engine-side model layer:
  - `player/` — the player subpackage (all classes extend `BasePlayer` from `contrai-core`); `player/__init__.py` re-exports the public names so external imports (`from contrai_engine.model.player import …`) are unchanged:
    - `base.py` — `Player` (abstract) and `HumanPlayer`
    - `strategy.py` — the `BiddingStrategy` / `CardPlayStrategy` abstract interfaces and the `PlayerStateMixin` mix-in (live read access to the owning player's `hand` / `team` / `position`)
    - `rule_based.py` — `RuleBasedBiddingStrategy` / `RuleBasedCardPlayStrategy`, the first concrete level (the expert `SF-09` / `SF-10` rules)
    - `ai.py` — `AiPlayer`, which injects a bidding and a card-play strategy and delegates to them
    - `levels.py` — the `AI_LEVELS` registry + `make_ai_player()` factory for difficulty selection
  - `game.py` — `Game` (fires `view.on_round_dealt(...)` after the deal and `view.on_all_pass_redeal(...)` when nobody contracts). Carries the `RuleConfig` handed in at construction (`Game(players, rules=…)` → `Round` → `PlayState` / `score_round`); defaults to `RuleConfig()`, and nothing reads a knob of it yet
  - `round/` — the round subpackage (the lifecycle orchestrator, the pure scoring transformation it calls, and the §7.2 arithmetic that transformation delegates to); `round/__init__.py` re-exports `Round` / `RoundScore` / `UnannouncedSlam` / `Mark` / `contract_components` / `marked_total` so external imports (`from contrai_engine.model.round import …`) are unchanged:
    - `round.py` — `Round`, the lifecycle orchestrator: deal → `manage_bidding` → `play_trick` / `play_all_tricks` → the thin `calculate_round_scores` wrapper, plus the inline belote/rebelote helpers (`_belote_suits`, `_detect_belote_pairs`, `_is_belote_event`, `_transition_belote_state`, `_scoring_belotes` and the derived `belote_counts_by_side`). The trick loop is driven by the immutable core `contrai_core.play.PlayState` — seeded at the start of play by `play_all_tricks` (or lazily by `play_trick` when driven directly) — which owns whose turn it is, the legal cards, each seat's remaining hand, the completed tricks and their winners, and what each side has captured. `Round` keeps exactly one mirror of it — `_sync_hands` re-extends every `player.hand` after each play — so the view keeps reading the classic engine objects; every other question about the play phase is asked of `play_state` directly. AI seats instead read the frozen `PlayObservation` projected from that same state. Publishes `view.on_bid_made(...)`, `view.on_contract_established(...)`, `view.on_card_played(...)`, `view.on_trick_complete(...)`, and `view.on_belote_announced(...)` so the view can pace and narrate AI turns. Takes the table `RuleConfig` from the `Game` (`rules=` on the constructor, defaulting to `RuleConfig()`), runs the auction under it (`Auction.empty(rules=self.rules)`, so the offered trump choices and each mode's ladder top are the table's call), seeds it into both the validated and the lazy `PlayState`, names it to the scorer, and reads `all_trump_belote` off it to decide how many of the K + Q pairs a deal holds actually mark
    - `scoring.py` — the pure `score_round(round) -> RoundScore` transformation. Its own job is reading the played-out round and judging made/failed; the arithmetic it then feeds is `components.py`'s. Also `sweep_substitute` (the flat amount an unannounced sweep puts in place of its pile), `count_player_tricks` and the `UnannouncedSlam` outcome tag. The table ruleset is read off `round.rules` rather than taken as an argument, so a round can only be scored under the ruleset it was played under
    - `components.py` — the §7.2 component arithmetic: `Mark(made, announced)`, `contract_components(...)` which splits a round into one `Mark` per side, `marked_total(mark, multiplier, rules)` which reduces a `Mark` to the number written down, and `FLAT_FAILURE_PILE`. Pure arithmetic over ints and a `RuleConfig` — no `Round`, no `Contract`, no `PlayState`
- `view/` — the terminal UI, split into focused modules (see [CLI](#cli) below):
  - `rich_view.py` — `RichView`, the stateful orchestrator: console + per-game state, the engine hooks (`request_*_action`, `on_*`, `show_*`), the input loops, and `_render_in_game` (the single seam that pulls state off `self` and feeds the pure builders). `RoundSummary` lives here too. Re-exported from `view/__init__.py`, so both `from contrai_engine.view.rich_view import RichView` (used by `cli.py` / `model/game.py`) and `from contrai_engine.view import RichView` work.
  - `theme.py` — design tokens (colour palette) and lookup tables (target-score options, `POSITION_SHORT` seat labels, `TEAM_ABBR` side labels, `TRUMP_GLYPH` / `TRUMP_LABEL` contract-trump tags and names, bid aliases, valid contract values). The team labels here are *presentation only* — identity is `TeamSide`, so rewording one cannot break a score lookup. `TRUMP_GLYPH` gives the two suitless variants the two-letter tags `NT` / `AT` rather than falling through to the enum value (`"NoTrump"`, 7 cells), which overflowed the bidding history's 11-cell bid column
  - `formatting.py` — stateless text / glyph / label builders (seat & suit labels, the shared contract / trump labels, and the compact `Bid` label)
  - `parsing.py` — human-input parsers (`_parse_bid_input`, `_parse_card_input`)
  - `bidding_rules.py` — the messaging-only `_illegal_bid_reason` mirror of the auction rules (the specific nudge shown when a human types an illegal bid); the adaptive prompt hint is derived directly from `Auction.legal_actions`
  - `state_helpers.py` — small game-state readers (`_current_winner`, `_explain_constraint`, `_sort_hand_for_display`, `_belote_by_position`, `_resolve_delay`)
  - `layout.py` — cross-screen layout (`_two_column`, the Prompt panel, the event-log panel, and the Game-score panel shown in every in-game frame's top-left)
  - `screens/` — one module per screen of the five-screen design: `landing.py`, `bidding.py`, `trick.py`, `recap.py`, `endgame.py`. Each exposes pure `(data) -> Panel/Text` builders; `RichView` composes and prints them.
- `cli.py` — `contrai` console-script entry point: landing → game-loop → end-game; also parses the three debug-mode flags and the two mutually exclusive ruleset flags `--rules FILE` / `--preset NAME` (see [CLI](#cli))
- `options.py` — `DebugOptions`, the frozen value object the `--debug` / `--seed` / `--autoplay` flags parse into. Stdlib-only, read by the CLI and the view, **never** by the model
- `ruleset.py` — TOML ⇄ `RuleConfig`: `parse_ruleset` / `load_ruleset` / `dump_ruleset`, the `SECTIONS` layout table, `resolve_rules` (the `--rules` / `--preset` resolution) and `RulesetError`. Stdlib `tomllib`, so no new dependency; loading lives here because core stays I/O-free
- `log_setup.py` — the one place that attaches a logging handler: a DEBUG-level `FileHandler` on the `contrai_engine` / `contrai_core` package roots, and a no-op unless `--debug` is set
- `debug_state.py` — Rich-free plain-text projections shared by the debug view and the log (`sort_cards_trump_first`, `cards_still_in_play`, `hand_snapshot`, `deal_lines`, `round_result_lines`)
- `tests/` — pytest suite (`test_model/`, `test_view/`)

Everything else (`Card`, `Deck`, `Hand`, `Suit`, `TrumpVariant`, `Rank`, `Bid`, `Contract`, `Trick`, `Team`, exceptions) is imported directly from `contrai_core`. There are no back-compat re-exports under the engine namespace anymore.

## Class structure

```plantuml format="svg" source="class_engine.puml"
```

`Player` extends `BasePlayer` from `contrai-core` (drawn as a blue boundary element). The two concrete subclasses are `HumanPlayer` (whose `choose_bid` / `choose_card` still return `None` — the `RichView` is what actually services human input through `Round`'s `view.request_*_action` hooks) and `AiPlayer`, which holds an injected `BiddingStrategy` and `CardPlayStrategy` and delegates to them. There is no standalone controller class today: the controller role is the `cli.py` game loop plus the view hooks `Round` calls. `RichView` is the live engine view; the old `CliView` placeholder has been removed. See [Diagrams](../diagrams/) for the colour convention.

## AI players

`AiPlayer` owns no strategic logic of its own. It holds two strategy objects behind the abstract `BiddingStrategy` / `CardPlayStrategy` interfaces (`strategy.py`) and routes `choose_bid` / `choose_card` to them. Strategies are supplied at construction as **factories** (`player -> strategy`, i.e. the strategy class itself), resolving the chicken-and-egg of a strategy that needs a back-reference to the player while the player is still being built; the `PlayerStateMixin` mix-in then gives each strategy live read access to the player's `hand` / `team` / `position`. The defaults reproduce today's bot, so `AiPlayer("Bot", "South")` is unchanged.

The first concrete level is the rule-based pair (`rule_based.py`): `RuleBasedBiddingStrategy` implements the expert bidding table (80–160 plus Slam and Solo Slam) and `RuleBasedCardPlayStrategy` the card-play strategy from the functional specs (`SF-09`, `SF-10`). Future AI levels (MCTS, learned policies — AI roadmap §6) are new strategy classes, never edits to `AiPlayer`; a thin `AI_LEVELS` registry + `make_ai_player(name, position, level="expert")` factory (`levels.py`) gives ergonomic difficulty selection on top, while the raw `AiPlayer(..., bidding=…, cardplay=…)` form stays available for mix-and-match (e.g. rule-based bidding + a learned card-play). The strategy object is also the natural home for a future explainability rule-trace (§6.1). `choose_card` takes a single frozen `contrai_core.PlayObservation` — the seat's own hand, the public trick history, the contract/auction, and its legal cards right now — projected by `PlayState.observe()`. Because that observation is the *only* input a card-play strategy ever receives, and every seat it names is a bare `Position` (the observer, the `ObservedPlay` trick records, the `ObservedContract`, each `Bid[Position]`), a strategy is sealed off from another seat's hand by construction — no live player object, and therefore no hand, is reachable by *any* path from what it is handed. `RuleBasedCardPlayStrategy` is stateless between calls: it derives whatever card tracking it needs (which cards have fallen, which seats are known void in trump — keyed by `Position`, with partner/opponent checks done as seat arithmetic on `Position.partner` / `Position.opponents`, and "did my side declare this?" as `Position.is_teammate(contract.declarer)`) by replaying the observation's public trick history fresh on every turn (`_derive_tracking`), rather than carrying counters across calls or rounds. The trump-void inference stays compelled-only — a seat discarding behind its master partner is *not* marked void, since that discard is voluntary; on a trump lead the proof is unconditional, because holding trump forces playing it.

When the AI's team is currently winning the trick (`_play_when_team_winning`) and the AI cannot follow the led suit, the rule is *don't waste trumps*: prefer a non-trump discard over playing a trump card, even though a trump would add more points to the pile. Within the non-trump discard pool the AI prefers non-master cards (preserving cards that can still win their suit later) and picks the highest-points to maximise this trick's value. Only when the hand has nothing left but trumps does the AI play one — and it picks the lowest trump in that case, so the Jack or 9 of trump aren't dumped onto an already-won trick.

**Discarding is by cost, not by suit length.** When the seat can neither follow nor usefully ruff, it gives up its *cheapest* card outright. The previous rule emptied whichever suit the seat happened to be shortest in, which is a shape heuristic answering a points question: it would walk a short suit down to its Ten while a worthless 7 sat untouched in a longer one, handing the opponents 10 points for nothing. Measured over 300 rounds, discarding by cost concedes **36% fewer points** on those discards and halves the ten-or-better cards thrown away. Ties break to the longest suit, then to a draw — the draw matters because a deterministic tie-break makes a seat's discards readable from its hand order, which is information the opponents should not get for free.

**Trump is held back against trump-void opponents.** Leading a later trick with both opponents proven out of trump, the seat searches only *plain* suits for the ace or master to cash: an unbeatable trump is worth more kept than spent, since nobody can take it off the seat later. Previously the trump ace went out whenever trump happened to be the seat's longest suit, and a hand of nothing but trump led its ace rather than its cheapest card. The inference is also symmetric — holding trump back against trump-void opponents is worth exactly the same on either side of the contract — but the seat used to ask the question only when its *own* side had declared, so defenders never got the benefit. Both sides run it now.

## CLI

`uv run contrai` (or `python -m contrai_engine.cli`) launches a six-screen Rich-based terminal UI driven by `RichView` and wired in `cli.py`:

| Screen        | Trigger                                 | Notes                                                                                                                                                                                       |
| ------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Landing**   | program start, `n` from end-game        | Target-score picker (500 / 1000 / 1500 / 2000 / 3000). Hardcoded seating today: South = `HumanPlayer`, N/E/W = `AiPlayer` (expert — the default).                                                         |
| **Bidding**   | `Round.manage_bidding` → human turn     | Game-score + Round panels (title shows `Round #N`), bid history with ` - ` separator between bidding rounds, hand + prompt. Accepts `pass`, `double`, `redouble`, `<value> <suit>` (English only — the FR aliases `coinche`/`surcoinche` are rejected). When an opponent has doubled the contractor's contract, the hint switches to `(pass / redouble)`. When the player's **partner** has just doubled (or redoubled), the prompt is skipped entirely and the engine auto-passes them — pass is the only legal action and the human shouldn't have to confirm it. |
| **Mid-trick** | `Round.play_trick` → human turn         | Diamond seating (N top, E right, S bottom, W left). Live winner gets the gold pill. Hand row dim/green for legal vs. illegal plays. Once the holder plays a K or Q of trump, a persistent `★ Belote` badge appears under their seat for the rest of the round (the Belote / Rebelote distinction is kept in the event log). |
| **Trick won** | `Round` fires `view.on_trick_complete`  | Four-card diamond with the winner highlighted; "Press [Enter] to continue…". The hook is gated on `hasattr(view, 'on_trick_complete')`.                                                     |
| **Round recap** | `cli.py` calls `view.show_round_recap` after `view.on_round_complete` | Between-round panel: contract (or "All passed"; the label names the taker and any Coinche/Surcoinche caller, spelled out verbose as `doubled`/`redoubled` here — see below), a `Trump:` recall line (the contract suit, since the contract label omits it), made / failed badge, then **two stacked sub-tables** sharing the N-S / E-W columns. The **Outcome** table reports the factual play tally — `Tricks won` (count) and `Round points` (trump-aware pile + last-trick 10 + belote 20 each side captured, always the real total regardless of scoring shape). The **Scoring** table is that round's mark broken into §7.2's two components as the table marked them — `Contract` (the announced-points component, carrying the double/redouble multiplier wherever the ruleset puts it) and `Round points` (the made-points component plus the belote) — then a divider and the `Round score` subtotal, which the two rows add up to by construction. A doubled or failed round therefore reads as its real shape (`Contract 200` beside `Round points 160`) rather than hiding the flat 160 pile inside a row labelled for the contract; the Outcome table above still surfaces the points genuinely captured. A final `Running` line carries the game totals and target, its numbers aligned under the team columns. Waits for Enter; shown after *every* round — when the same round just crossed the target, the prompt flips to "Press [Enter] to see the final score…" and the end-game banner is the next screen. When both teams end the round **level at/above the target** (sudden death), the panel closes with a gold "tiebreaker round follows" notice and the prompt reads "Press [Enter] to deal the tiebreaker round…". |
| **Game over** | `Game.check_game_over(target)` true — one team strictly leads at/above the target (a tie there keeps the game running with tiebreaker rounds) | Double-line gold banner, round-by-round summary table. `[n]` new game · `[r]` rematch · `[q]` quit.                                                                                          |

Every in-game screen also carries a rolling **event log** panel (5 lines, "Log") slotted between the hand and the prompt. It captures the last few engine events — deal, all-pass redeal, every bid, the *contract-set* bookmark when bidding ends on a deal, every card play, every trick winner, belote / rebelote announcements — so the user always sees the narrative continuity, even when AI players act faster than they can read.

Per-round summaries shown on the end-game scoreboard are tracked **view-side** (`RichView.history: list[RoundSummary]`), so `Game` itself stays free of UI state.

**Contract label.** A single helper (`_format_contract_short`) renders the contract everywhere it appears — the in-game Round panel, the round recap, and the event-log *contract-set* line. It reads `VALUE by <taker>  ×2/×4 by <caller>`, e.g. `110 by E  ×2 by S`: the taker (`contract.player`) and any Coinche/Surcoinche caller are shown as single-letter seats colored by team (blue N-S, orange E-W). The caller identities ride on `Contract.double_player` / `redouble_player`, which `Auction.contract()` lifts off the bid history when it materialises the contract; the multiplier still renders if the caller is unknown. The recap passes `verbose=True`, which spells the markers out in full prose (`110 by N  doubled by E`, `120 by N  redoubled by N`) since the after-round summary has the room and reads better than the compact glyph.

**Pacing.** AI bids and card plays each fire a view hook that re-renders the state with the new action visible, then sleeps for a tunable interval before the next player acts. Defaults: `1.4 s` between bids, `0.9 s` between card plays and after a belote announcement. Override via env vars (any positive float; garbage falls back to the default, negatives clamp to zero):

```bash
CONTRAI_AI_BID_DELAY=0.5  CONTRAI_AI_CARD_DELAY=0.3  uv run contrai
```

**Debug mode.** Three orthogonal flags, parsed by `cli.py` into a single frozen `DebugOptions` (`options.py`):

```bash
uv run contrai --debug              # face-up hands + diagnostics to contrai-debug.log
uv run contrai --seed 42            # reproducible deal and dealer rotation
uv run contrai --autoplay           # one unattended game, an AI at every seat
```

`DebugOptions` is stdlib-only and readable from the CLI (which parses the flags) and the view (which decides whether to draw face-up hands) — but **never from the model layer, which stays unaware that a debug mode exists at all**. `DebugOptions()` with no arguments reproduces normal runtime behaviour exactly, so the whole feature is off by default rather than branched around.

Seeding is *generate-then-seed*: an explicit `--seed` always wins and is applied as-is; absent one, `--debug` generates a fresh seed, applies it, and records it back onto the options — so a debug run is reproducible *after the fact*, even when the user didn't think to ask for it. With neither flag set the global RNG is left untouched, so a normal run is bit-for-bit what it was before the feature existed.

Logging is treated as infrastructure, not presentation: model and view code only ever emits through the standard `logging` module, and no module attaches a handler or sets a level itself. `log_setup.py` is the one place that does — a no-op unless `--debug` is set, at which point a single DEBUG-level `FileHandler` (writing `contrai-debug.log`, overwritten per run, UTF-8) is attached to both the `contrai_engine` and `contrai_core` package-root loggers. `debug_state.py` holds the Rich-free plain-text projections the debug view and the log share (`sort_cards_trump_first`, `cards_still_in_play`, `hand_snapshot`, `deal_lines`, `round_result_lines`), so the diagnostics render identically to a terminal panel and to a log file.

Under `--autoplay` every screen that would block on Enter pauses on a timer instead, each with its own override: `CONTRAI_AUTOPLAY_PAUSE` (trick won, 1.2 s), `CONTRAI_AUTOPLAY_RECAP_PAUSE` (round recap, 2.5 s), `CONTRAI_AUTOPLAY_LANDING_PAUSE` (1.2 s) and `CONTRAI_AUTOPLAY_ENDGAME_PAUSE` (2.0 s). Zeroing all four alongside the two AI delays is the workspace's unattended smoke test — it exercises the real wiring end to end, which is what covers the pure-Rich rendering that unit tests deliberately skip:

```bash
CONTRAI_AUTOPLAY_PAUSE=0 CONTRAI_AUTOPLAY_RECAP_PAUSE=0 \
CONTRAI_AUTOPLAY_LANDING_PAUSE=0 CONTRAI_AUTOPLAY_ENDGAME_PAUSE=0 \
CONTRAI_AI_CARD_DELAY=0 CONTRAI_AI_BID_DELAY=0 \
uv run contrai --autoplay --seed 42
```

**Rulesets.** Two mutually exclusive flags pick the table rules the game is built under:

```bash
uv run contrai --preset classic        # a named built-in ruleset (today: classic = the §9 defaults)
uv run contrai --rules table.toml      # a TOML ruleset file laid out as contree-domain.md §9
```

A file is a *partial override* on the built-in defaults: it names only the knobs that differ, and every missing key keeps its §9 value. What it does name is checked strictly — an unknown section, an unknown key, a value of the wrong type or an unknown enum token is a usage error (exit code 2) rather than a warning, because a typo'd knob that silently kept its default would corrupt a logged experiment. A well-formed file that names an *impossible* table (neither marking convention on, or a target score off the 500–5000 ladder) is rejected by core's `InvalidRuleConfigError`, surfaced through the same usage error. `ruleset.py` also writes the format back (`dump_ruleset`), so the ruleset a simulation actually ran under can be archived next to its results:

```bash
uv run python -c "from contrai_core import RuleConfig; from contrai_engine.ruleset import dump_ruleset; print(dump_ruleset(RuleConfig()), end='')" > table.toml
```

**Trump choices.** No trump and all trump are off by default (`contree-domain.md` §9.2). Turn them on with a ruleset file:

```toml
[trump]
extended_trump_choices = true
all_trump_belote       = "single"   # none / single / four
```

```bash
uv run contrai --rules table.toml
```

At the prompt they are `nt` and `at` — `"100 nt"`, `"240at"`, and the spelled-out `no-trump` / `all-trump` also parse. Each mode has its own ladder top: 180 at a suit, 160 at no trump, and 160 / 180 / 240 at all trump depending on the belote regime. The bid prompt advertises only what is actually legal, so a variant already past its ceiling stops being suggested, and a rejected bid says which limit it crossed rather than "not legal here".

**Belote regimes.** A belote is a King + Queen held in the same hand, in a suit that is trump. That makes it a non-question outside all trump: a suit contract has exactly one trump suit and so at most one pair in the deal, and no trump has none at all. All trump makes every suit trump, so one deal can hold up to four pairs — and one seat can hold two of them. `all_trump_belote` picks what happens then:

| Regime | Effect |
| --- | --- |
| `none` | No belote exists. Nothing is detected and nothing is announced — not "announced but worth 0". |
| `single` (default) | Every pair is detected and every holder announces, but only **the first pair announced in play** marks its 20, whichever side holds it. |
| `four` | Every pair marks. A side holding three of them marks +60. |

`single` resolves by **announcement order, not seat order**: `Round.belote_order` records each pair the first time one of its two cards is played, and its head is the pair that marks. Nothing about the seating or the deal decides it — the same two hands can give the 20 to either side depending on who plays their King first.

Two consequences worth knowing. Belote counts toward *realized* points, not just toward the mark, so under `four` a declarer taking nothing in cards still makes an 80 contract on four pairs alone — which is why all trump's ladder reaches 240 there. And the round tracks announcements per `(holder, suit)` pair rather than per holder, so a seat mid-announcement in two suits at once is representable; the trick diamond still shows that seat one badge, the strongest kind it has reached.

**No knob changes behaviour yet.** The flags resolve, validate and reach `Game`, which threads the config down to `Round`, `PlayState` and `score_round` — and nothing consults a field of it. That is deliberate: this step is the backbone, and each knob is wired to behaviour on its own in a later step.

**Play legality at the play boundary.** Card-play legality now lives entirely in `contrai_core.play.PlayState`: `Round.play_trick` reads the active player and their legal cards off `play_state.to_act` / `play_state.legal_actions(player)`, then advances the state with `play_state.apply(Play(player, card))`. `apply` is the single legality-enforcing transition — it checks turn order, then hand membership, then the follow/trump obligations — and raises `IllegalPlayError` (carrying the offending card, a `PlayRuleViolation` reason, and the legal alternatives) on an out-of-turn, not-held, or obligation-breaking play, instead of being **silently corrected** to a legal fallback. Both `AiPlayer.choose_card` (fed the `PlayObservation` projected from that same state) and `RichView.request_card_action` are contracted to only ever return a card from the legal set, so the raise is a safety net surfacing wiring bugs (cf. the `AiPlayer` cleanup in the open work) rather than a path hit in normal play — the headless 4-AI smoke run confirms it never fires.

**Bid legality at the input boundary.** `request_bid_action` parses raw input for *shape* (`_parse_bid_input`) and then validates it against `Auction.is_legal` before returning. An illegal-but-parseable bid — e.g. doubling your own partner's contract — re-prompts with a specific reason (`_illegal_bid_reason`) instead of escaping to `Auction.apply` and crashing the CLI. The model keeps its strict hard-raise contract; the human-input layer is where unvalidated input is filtered. The bid prompt hint is likewise adaptive, and reads straight off `Auction.legal_actions(player)`: `double` / `redouble` are only advertised when a `DoubleBid` / `RedoubleBid` is in the seat's legal set, and the worked contract example is the cheapest legal `ContractBid` value on offer (`100 H` once a `90` stands, not the bare `80` floor) — dropped past `180` where only Slam (a non-integer value) outranks the standing contract.

The pure helpers (bid parser, card parser, hand sorter, current-winner, constraint hint, illegal-bid reason, delay resolver, compact `Bid` label) are module-level functions in their respective modules (`parsing`, `state_helpers`, `bidding_rules`, `formatting`), and the per-screen `Panel` / `Table` builders are pure functions under `screens/`. The test suite mirrors that split — `tests/test_view/test_{formatting,parsing,bidding_rules,state_helpers,layout,recap,endgame}.py` test the extracted modules, while `test_rich_view.py` keeps the stateful `RichView` behaviour (hooks, input loops, in-game frame). The shared `four_players` fixture lives in `tests/test_view/conftest.py`. The deepest `Panel` / `Table` layouts not asserted on are validated end-to-end by smoke-running `uv run contrai`.

```mermaid format="svg" source="state_cli_screens.mmd"
```

The screen flow above is rendered from [`state_cli_screens.mmd`](../diagrams/state_cli_screens.mmd) — the canonical source — and shows every transition the view drives, including the `on_trick_complete` callback edge and the new between-rounds recap.

See the [Rich TUI design handoff](../../ContrAI%20CLI/design_handoff_contrai_tui/README.md) for the visual spec, including all five SVG mockups (the design predates the recap screen and the event log panel, both of which build on top of the same vocabulary).

## Game loop

```plantuml format="svg" source="seq_cli.puml"
```

`cli.py`'s `main()` is the controller role today — there is no separate controller class. It builds a `RichView`, asks for a target score on the landing screen, then runs two nested loops: an outer **new-game / rematch** loop (each iteration `_build_game()`s a fresh `Game` — South `HumanPlayer`, N/E/W expert `AiPlayer` — and `view.attach`es it) and an inner **round** loop that runs while `game.check_game_over(target).game_over` is false. Each round tick calls `game.manage_round(view=view)` (detailed in [Round lifecycle](#round-lifecycle)), then `view.on_round_complete` to feed the scoreboard and `view.show_round_recap(..., is_final=…)` to block on the between-round panel. When the game is over, `view.show_end_game(status)` returns `'q'` (break), `'n'` (re-prompt the landing target), or `'r'` (rematch on the same target). A top-level `try/except (KeyboardInterrupt, EOFError)` turns Ctrl-C / Ctrl-D into a graceful "Goodbye." rather than a traceback.

## Round lifecycle

```plantuml format="svg" source="seq_round.puml"
```

The end-to-end flow of `Game.manage_round`: setup (deal, dealer rotation, players_order, `view.on_round_dealt` notification) → bidding (delegated to `Round.manage_bidding`, which establishes the contract and snapshots every K + Q pair held) → eight tricks (`Round.play_all_tricks`) → scoring (`calculate_round_scores`, with belote +20 and the last-trick bonus +10, applying the double / redouble multiplier). The failed-contract branch (everyone passed) records zero scores, redistributes cards, and fires `view.on_all_pass_redeal`. `manage_round` returns nothing — it mutates the `Game`/`Round` in place (`current_contract`, `scores`) and the caller reads the outcome off those. After each `manage_round`, `cli.py` calls `view.on_round_complete` and then `view.show_round_recap(round_, scores, is_final=…)` — shown for every round, including the one that just clinched the game (the prompt flips to "see the final score…" so the end-game banner is what follows).

The two zoom diagrams below break out the dense parts.

??? note "Bidding cycle zoom — `Round.manage_bidding`"

    ```plantuml format="svg" source="seq_bidding.puml"
    ```

    The bid loop drives a `contrai_core.Auction` through `itertools.cycle(players_order)`. Each turn looks up `auction.legal_actions(player)`; when the only legal action is `PassBid` (partner just doubled or redoubled, or a pass closed the redouble window) the engine auto-applies it without prompting the player or the view. Otherwise `_gather_bid` consults `player.choose_bid(auction)` and — for the human seat — `view.request_bid_action(player, auction)`, both of which now return real `Bid` instances. The chosen bid is applied via `auction.apply(bid)`, which raises `IllegalBidError` rather than silently downgrading an illegal bid to a Pass. After every commit Round fires `view.on_bid_made(player, bid, history)` so the view can log the action and pause for AI bidders. Once `auction.is_terminal()`, the final `Contract` is materialised by `auction.contract()` and `Round._detect_belote_pairs()` scans every seat for a K + Q pair in each of the round's belote suits — the trump suit at a suit contract, none at no trump, all four at all trump. The suits come from `rules_for(trump).belote_suits`, so they are always real card suits and the scan never builds a card in a suit no deck holds. The AI's expert table and the Rich view's renderer both operate on typed `Bid` objects end to end — the legacy wire format (`'Pass'` / `'Double'` / `'Redouble'` / `(value, suit)`) and its `wire_to_bid` / `bid_to_wire` bridge have been retired.

??? note "Single trick zoom — `Round.play_trick`"

    ```plantuml format="svg" source="seq_trick.puml"
    ```

    Leader determination is implicit in `play_state.to_act` (trick 0 is led by `players_order[0]`; every later trick by the previous winner). For each of the four plays: `play_state.to_act` / `play_state.legal_actions(player)` supply the active player and legal cards → the human seat is asked via `view.request_card_action`, an AI seat via `choose_card(play_state.observe(player, bids=...))` (the frozen `PlayObservation` is the only thing the strategy ever sees), any other seat falls back to the first legal card → `play_state = play_state.apply(Play(player, card))` advances the authoritative state (raising `IllegalPlayError` on an out-of-turn, not-held, or obligation-breaking card — no silent correction) → `_sync_hands` re-mirrors every seat's `Hand` from the new state → `view.on_card_played(player, card, plays)` → optional `view.on_belote_announced(player, kind, round_)` when a seat plays a K or Q of a pair it holds. Once all four plays land: `view.on_trick_complete(plays, winner, round_)` callback (each hook is gated on `hasattr(view, …)`, so non-Rich callers stay unaffected). The `plays` the hooks receive are core `Play` records — a `(player, card)` `NamedTuple`, so an in-progress trick and a completed `TrickRecord` read alike. Two subtleties to know. `Round._trick_after_play` normally hands over `play_state.current_trick`, but after a trick's fourth card the state has *already* closed it — that view is empty again and the trick sits in `completed_tricks[-1]`, which is precisely the frame the view must draw, so the just-closed trick is handed back instead. That choice is only sound *after* an `apply`: before one, an empty current trick means the seat is on lead, which is why the human prompt reads `play_state.current_trick` directly. And `PlayState.legal_actions` (contrai-core) correctly forces over-trump when trump is led and keys the partner exemption on the *current master* of the partial trick — see the legality note at the foot of the diagram.

### Scoring

`Round.calculate_round_scores` is a thin wrapper around `scoring.score_round(self)`, the pure `round → RoundScore` transformation: it reads the played-out round and publishes the whole result onto `Round.round_score`. `round_scores`, `contract_made` and `unannounced_slam` are read-only properties over that one object rather than copies of it, so they cannot drift from the score they describe — and an all-passed round publishes a `RoundScore` too, carrying `contract_made=None` so the recap reads the redeal as "no contract" rather than as a failure.

#### Two components

**A mark is a sum of two components** (`contree-domain.md` §7.2): the *made points* the trick pile is worth to a side, and the *announced points* the contract itself is worth. `components.contract_components(...)` splits a round into one `Mark(made, announced)` per side; `components.marked_total(mark, multiplier, rules)` reduces a `Mark` to the number actually written down, applying the §7.3 marking conventions and placing the double/redouble multiplier. Every round's score is `marked_total(mark, M, rules) + belote`.

That decomposition is what the four scoring shapes the engine used to branch on really are. A made un-doubled numeric contract is `Mark(attack_pile, C)` against `Mark(defense_pile, 0)` — the only shape where both sides mark a pile. A failure hands the defense `Mark(160, C)`: the pile is taken whole, so its 162 is written as a flat 160 rather than counted out (`FLAT_FAILURE_PILE`). A doubled round flattens the pile the same way and gives it all to the winner. A Slam-family contract, and an un-doubled unannounced sweep, replace the pile with a flat *substitute* — the 250 / 500 base — which absorbs the 152 cards and the 10-point last-trick bonus alike. Writing those as one expression each, instead of a branch each, is what lets the §9.6 scoring knobs land as independent switches rather than as a fifth and sixth branch.

`RoundScore` carries the components (`marks`) alongside the totals, plus the belote credited (`belote_points`), the raw piles the contract was judged on (`card_points`, last-trick bonus included), `last_trick_side` and the `multiplier` — everything needed to reduce `marks` back to `scores`.

#### Judging the contract

Made-ness is decided before any component is computed, and the two are kept apart deliberately: §7.4's rounding moves the *marks*, never the verdict.

A Slam-family contract is judged on **tricks alone** — the declaring team took all 8 (and, for a Solo Slam, the bidder personally did). Points are never consulted, so none of the switches below reach it. An unannounced sweep is likewise made outright: taking every trick cannot fail.

Every other numeric contract is judged on points, and by default on **two** tests (`contree-domain.md` §7.5):

```text
P_att = attack pile + attack belote      (belote iff belote_counts_toward_contract)
P_def = defense pile + defense belote
made  = P_att >= C  and  P_att > P_def   (the second iff attack_must_outscore_defense)
```

`attack_must_outscore_defense` is **on** by default, which is a deliberate change from `v0.3.0`: reaching `C` used to be sufficient. The second test is what settles the *dispute* §7.5 names — an exact tie fails the contract, so no separate knob is needed. The three splits that produce one are 81/81 on cards alone, 91/91 with a single belote (out of 182), and 101/101 with a belote on each side under the all-trump *four* regime (out of 202). Set `attack_must_outscore_defense = false` for the older behaviour.

`belote_counts_toward_contract` (on by default) decides whether the +20 counts toward *both* tests. Switched off it is dropped from each side symmetrically, which can cut either way: a declarer that needed its belote to reach `C` now fails, while a declarer that was being out-scored only because the *defense* held a belote now makes it.

`belote_lost_when_contract_fails` (off by default) is applied after the verdict and before the components: a failing declarer's belote moves to the defense. The transfer is one-directional — §6.6 is explicit that a defending team's belote is never taken — and every pair the declarer holds moves together, up to the four an all-trump round can award. Because it runs after the judging, a belote that carried the contract home is never the one that moves.

#### Which components a table marks

Two §9.6 switches decide which of the two components a table actually writes down — `mark_made_points` and `mark_announced_points`. At least one must be on; a `RuleConfig` with both off is rejected at construction, since such a table would keep no score at all. For an un-doubled round the three legal combinations mark (`contree-domain.md` §7.3):

| Active conventions | Made contract | Failed contract |
| --- | --- | --- |
| Both (default) | declarer `C + P_attack`; defense its own points | defense `160 + C` |
| *Made points* only | declarer `P_attack`; defense its own points | defense `160` (Slam-family: the 250 / 500 substitute) |
| *Announced points* only | declarer `C`; defense `0` | defense `C` (Slam-family: 250 / 500) |

The third switch, `only_announced_points_multiplied` (on by default), decides where a double or redouble bites when *both* components are marked: on the announced one alone (`made + announced × M`, so a doubled numeric contract marks `160 + C × M`) or on their sum (`(made + announced) × M`). When only one convention is active the multiplier falls on whichever component survives — otherwise a double would change nothing at all for a table that does not mark announced points. That override makes `only_announced_points_multiplied` inert on a single-convention table, which is why an announced-only table marks `A × M` either way.

#### What a failed or swept round marks

Four §9.6 switches reshape the components without adding a branch to the scorer.

`any_failure_marks_160` (off by default) replaces the announced component `C` with a flat 160 on *every* failure, so an un-doubled failure marks 320 whatever was bid. It is inert on a made contract.

The two `failed_slam_marks_*` switches (both on by default) decide what survives of a *failed* Slam-family contract. With `failed_slam_marks_made_points` off, the failed Slam's made component falls back to the ordinary flat 160 instead of its 250 / 500 substitute. `failed_slam_marks_announced_points` protects the announced component from `any_failure_marks_160` — which is why §9.6 documents it as inert while that switch is off: with `any_failure_marks_160` off, the announced component of a failed Slam is already `C`, and `C` *is* the 250 / 500. Both switches are named for the Slam family and never reach a numeric contract.

`unannounced_slam_substitute` (on by default) decides whether an *un-declared* sweep marks the flat 250 / 500 its tag names or its real 162 of cards. Switched off, a sweep scores like any other made contract — `C + 162` rather than `C + 250` — but it is still tagged, because `UnannouncedSlam` classifies what happened, not what it is worth, and the recap still calls it a Slam. The knob is named for the *unannounced* sweep: a declared Slam always marks its substitute.

`view/screens/recap.py::_recap_breakdown` is exactly that reduction, and nothing more: it reads `round.round_score`, feeds each half of every `Mark` back through the same `marked_total` the scorer used, and emits the rows the panel prints. There is no second implementation of §7.2 in the view, so a §9.6 knob reaches the recap the moment the scorer honours it. The split is exact because both §7.3 marking conventions are linear in the components — `marked_total(Mark(a, 0)) + marked_total(Mark(0, b))` is always `marked_total(Mark(a, b))` — which is what makes `contract + card_points + belote == round_score` an identity rather than a coincidence to be re-checked per branch.

**Every score is keyed by `TeamSide`.** `RoundScore.scores`, `Round.round_scores`, `PlayState.card_points_by_side` / `trick_counts_by_side`, `Game.scores` and `GameOverStatus.winner` / `tied_teams` / `final_scores` all use the core enum rather than the team's name string, so a winner looks straight up in the final scores and nothing depends on how the view spells a label. Which side won a trick, holds Belote, or declared the contract is read off the seat (`winner.position.team_side`, `contract.player.position.team_side`) rather than the mutable `Team` roster — which also retires the old "winner has no team" guards that silently dropped a trick from the tally. `"North-South"` / `"East-West"` survive only as `Team.name` and as `theme.TEAM_ABBR`, the view's `N-S` / `E-W` scoreboard mapping.
 The pipeline below traces the whole path — the all-passed zero case, judging made/failed (a trick predicate for the Slam family, a points test for a numeric contract), picking the substitute, then the two §7.2 components and their reduction under the table's marking conventions — with the Belote +20 *per pair* to each pair's holder layered on top of every shape.

```mermaid format="svg" source="flow_scoring.mmd"
```

## Open work

- The round test suite mirrors the `model/round/` split (sharing the `players` fixture in `tests/test_model/conftest.py`): `test_components.py` pins the §7.2 grids as pure arithmetic — every table the domain reference prints, reproduced as a parametrize list, with no `Round` and no `PlayState` in sight; `test_round_scoring.py` covers the `scoring.score_round` grid (numeric / unannounced-Slam / doubled / Slam-family) plus a direct `RoundScore`/`score_round` purity check and the `Round.round_score` publishing surface; `test_round.py` keeps the orchestrator concerns — `play_trick`'s `IllegalPlayError` raise on an illegal card (`TestPlayTrickRejectsIllegalCard`), the lazy `PlayState` seeding guard and the validated `play_all_tricks` seeding (`TestPlayStateSeeding`), `_sync_hands` mirroring and card-identity flow-through (`TestSyncHandsMirrorsPlayState`, `TestCardIdentityFlowsFromSeed`), the retained auction (`TestAuctionRetention`), the `PlayObservation` each AI seat is handed (`TestPlayTrickHandsObservation`), the belote tracking helpers, and the auction-driven integration test that the human seat is never prompted when their partner has doubled (`TestManageBiddingAutoPasses`); and `test_round_lifecycle.py` backfills the full end-to-end path — `deal_cards` → `manage_bidding` → `play_all_tricks` → `calculate_round_scores` — on a fully stacked deck with four `AiPlayer` seats and no view, pinning both a scoring invariant and a concrete regression split.
- The screen `Panel`/`Table` *builders* (now pure functions under `view/screens/` and `view/layout.py`, no longer `RichView` methods) are lightly covered: title/text smoke tests for the round panel, bidding-history, event-log, recap tables, and the diamond's belote badge, in the matching `test_view/test_*.py` files. Layouts that aren't asserted on are still validated by `uv run contrai` smoke-running.
- Sweep the rule-based strategy private helpers for any remaining `contract[…]` indexing residues — the four visible call sites were fixed during CLI work but a defensive pass through `rule_based.py` would not hurt.
- The rule-based strategy now reads the `Auction` directly: `choose_bid` / `_choose_open_bid` walk `auction.bids`, resolve the Double/Redouble freeze through `auction.has_double` / `has_redouble`, and the expert helpers (`_get_partner_bid`, `_check_double`) consume and return typed `Bid` objects. The open-bid path evaluates the hand once and resolves it to a single `(contract, suit)` pair via `_find_best_contract` — max-contract search and suit tie-break (belote first, then the Spades → Clubs preference order, picking among *qualifying* suits only) folded into one step. Partner support is anchored to the team's *opening* bid in the suit: the supporting seat announces its complement (+10 per external ace, +10 for the trump complement) exactly once, capped at `opening + complement`, and a seat never supports a suit it opened itself — previously each supporting turn re-added the full complement on top of the standing contract, so partners alternately ratcheted each other up (80 → 110 → 130 → 160 …) until the inflated value armed the opponents' double heuristic. The `RedoubleBid` decision lives on the frozen-auction path (`_choose_under_double`), gated on a `_should_redouble` stub that returns `False` today — a real Surcoinche heuristic is the open follow-up here.
