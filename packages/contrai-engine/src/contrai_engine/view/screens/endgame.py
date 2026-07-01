"""End-game screen rendering for the Rich terminal UI.

The final scoreboard: the winner banner, the round-by-round summary
table (one row per :class:`~contrai_engine.view.rich_view.RoundSummary`),
the per-row contract cell, and the new-game / rematch / quit prompt.
Pure builders consuming the UI-side history ``RichView`` accumulated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.box import DOUBLE, ROUNDED, SQUARE
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contrai_engine.view.formatting import (
    _suit_color,
    _suit_glyph,
    _team_abbr,
    _team_color,
)
from contrai_engine.view.theme import (
    BLUE,
    BORDER,
    DIM,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_CHECK,
    ORANGE,
    RED,
    RULE,
    TITLE,
    YELLOW,
)

if TYPE_CHECKING:
    from contrai_engine.model.game import GameOverStatus
    from contrai_engine.view.rich_view import RoundSummary


def _panel_game_over_banner(status: GameOverStatus) -> Panel:
    winner_name = status.winner or "—"
    winner_abbr = _team_abbr(winner_name) if winner_name != "—" else "—"
    final = status.final_scores
    ns = final.get("North-South", 0)
    ew = final.get("East-West", 0)
    is_ns_winner = winner_name == "North-South"

    body = Text()
    body.append("\n")
    # Winner banner row: gold pill spanning full inner width.
    banner = f"★   {winner_abbr}   WINS   ★"
    pad = max(0, (66 - len(banner)) // 2)
    body.append(" " * pad)
    body.append(banner, style=f"bold {GOLD_FG} on {GOLD_BG}")
    body.append("\n\n")
    body.append("Final score".center(66), style=DIM)
    body.append("\n")
    # Score line: "1620   vs   1420"
    ns_str = str(ns)
    ew_str = str(ew)
    score_line = Text()
    if is_ns_winner:
        score_line.append(ns_str, style=f"bold {GOLD}")
    else:
        score_line.append(ns_str, style=f"bold {BLUE}")
    score_line.append("   vs   ", style=DIM)
    if not is_ns_winner:
        score_line.append(ew_str, style=f"bold {GOLD}")
    else:
        score_line.append(ew_str, style=f"bold {ORANGE}")
    pad2 = max(0, (66 - score_line.cell_len) // 2)
    body.append(" " * pad2)
    body.append_text(score_line)
    body.append("\n")
    # Team labels
    label_line = Text()
    label_line.append("N-S".rjust(len(ns_str)), style=f"bold {BLUE}")
    label_line.append("       ", style=DIM)
    label_line.append("E-W".ljust(len(ew_str)), style=f"bold {ORANGE}")
    pad3 = max(0, (66 - label_line.cell_len) // 2)
    body.append(" " * pad3)
    body.append_text(label_line)

    return Panel(
        body,
        title=Text("Game over", style=f"bold {GOLD}"),
        border_style=GOLD,
        box=DOUBLE,
        width=70,
    )


def _panel_round_summary(history: list["RoundSummary"]) -> Panel:
    table = Table(
        show_header=True,
        header_style=f"bold {DIM}",
        border_style=RULE,
        box=SQUARE,
        expand=True,
    )
    table.add_column("#", justify="right", style=DIM, width=3)
    table.add_column("Contract", justify="left")
    table.add_column("Made", justify="center", width=5)
    table.add_column("N-S pts", justify="right")
    table.add_column("E-W pts", justify="right")
    table.add_column("Running N-S / E-W", justify="right", style=DIM)

    for row in history:
        num = str(row.round_number)
        contract_cell = _format_summary_contract(row)
        made_cell = (
            Text("✓", style=f"bold {GREEN_CHECK}")
            if row.contract_made
            else Text("✗", style=f"bold {RED}")
        )
        if row.contract is None:
            made_cell = Text("—", style=DIM)
        ns_cell = (Text(str(row.ns_pts), style=f"bold {BLUE}")
                   if row.ns_pts > 0
                   else Text("·", style=DIM))
        ew_cell = (Text(str(row.ew_pts), style=f"bold {ORANGE}")
                   if row.ew_pts > 0
                   else Text("·", style=DIM))
        running = f"{row.running_ns} / {row.running_ew}"
        table.add_row(num, contract_cell, made_cell, ns_cell, ew_cell,
                      Text(running, style=DIM))

    return Panel(
        table,
        title=Text("Round-by-round summary", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )


def _format_summary_contract(row: "RoundSummary") -> Text:
    t = Text()
    if row.contract is None:
        t.append("all passed", style=DIM)
        return t
    team_abbr = _team_abbr(row.contract_team_name or "")
    team_color = _team_color(row.contract_team_name or "")
    t.append(team_abbr, style=f"bold {team_color}")
    t.append(" ", style=FG)
    # SlamLevel.__str__ yields "Slam" / "Solo Slam"; numerics "80"…"180".
    value_str = str(row.contract.value)
    t.append(value_str, style="bold")
    t.append(" ", style=FG)
    t.append(_suit_glyph(row.contract.suit),
             style=_suit_color(row.contract.suit))
    if row.contract.redouble:
        t.append(" redoubled", style=GOLD)
    elif row.contract.double:
        t.append(" doubled", style=GOLD)
    return t


def _end_game_prompt_text() -> Text:
    t = Text()
    t.append("Game over.  ", style=FG)
    t.append("[n]", style=f"bold {YELLOW}")
    t.append(" new game  ·  ", style=FG)
    t.append("[r]", style=f"bold {YELLOW}")
    t.append(" rematch  ·  ", style=FG)
    t.append("[q]", style=f"bold {YELLOW}")
    t.append(" quit", style=FG)
    return t
