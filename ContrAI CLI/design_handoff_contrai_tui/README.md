# Handoff: ContrAI Terminal UI

## Overview

ContrAI is a Python CLI implementation of **Belote contrée**, a French four-player trick-taking card game. This handoff covers the full terminal UI for the game, rendered with the [Rich](https://rich.readthedocs.io/) library: a landing/setup screen, three in-game states covering the bidding and trick-play loop, and a final scoreboard screen.

## About the Design Files

The files in `mockups/` are **design references created in HTML/SVG** — they show the intended look and behavior of the terminal UI but are **not production code to copy**. The task is to **recreate these designs in the existing Python codebase** using the Rich library's panel/table/text primitives, hooked into your existing game-state objects (Round, Trick, Player, Hand, etc.).

`mockups/index.html` is a single-page viewer that shows all five frames stacked vertically — open it in any browser for the canonical reference.

## Fidelity

**High-fidelity.** Every panel title, border style, color, and prompt string in the mockups is intentional. Match them exactly unless a Rich constraint forces a substitute. The mockups use:

- Monospace terminal aesthetic, dark background (`#1e1e1e`)
- Rich-style panels: single `┌─ … ─┐` lines for normal panels, double `╔═ … ═╗` for the end-game banner
- Box titles in the top border, e.g. `┌─ Your hand (South) ─┐`
- Suit glyphs `♠ ♣` in light gray, `♥ ♦` in red
- Diamond seating layout (N top, E right, S bottom, W left) for trick panels

## Screens / Views

### 0. Landing — Game Setup (`mockups/00-landing.svg`)

**Purpose**: First screen shown after `contrai` is launched. The user picks a target score and starts the game.

**Layout** (terminal grid, 70 columns wide):

- Rows 1–6: Block-ASCII **CONTRAI** title, centered. Use the [pyfiglet](https://pypi.org/project/pyfiglet/) `ANSI Shadow` font OR a hand-rolled `▀ ▄ █` block (the mockup uses `█ ╗ ╔ ╝ ╚` Unicode box block glyphs). Color: `#e5c07b` (warm yellow), bold.
- Row 7: Subtitle "Belote · Contrée · CLI edition" in dim gray (`#6a6a6a`), centered.
- Row 8: Suit ribbon `♠   ♥   ♦   ♣`, centered. Red suits `#e06c75`, black suits `#d4d4d4`.
- Rows 10–19: **Game setup** panel (Rich `Panel`, title="Game setup", border `#7a7a7a`). Contains:
  - Row 11: "Target score" (bold light gray) + helper text "(first team to reach the target wins the game)" in dim
  - Rows 13–17: Five radio-style options, one per row:
    - `( )` empty radio or `(●)` filled radio for the selected one
    - Value (right-padded to 4 chars): `500`, `1000`, `1500`, `2000`, `3000`
    - Label: `Quick game`, `Short game`, `Standard`, `Long game`, `Marathon`
    - Separator `·`
    - Estimate: `~10 min`, `~20 min`, `~30 min`, `~45 min`, `~60 min`
    - The selected row (`1500 Standard`) has:
      - Gold background pill `#3a2b10` spanning the full row
      - Foreground text in `#ffd57a` (gold), bold value, "← default" suffix in `#f0b54a`
- Rows 20–23: **Players** panel (Rich `Panel`, title="Players"). Two columns of two:
  - `N North (AI · medium)` — N in blue `#7fb6ff` bold, name in default gray, role in dim
  - `E East (AI · medium)` — E in orange `#ffb482`
  - `S You · human` — S and "You" in green `#cfeac0` bold (this is the human player)
  - `W West (AI · medium)` — W in orange
- Row 23–24: Prompt line, no border (or in its own Panel — keep consistent with in-game prompt panel):
  - `> ` in green bold
  - `Target score? [500 / 1000 / 1500 / 2000 / 3000] (default 1500): █` — the default value `1500` is highlighted in gold, cursor block `█` at the end

**Interaction**: User types a number and presses Enter, or just Enter to accept default 1500. Validate: must be one of the five values (or accept any positive int divisible by 10 if you want to be liberal — match your existing pattern). Then transition to game.

### 1. In-Game — Bidding Phase (`mockups/01-bidding.svg`)

**Purpose**: Cards have just been dealt; players are bidding. South's turn to bid (West just passed).

**Layout** (70-column grid, 5 panels):

```
┌─ Game score ───────┐  ┌─ Round ──────────────────────────────────────┐
│ N-S            850 │  │ Contract: —                                  │
│ E-W           1320 │  │ Trump:    —                                  │
│ ·················· │  │ Phase:    Bidding in progress                │
│ Target        1500 │  │ Dealer:   East                               │
└────────────────────┘  └──────────────────────────────────────────────┘

┌─ Last trick ─────┐    ┌─ Current trick ──────────────────────────────┐
│   (none)         │    │   (bidding…)                                 │
│                  │    │                                              │
└──────────────────┘    └──────────────────────────────────────────────┘

┌─ Your hand (South) ─────────────────────────────────────────────────┐
│  [1] A♠  [2] K♠  [3] J♥  [4] A♥  [5] 9♥  [6] Q♦  [7] 10♦  [8] 8♦   │
│        (no card-play obligation yet — bidding phase)                │
└─────────────────────────────────────────────────────────────────────┘

┌─ Prompt ────────────────────────────────────────────────────────────┐
│ West passed. Your bid?  (e.g. '80 H' / 'pass' / 'coinche')          │
│ > █                                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

**Component details**:

- **Game score panel**: Width 22 cols. `N-S` label in blue bold + right-aligned score. `E-W` label in orange bold + right-aligned score. Dotted separator `·` row in `#3a3a3a`. `Target` in dim + value in yellow `#e5c07b`.
- **Round panel** (state 1, bidding only): Width 46 cols. Plain gray border. Fields `Contract:`, `Trump:`, `Phase:`, `Dealer:` — labels in dim gray, values in default. "Bidding in progress" value is bold yellow to call attention.
- **Last trick / Current trick**: When no tricks played, show placeholder text `(none)` / `(bidding…)` in dim, centered vertically in the panel. Panel height 8 rows.
- **Hand panel**: All 8 cards visible. Each card cell is 8 chars wide: ` [n] RR S` (compact — fits 8 in 70 cols). Numbers 1–8 in yellow `#e5c07b` bold for selection, brackets in border gray. Cards sorted: spades, hearts, diamonds (no clubs in this hand). No card is highlighted as playable.
- **Prompt panel**: 4 rows tall. Question on row 1 (default gray, since not the user's mandatory turn). `> ` in green bold + cursor block `█` on row 2.

### 2. In-Game — Mid-Trick, Must Trump (`mockups/02-trick-in-progress.svg`)

**Purpose**: Trick 4 of 8. West led ♣K, North played ♣10, East played ♣A, **South to play**. South has no clubs → must trump. The two ♥ cards are highlighted as the only legal plays.

**Differences from state 1**:

- **Round panel border becomes yellow** `#e5c07b` (trump active). Title in yellow. Title suffix `★` in gold `#f0b54a`.
  
  - `Contract: 100 by E-W` — "100" bold default, "by" dim, "E-W" orange bold. **Do not repeat the trump suit here** — it lives in the Trump row.
  - `Trump:    ♥ Hearts ★` — heart glyph red, "Hearts" bold default, ★ gold
  - `Trick:    4 of 8`
  - `Round pts: N-S 38 · E-W 52` — N-S in blue, E-W in orange

- **Last trick panel**: Now uses the **dim/echo** treatment. Border `#444`, title "Last trick 3" in dim. Width compressed to 22 cols (half the width of current trick). Diamond rendered with muted colors — this is intentional secondary information. South's ♠A is the winner, shown with gold pill `★`. Status line: `Won: South` in gold.

- **Current trick panel**: Width 46 cols (the focal point). Title `Current trick`, suffix `trick 4` in dim. Diamond layout:
  
  ```
              N ♣10
   W ♣K (led)        [E ♣A ★]    ← E is currently winning the trick
              S  ?              ← S to play, yellow "?"
  ```
  
  - **N at top center, E at right (anchored to inner right edge), S at bottom center, W at left (anchored to inner left)**
  - Faint `╱╲` diamond outline in `#3d3d40` between positions
  - Each player rendered as `LABEL CARD`. Label in blue (N/S) or orange (E/W).
  - **Live leader** (E here — highest card under the led suit, no trump played) gets the gold pill background `#3a2b10`, label/card text in `#ffd57a`, trailing `★` in `#f0b54a`.
  - **South's slot** shows `?` in yellow bold (pending).
  - **`(led)` annotation** in dim gray follows the leader's card.
  - Status line at bottom of panel: `→ Your turn` in yellow.

- **Hand panel**: 5 cards (sorted trump-first): `♥J ♥A | ♠A | ♦Q ♦10`. Cell width 10 (more breathing room with fewer cards).
  
  - The two playable cards (`♥J`, `♥A`) get a **green background** `#2e5a2a` spanning the cell, foreground `#cfeac0`, number in white bold.
  - The three non-playable cards (`♠A`, `♦Q`, `♦10`) are dimmed: text in `#6a6a6a`, red suits in `#7a3a3f`.
  - Hint line under the row: `↑ playable (must trump — partner E led ♣A)` in green `#cfeac0`, centered.

- **Prompt panel**: First line `Your turn. Must trump. Choose card [1-5]:` in **yellow bold** (mandatory action).

### 3. In-Game — Trick Won, Transition (`mockups/03-trick-won.svg`)

**Purpose**: Trick 4 just finished. South played ♥J (jack of trump) and won.

**Differences from state 2**:

- Round panel `Round pts:` updates to `N-S 58 · E-W 52` (South's team gained the trick).
- **Current trick panel**: All four cards revealed in the diamond. S now shows `♥J` with the gold winner pill + `★`. Status line: `Won: South` in gold.
- **Last trick panel**: Unchanged from state 2 (still trick 3 — only rotates after next play).
- **Hand panel**: 4 cards remaining (`♥A`, `♠A`, `♦Q`, `♦10`). All neutral (not South's turn yet visually). Cell width 11. Hint: `4 cards remaining` in dim.
- **Prompt panel**: `South leads next trick. Press [Enter] to continue…` in default gray (waiting, not mandatory).

### 4. End Game — Scoreboard (`mockups/04-game-over.svg`)

**Purpose**: A team has reached the target score. Show the winner and full round-by-round breakdown.

**Layout**:

- **Rows 0–6: "Game over" banner panel** — full width, **double-line border** `╔═ … ═╗` in gold `#f0b54a`, title `Game over` in gold bold.
  - Row 2: `★   N-S   WINS   ★` centered, with gold pill background `#3a2b10` spanning the full inner row, foreground `#ffd57a` bold.
  - Row 4: "Final score" label in dim, centered.
  - Row 5: `1620   vs   1420` centered. `1620` (winner) in gold bold, `vs` in dim, `1420` in orange bold.
  - Row 6: Team labels `N-S` (blue) and `E-W` (orange) directly under their numbers.
- **Rows 8–20: Round-by-round summary panel** — Rich `Table` inside a Panel titled "Round-by-round summary". Columns:
  - `#` (right-align, dim) — round number 1..10
  - `Contract` (left, default) — e.g. `N-S 100 ♠`, `E-W 110 ♦`, `N-S 130 ♥ + bel`, `E-W 100 ♣ contrée` (color the suit glyph)
  - `Made` (center) — `✓` in `#3a7a3a` if made, `✗` in red `#e06c75` if down
  - `N-S pts` (right) — bold blue if N-S won the round, dim `·` if 0
  - `E-W pts` (right) — bold orange if E-W won the round, dim `·` if 0
  - `Running N-S / E-W` (right, dim) — cumulative game score after this round, e.g. `358 / 284`
  - Header row in dim bold, separator row `─` in `#2a2a2a` below the header.
- **Rows 21–24: Prompt panel** — `Game over.  [n] new game  ·  [r] rematch  ·  [q] quit` — bracketed keys in yellow bold. `> █` on line 2.

**Interaction**: `n` → back to landing screen (state 0). `r` → new game, same target & player config. `q` → exit cleanly.

## Interactions & Behavior

### Navigation flow

```
landing (0) ──Enter──▶ bidding (1) ──bid accepted──▶ play loop ──┐
                                                                  │
                       ┌──────────────────────────────────────────┘
                       ▼
                play trick (2 → 3 → 2 → 3 …) ──hand exhausted──▶ next round
                                                                  │
                       ┌──────────────────────────────────────────┘
                       ▼
                round score updates ──either team hits target──▶ end game (4)
                                                                       │
                       ┌───────────────────────────────────────────────┘
                       ▼
                  [n] → landing  ·  [r] → bidding (same setup)  ·  [q] → exit
```

### Live trick-winner tracking (state 2)

After each card is played within a trick, recompute who is currently winning:

- If any trump has been played: highest trump wins
- Else: highest card of the led suit wins

Apply the **gold pill highlight (`#3a2b10` bg, `#ffd57a` fg, `★` suffix)** to that player's slot in the Current Trick panel. As more cards are played, the highlight may move. When all 4 are in, the winner is final and the status line flips from `→ Your turn` (or whoever's turn) to `Won: <player>`.

### Playable-card highlighting (state 2)

Compute legal plays per Coinche rules:

- Must follow suit if possible
- If can't follow and partner is not currently winning: must trump (and over-trump if a trump has been played)
- If can't follow and partner *is* winning: free to discard
- "Pisser" — if no trump in hand and can't follow: free to discard

Cards that pass these rules → **green pill** (`#2e5a2a` bg, `#cfeac0` fg, white number).
Cards that don't → **dimmed** (`#6a6a6a` fg, `#7a3a3f` for red suits).

The hint line below the hand explains *why* (e.g. "must trump — partner E led ♣A", "must follow ♠", "free discard").

### Animations

The CLI doesn't really animate, but consider:

- Re-render the full layout on each state change (Rich's `Live` context is the right pattern)
- A short `time.sleep(0.5)` between trick completion (state 3) and clearing for next trick gives the user time to read the result

## State Management

You likely already have these — just map them to render input:

- `Game`: target_score, n_s_score, e_w_score, current_round, history (list of completed rounds), is_over
- `Round`: contract (bid value, suit, taker, doubled_state), trump_suit, current_trick_idx, tricks_played, ns_round_pts, ew_round_pts
- `Trick`: led_suit, plays (ordered list of {player, card}), winner (computed)
- `Player`: name, position (N/E/S/W), is_human, hand (list of Cards)
- `Card`: rank, suit; helpers `is_trump(round.trump)`, `value(is_trump)`

Render functions receive these objects and emit Rich `Panel`s / `Table`s / `Text` instances.

## Design Tokens

### Colors

| Token         | Hex       | Use                                             |
| ------------- | --------- | ----------------------------------------------- |
| `bg`          | `#1e1e1e` | Terminal background (Rich auto, just don't set) |
| `fg`          | `#d4d4d4` | Default text                                    |
| `dim`         | `#6a6a6a` | Labels, secondary text, dimmed cards            |
| `border`      | `#7a7a7a` | Default panel borders                           |
| `border_dim`  | `#444444` | Last-trick panel border                         |
| `title`       | `#c8c8c8` | Panel titles                                    |
| `red`         | `#e06c75` | ♥ ♦ suits, error                                |
| `red_dim`     | `#7a3a3f` | ♥ ♦ suits in dimmed cards                       |
| `blue`        | `#7fb6ff` | N-S team color, N/S player labels               |
| `orange`      | `#ffb482` | E-W team color, E/W player labels               |
| `green_bg`    | `#2e5a2a` | Playable-card background pill                   |
| `green_fg`    | `#cfeac0` | Playable-card foreground, "You" / prompt arrow  |
| `green_check` | `#3a7a3a` | ✓ "made" marker                                 |
| `yellow`      | `#e5c07b` | Trump emphasis, mandatory prompt, hotkeys       |
| `gold`        | `#f0b54a` | ★ markers, winner banner border, default arrow  |
| `gold_bg`     | `#3a2b10` | Winner pill background, selected-radio bg       |
| `gold_fg`     | `#ffd57a` | Winner pill foreground, gold text               |
| `hint`        | `#3d3d40` | Faint diamond outline `╱╲`                      |
| `rule`        | `#2a2a2a` | Table separator under header                    |
| `dot`         | `#3a3a3a` | Dotted divider row in Game score panel          |

### Box characters

- Single line: `┌ ┐ └ ┘ ─ │ ├ ┤ ┬ ┴ ┼`
- Double line (banner): `╔ ╗ ╚ ╝ ═ ║`
- Diamond outline: `╱ ╲`
- Suits: `♠ ♥ ♦ ♣`
- Misc: `★ █ ✓ ✗ ● ◄ →`

### Typography

The CLI inherits whatever monospace font the user's terminal uses. Don't try to override. The mockups render in **Menlo / Monaco / Courier New** for preview but in production Rich just outputs ANSI escapes.

Bold is applied to: panel titles, player labels (N/E/S/W), card ranks and suits in trick panels, mandatory prompt text, hotkey letters in end-game prompt, ✓/✗ markers, winning-card highlight, ★, █ cursor.

### Spacing / Layout

- Total terminal width target: **70 columns** (works in any terminal ≥ 80 cols).
- Top row: `Game score` (22 cols) + 2-col gap + `Round` (46 cols)
- Middle row: `Last trick` (22 cols) + 2-col gap + `Current trick` (46 cols)
- Hand panel: full 70 cols, height 5 (border + 1 card row + 1 hint row + border + 1 spare)
- Prompt panel: full 70 cols, height 4 (border + question row + input row + border)
- Panel heights: top = 6, middle = 8, hand = 5, prompt = 4 → total content = ~24 rows, fits a 24-row terminal.

## Implementation Notes for Rich

- Use `rich.layout.Layout` for the 2×2 panel grid in-game. Use `Layout.split_row()` for the top and middle rows.
- For the diamond seating, render a single `rich.text.Text` with explicit spaces and newlines (don't try to use a Table — manual positioning is cleaner here). Use `Text.append()` with style strings per segment.
- The Round panel border switches between `style="white"` (default) and `style="bold yellow"` (trump active) — Rich's `Panel(border_style=...)` is your friend.
- The playable-card green pill is `Text(" [1] J♥ ", style="white on rgb(46,90,42)")` — Rich does ANSI bg colors fine.
- The end-game **double-line border** — Rich's `Panel` supports `box=box.DOUBLE` (or use `box.DOUBLE_EDGE`). The gold color is `border_style="rgb(240,181,74)"`.
- The round-by-round summary is a perfect fit for `rich.table.Table(show_header=True, header_style="bold dim")` with per-cell `Text(..., style=...)`.

## Assets

No image assets — everything is text + ANSI colors. The mockups are SVG-as-terminal renderings (a generator script produced them); they exist only for visual reference and don't need to be shipped with the app.

## Files in this bundle

- `README.md` — this file
- `mockups/index.html` — single-page viewer showing all 5 frames stacked
- `mockups/00-landing.svg` — game setup screen
- `mockups/01-bidding.svg` — in-game, bidding phase
- `mockups/02-trick-in-progress.svg` — in-game, mid-trick, South must trump
- `mockups/03-trick-won.svg` — in-game, trick just completed
- `mockups/04-game-over.svg` — end-game scoreboard

## How to use this with Claude Code

In your repo, run Claude Code and reference the files explicitly. Example prompt:

> Here is a design handoff in `design_handoff_contrai_tui/`. Read `README.md` and the SVGs in `mockups/` to understand the intended UI. Then implement these five screens in the existing codebase using the Rich library, wiring them up to the existing `Game` / `Round` / `Trick` / `Player` objects. Start by sketching the Layout structure for the in-game view, then build the landing screen, then the end-game screen. Keep rendering logic in a new module `contrai/ui.py` and don't touch game-logic code.

Iterate from there — Claude Code can compare its output against the SVGs side-by-side using `mockups/index.html`.
