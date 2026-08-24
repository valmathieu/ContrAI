"""Landing screen rendering for the Rich terminal UI.

The pre-game splash: the block-ASCII title and subtitle, the suit
ribbon, the target-score radio, the seat roster, and the target prompt.
Pure builders consuming scalars.
"""

from __future__ import annotations

from contrai_core import Position, Suit
from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contrai_engine.view.formatting import (
    _position_short,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.theme import (
    BLUE,
    BORDER,
    DEFAULT_TARGET,
    DIM,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_FG,
    ORANGE,
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
    """Centered dim subtitle line under the block title."""
    return Text("Belote · Contrée · CLI edition".center(70), style=DIM)


def _landing_suit_ribbon() -> Text:
    """Centered decorative ribbon of the four suit glyphs."""
    ribbon = Text()
    # One glyph per card suit, in Suit's own order.
    glyphs = [(suit, _suit_color(suit)) for suit in Suit]
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
    """One radio row per §9.1 target score, highlighting the selected one."""
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


def _panel_players(autoplay: bool = False) -> Panel:
    """Players block. Hardcoded for v1 — South=human, others=AI expert.

    Args:
        autoplay: ``True`` when South is an AI seat as well (unattended
            four-AI game), so the block announces four AI players
            instead of a human seat nobody is sitting in.

    Returns:
        The players ``Panel``.

    TODO: replace with a configurable seat picker when we expose
    difficulty / player config on the landing screen.
    """
    # Per-seat role metadata, keyed by Position rather than a parallel
    # string roster — the letter and seat name rendered below both
    # derive from the enum member itself.
    south = (
        ("AI · expert", ORANGE, False)
        if autoplay
        else ("human", GREEN_FG, True)
    )
    roles: dict[Position, tuple[str, str, bool]] = {
        Position.NORTH: ("AI · expert", BLUE, False),
        Position.EAST: ("AI · expert", ORANGE, False),
        Position.SOUTH: south,
        Position.WEST: ("AI · expert", ORANGE, False),
    }
    # Two columns of two: render as a 2-row, 2-col Table. The on-screen
    # grouping (N, E / S, W) is independent of Position's anticlockwise
    # definition order, so it is spelled out explicitly here.
    layout = (
        (Position.NORTH, Position.EAST),
        (Position.SOUTH, Position.WEST),
    )
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    for row_seats in layout:
        cells = []
        for seat in row_seats:
            role, color, is_human = roles[seat]
            cell = Text()
            cell.append(_position_short(seat), style=f"bold {color}")
            cell.append(" ", style=FG)
            name = "You" if is_human else str(seat)
            cell.append(name, style=f"bold {color}" if is_human else FG)
            cell.append(f" ({role})", style=DIM)
            cells.append(cell)
        table.add_row(*cells)
    return Panel(
        table,
        title=Text("Players", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )


def _landing_prompt_text(selected: int) -> Text:
    """Prompt line asking for the target score, naming the default.

    The offered values are read off :data:`TARGET_OPTIONS` rather than
    spelled out, so the prompt and the radio above it can never disagree
    about what the table accepts.
    """
    t = Text()
    offered = " / ".join(str(value) for value, _, _ in TARGET_OPTIONS)
    t.append(
        f"Target score? [{offered}] (default ",
        style=FG,
    )
    t.append(str(selected), style=f"bold {GOLD}")
    t.append(")", style=FG)
    return t
