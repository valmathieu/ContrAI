"""Landing screen rendering for the Rich terminal UI.

The pre-game splash: the block-ASCII title and subtitle, the suit
ribbon, the target-score radio, the seat roster, and the target prompt —
plus the in-game ``Game score`` panel (the running game total shown in
every frame's top-left). Pure builders consuming scalars.
"""

from __future__ import annotations

from contrai_core import Suit
from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contrai_engine.view.formatting import _suit_glyph
from contrai_engine.view.theme import (
    BLUE,
    BORDER,
    DEFAULT_TARGET,
    DIM,
    DOT,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_FG,
    ORANGE,
    RED,
    TARGET_OPTIONS,
    TITLE,
    YELLOW,
)

try:
    from pyfiglet import Figlet
    _HAS_PYFIGLET = True
except ImportError:
    _HAS_PYFIGLET = False


def _landing_title() -> Text:
    """Centered block-ASCII CONTRAI title."""
    if _HAS_PYFIGLET:
        ascii_art = Figlet(font="ansi_shadow", width=70).renderText("CONTRAI")
    else:
        ascii_art = "CONTRAI"
    t = Text()
    for line in ascii_art.splitlines():
        t.append(line.center(70), style=f"bold {YELLOW}")
        t.append("\n")
    return t


def _landing_subtitle() -> Text:
    return Text("Belote · Contrée · CLI edition".center(70), style=DIM)


def _landing_suit_ribbon() -> Text:
    ribbon = Text()
    glyphs = [(Suit.SPADES, FG), (Suit.HEARTS, RED),
              (Suit.DIAMONDS, RED), (Suit.CLUBS, FG)]
    # Build "  ♠   ♥   ♦   ♣  " then center it.
    segments = []
    for suit, color in glyphs:
        segments.append((suit, color))
    # Render with 3 spaces between glyphs.
    inner = Text()
    for i, (suit, color) in enumerate(segments):
        if i > 0:
            inner.append("   ")
        inner.append(_suit_glyph(suit), style=f"bold {color}")
    # Centered within 70 cols.
    total = inner.cell_len
    pad = max(0, (70 - total) // 2)
    ribbon.append(" " * pad)
    ribbon.append_text(inner)
    return ribbon


def _panel_game_setup(selected: int) -> Panel:
    """Five radio rows for target score, highlight the selected one."""
    rows = Text()
    rows.append("Target score", style=f"bold {FG}")
    rows.append(" ", style=FG)
    rows.append(
        "(first team to reach the target wins the game)\n\n",
        style=DIM,
    )
    for value, label, estimate in TARGET_OPTIONS:
        is_sel = value == selected
        line = Text()
        if is_sel:
            radio = "(●)"
            line.append(f" {radio} ", style=f"bold {GOLD_FG} on {GOLD_BG}")
            line.append(f"{value:<4}  ", style=f"bold {GOLD_FG} on {GOLD_BG}")
            line.append(f"{label:<10}", style=f"{GOLD_FG} on {GOLD_BG}")
            line.append(f"  ·  {estimate}", style=f"{GOLD_FG} on {GOLD_BG}")
            if value == DEFAULT_TARGET:
                line.append("   ← default", style=f"bold {GOLD} on {GOLD_BG}")
            # Pad to fill the panel width with the gold background.
            used = line.cell_len
            line.append(" " * max(0, 60 - used), style=f"on {GOLD_BG}")
        else:
            line.append(" ( ) ", style=DIM)
            line.append(f"{value:<4}  ", style=f"bold {FG}")
            line.append(f"{label:<10}", style=FG)
            line.append(f"  ·  {estimate}", style=DIM)
        rows.append_text(line)
        rows.append("\n")
    return Panel(
        rows,
        title=Text("Game setup", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )


def _panel_players() -> Panel:
    """Players block. Hardcoded for v1 — South=human, others=AI medium.

    TODO: replace with a configurable seat picker when we expose
    difficulty / player config on the landing screen.
    """
    seats = [
        ("N", "North", "AI · medium", BLUE, False),
        ("E", "East", "AI · medium", ORANGE, False),
        ("S", "You", "human", GREEN_FG, True),
        ("W", "West", "AI · medium", ORANGE, False),
    ]
    # Two columns of two: render as a 2-row, 2-col Table.
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    rows = []
    for label, name, role, color, is_human in seats:
        cell = Text()
        cell.append(label, style=f"bold {color}")
        cell.append(" ", style=FG)
        if is_human:
            cell.append(name, style=f"bold {color}")
        else:
            cell.append(name, style=FG)
        cell.append(f" ({role})", style=DIM)
        rows.append(cell)
    table.add_row(rows[0], rows[1])  # N, E
    table.add_row(rows[2], rows[3])  # S, W
    return Panel(
        table,
        title=Text("Players", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )


def _landing_prompt_text(selected: int) -> Text:
    t = Text()
    t.append(
        "Target score? [500 / 1000 / 1500 / 2000 / 3000] (default ",
        style=FG,
    )
    t.append(str(selected), style=f"bold {GOLD}")
    t.append(")", style=FG)
    return t


def _panel_game_score(scores: dict, target_score: int) -> Panel:
    body = Text()
    ns = scores.get("North-South", 0)
    ew = scores.get("East-West", 0)
    body.append(f"{'N-S':<8}", style=f"bold {BLUE}")
    body.append(f"{ns:>10}\n", style=FG)
    body.append(f"{'E-W':<8}", style=f"bold {ORANGE}")
    body.append(f"{ew:>10}\n", style=FG)
    body.append("·" * 18, style=DOT)
    body.append("\n")
    body.append(f"{'Target':<8}", style=DIM)
    body.append(f"{target_score:>10}", style=f"bold {YELLOW}")
    return Panel(
        body,
        title=Text("Game score", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=22,
        height=6,
    )
