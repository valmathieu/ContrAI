"""End-game screen rendering for the Rich terminal UI.

The final scoreboard: the winner banner, the round-by-round summary
table (one row per :class:`~contrai_engine.view.rich_view.RoundSummary`),
the per-row contract cell, and the new-game / rematch / quit prompt.
Pure builders consuming the UI-side history ``RichView`` accumulated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contrai_core import TeamSide
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
    BORDER,
    DIM,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_CHECK,
    RED,
    RULE,
    TITLE,
    YELLOW,
)

if TYPE_CHECKING:
    from contrai_engine.model.game import GameOverStatus
    from contrai_engine.view.rich_view import RoundSummary


def _panel_game_over_banner(status: GameOverStatus) -> Panel:
    """Gold double-bordered banner naming the winning team and final score.

    The winner's total is highlighted gold; the loser keeps its team
    color. Team labels sit under their numbers.
    """
    winner = status.winner
    winner_abbr = _team_abbr(winner) if winner is not None else "—"
    final = status.final_scores
    ns = final.get(TeamSide.NS, 0)
    ew = final.get(TeamSide.EW, 0)
    is_ns_winner = winner is TeamSide.NS

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
    ns_style = GOLD if is_ns_winner else _team_color(TeamSide.NS)
    ew_style = _team_color(TeamSide.EW) if is_ns_winner else GOLD
    score_line.append(ns_str, style=f"bold {ns_style}")
    score_line.append("   vs   ", style=DIM)
    score_line.append(ew_str, style=f"bold {ew_style}")
    pad2 = max(0, (66 - score_line.cell_len) // 2)
    body.append(" " * pad2)
    body.append_text(score_line)
    body.append("\n")
    # Team labels
    label_line = Text()
    label_line.append(
        _team_abbr(TeamSide.NS).rjust(len(ns_str)),
        style=f"bold {_team_color(TeamSide.NS)}",
    )
    label_line.append("       ", style=DIM)
    label_line.append(
        _team_abbr(TeamSide.EW).ljust(len(ew_str)),
        style=f"bold {_team_color(TeamSide.EW)}",
    )
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
    """Round-by-round table: one row per :class:`RoundSummary`.

    Columns: round number, contract, made/failed mark, per-team round
    points, and the running game totals after that round.
    """
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
    ns_abbr, ew_abbr = _team_abbr(TeamSide.NS), _team_abbr(TeamSide.EW)
    table.add_column(f"{ns_abbr} pts", justify="right")
    table.add_column(f"{ew_abbr} pts", justify="right")
    table.add_column(f"Running {ns_abbr} / {ew_abbr}", justify="right", style=DIM)

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
        ns_cell = (Text(str(row.ns_pts), style=f"bold {_team_color(TeamSide.NS)}")
                   if row.ns_pts > 0
                   else Text("·", style=DIM))
        ew_cell = (Text(str(row.ew_pts), style=f"bold {_team_color(TeamSide.EW)}")
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
    """Contract cell for a summary row: team, value, suit, double marker.

    Renders a dim ``all passed`` when the round produced no contract.
    """
    t = Text()
    if row.contract is None:
        t.append("all passed", style=DIM)
        return t
    side = row.contract_side
    if side is not None:
        t.append(_team_abbr(side), style=f"bold {_team_color(side)}")
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
    """Prompt line offering the ``[n]`` / ``[r]`` / ``[q]`` end-game choices."""
    t = Text()
    t.append("Game over.  ", style=FG)
    t.append("[n]", style=f"bold {YELLOW}")
    t.append(" new game  ·  ", style=FG)
    t.append("[r]", style=f"bold {YELLOW}")
    t.append(" rematch  ·  ", style=FG)
    t.append("[q]", style=f"bold {YELLOW}")
    t.append(" quit", style=FG)
    return t
