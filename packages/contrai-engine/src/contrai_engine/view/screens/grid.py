"""The replay's trick grid: a round's eight tricks on one screen.

Stepping shows one action at a time, which is the right way to follow a
hand and the wrong way to review one. The grid lays the whole round out
at once — a header naming the contract and how it ended, the eight tricks
four to a row, and the auction as the live game's "Bidding so far" panel
draws it, one row per time round the table.

Each trick is the live game's own diamond at its narrowest (the 18-cell
``Last trick`` echo), so a trick reads here exactly as it did at the
table: the winner gold with a ★, the seat that led marked ``▸``, and a
belote badge under the seat that announced it — on the trick it was
announced in, not for the rest of the round.

Pure ``(data) -> renderable`` builders, like every screen module;
``RichView.show_replay_grid`` prints them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Sequence

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contrai_core import BasePlayer, Play, Position, Suit, TeamSide, rules_for
from contrai_engine.view.formatting import (
    _format_contract_short,
    _position_short,
    _team_abbr,
    _team_color,
)
from contrai_engine.view.screens.bidding import _panel_bidding_history
from contrai_engine.view.screens.recap import _contract_made
from contrai_engine.view.screens.trick import _badge_rows, _render_diamond
from contrai_engine.view.state_helpers import _belote_badges_by_trick
from contrai_engine.view.theme import (
    BORDER,
    BORDER_DIM,
    DIM,
    FG,
    GOLD,
    GREEN_CHECK,
    RED,
    TITLE,
    YELLOW,
)

if TYPE_CHECKING:
    from contrai_engine.model.round import Round

#: One trick panel's width: the 18-cell diamond plus borders and padding,
#: the same as the in-game ``Last trick`` panel.
TRICK_WIDTH = 22

#: Tricks per grid row. Four across by two down keeps the whole screen
#: under 30 rows, where two across would run to about forty.
PER_ROW = 4

#: The screen's width: four trick panels edge to edge. The header and the
#: bidding panel are drawn at the same width so the screen reads as one
#: block.
GRID_WIDTH = TRICK_WIDTH * PER_ROW

#: The last-trick bonus a trick-8 footer names beside the card points.
LAST_TRICK_BONUS = 10


def _grid_tricks(
    round_: Optional["Round"],
) -> list[tuple[Sequence[Play], Optional[BasePlayer]]]:
    """The tricks the round has on record, each with its winner.

    Args:
        round_: The round, complete or in progress.

    Returns:
        The completed tricks with their winners, then the trick in
        progress (if any) with ``None`` — it has no winner yet. Empty
        before the play state is seeded, which is the whole auction.
    """
    play_state = getattr(round_, "play_state", None) if round_ else None
    if play_state is None:
        return []
    tricks: list[tuple[Sequence[Play], Optional[BasePlayer]]] = list(
        zip(play_state.completed_tricks, play_state.trick_winners)
    )
    if play_state.current_trick:
        tricks.append((play_state.current_trick, None))
    return tricks


def _panel_grid_trick(
    number: int,
    plays: Optional[Sequence[Play]],
    winner: Optional[BasePlayer],
    trump: Optional[Suit],
    *,
    badges: dict[Position, tuple[Suit, ...]],
    height: int,
) -> Panel:
    """One cell of the grid: trick ``number`` as a compact diamond.

    Args:
        number: The trick's 1-based number.
        plays: Its plays in order, or ``None`` for a trick not reached.
        winner: Who took it, or ``None`` while it is still in progress.
        trump: The contract's suit, which decides the winner highlight
            and the card points.
        badges: The belote badges announced *in this trick*.
        height: The panel height, shared by the whole grid so its rows
            stay flush.

    Returns:
        The panel.
    """
    title_style = f"bold {TITLE}" if plays is not None else DIM
    title = Text(f"Trick {number}", style=title_style)
    if plays is None:
        return Panel(
            Align.center(Text("(not played)", style=DIM), vertical="middle"),
            title=title,
            border_style=BORDER_DIM,
            box=ROUNDED,
            width=TRICK_WIDTH,
            height=height,
        )
    body = _render_diamond(
        plays,
        trump,
        pending_position=None,
        winner_position=winner.position if winner else None,
        dimmed=False,
        width=18,
        belote_by_position=badges,
        narrow_badges=True,
        lead_marker=True,
    )
    body.append("\n")
    if winner is None:
        body.append("in progress", style=f"italic {DIM}")
    else:
        points = sum(rules_for(trump).points(play.card) for play in plays)
        body.append("Won ", style=DIM)
        body.append(_position_short(winner.position), style=f"bold {GOLD}")
        body.append(f" · {points}", style=FG)
        if number == 8:
            body.append(f"+{LAST_TRICK_BONUS}", style=DIM)
    return Panel(
        body,
        title=title,
        border_style=BORDER,
        box=ROUNDED,
        width=TRICK_WIDTH,
        height=height,
    )


def _grid_result_text(round_: Any) -> Text:
    """The header's second half: how the round ended, or where it stands.

    Args:
        round_: The round.

    Returns:
        ``✓ Made`` / ``✗ Failed`` with each side's round score once the
        round is scored; each side's card points while it is being
        played; a note while it is still bidding.
    """
    text = Text()
    if getattr(round_, "contract", None) is None:
        passed_out = getattr(round_, "auction", None) is not None
        text.append(
            "All passed — no contract" if passed_out
            else "Bidding in progress",
            style=f"bold {YELLOW}",
        )
        return text
    if getattr(round_, "round_score", None) is not None:
        if _contract_made(round_):
            text.append("✓ Made", style=f"bold {GREEN_CHECK}")
        else:
            text.append("✗ Failed", style=f"bold {RED}")
        scores = round_.round_scores
        label = "   Round score  "
    else:
        text.append("In progress", style=f"italic {DIM}")
        play_state = getattr(round_, "play_state", None)
        scores = (
            play_state.card_points_by_side if play_state is not None else {}
        )
        label = "   Card points  "
    text.append(label, style=DIM)
    for i, side in enumerate(TeamSide):
        if i:
            text.append("  ·  ", style=DIM)
        text.append(f"{_team_abbr(side)} ", style=f"bold {_team_color(side)}")
        text.append(str(scores.get(side, 0)), style="bold")
    return text


def _panel_grid_header(round_: Any) -> Panel:
    """The grid's top line: the round, its contract, and how it ended.

    Args:
        round_: The round.

    Returns:
        A one-line panel as wide as the grid.
    """
    body = Text()
    contract = getattr(round_, "contract", None)
    if contract is not None:
        body.append("Contract ", style=DIM)
        body.append_text(_format_contract_short(contract, suit_glyph=True))
        body.append("   ", style=DIM)
    body.append_text(_grid_result_text(round_))
    return Panel(
        body,
        title=Text(
            f"Round #{getattr(round_, 'round_number', '?')} — all tricks",
            style=f"bold {GOLD}",
        ),
        border_style=GOLD if contract is not None else BORDER,
        box=ROUNDED,
        width=GRID_WIDTH,
    )


def _render_trick_grid(round_: Any, bids: Sequence[Any]) -> Group:
    """The whole grid screen: header, eight tricks, then the auction.

    Args:
        round_: The round, complete or in progress.
        bids: The auction as far as it has gone, in order.

    Returns:
        The screen, ready to print.
    """
    contract = getattr(round_, "contract", None)
    trump = contract.suit if contract is not None else None
    tricks = _grid_tricks(round_)
    badges = _belote_badges_by_trick(round_)
    # One height for all eight, from the busiest trick's badges: a row
    # of panels of different heights would not line up.
    extra = max((_badge_rows(seats) for seats in badges.values()), default=0)
    height = 8 + max(0, extra - 1)
    panels = []
    for index in range(8):
        plays, winner = tricks[index] if index < len(tricks) else (None, None)
        panels.append(
            _panel_grid_trick(
                index + 1,
                plays,
                winner,
                trump,
                badges=badges.get(index, {}),
                height=height,
            )
        )
    grid = Table.grid(expand=False, padding=0)
    for _ in range(PER_ROW):
        grid.add_column(width=TRICK_WIDTH, no_wrap=True)
    for start in range(0, 8, PER_ROW):
        grid.add_row(*panels[start:start + PER_ROW])
    return Group(
        _panel_grid_header(round_),
        grid,
        _panel_bidding_history(list(bids), width=GRID_WIDTH),
    )


def _replay_grid_prompt_text() -> Text:
    """The line under the grid: what the marks mean, and the way out.

    Returns:
        The prompt line.
    """
    text = Text()
    text.append("▸", style=f"bold {YELLOW}")
    text.append(" led   ", style=DIM)
    text.append("★", style=f"bold {GOLD}")
    text.append(" won   ", style=DIM)
    text.append("[Enter]", style=f"bold {GOLD}")
    text.append(" back", style=FG)
    return text
