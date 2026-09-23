"""Replay screen rendering for the Rich terminal UI.

The two screens a recorded game is watched through: the round picker —
one row per recorded round, with its contract, outcome, running totals
and verification verdict — and the one-line step keys that draw under
whatever frame the replay just rendered.

This module is the **throwaway half** of the picker. The stable half is
:mod:`contrai_engine.replay.summary`, which computes the rows as plain
data; everything here is a ``(data) -> Panel | Text`` builder that could
be replaced wholesale by a different interface without a row changing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Sequence

from rich.align import Align
from rich.box import ROUNDED, SQUARE
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contrai_core import TeamSide
from contrai_engine.replay.verdict import Verdict
from contrai_engine.view.formatting import (
    _format_contract_short,
    _position_short,
    _suit_color,
    _suit_glyph,
    _team_abbr,
    _team_color,
)
from contrai_engine.view.theme import (
    BORDER_DIM,
    DIM,
    FG,
    GOLD,
    GREEN_FG,
    RED,
)

if TYPE_CHECKING:
    from contrai_engine.replay.summary import ReplayRow

#: Panel width, matching every other full-width screen in this interface.
WIDTH = 70

#: The not-steppable note, and the width the verdict column needs to show
#: it on one line. Every verdict token is shorter, so this is the binding
#: constraint on that column.
NOT_STEPPABLE = "not steppable"


def _panel_replay_summary(
    rows: Sequence["ReplayRow"], game_id: str
) -> Panel:
    """Round-by-round table for the replay picker, one row per recorded round.

    Columns: round number, contract, outcome, the running totals where the
    record holds a score line, and the verification verdict. A round that
    cannot be replayed is listed with a ``not steppable`` note rather than
    hidden — the round a viewer is hunting is usually one of those.

    Args:
        rows: One row per recorded round, in file order.
        game_id: The record's id, shown in the title.

    Returns:
        The table, panelled.
    """

    title = Text(f"Replay — {game_id}", style=f"bold {GOLD}")
    if not rows:
        return Panel(
            Align.center(Text("no rounds in this record", style=DIM)),
            title=title,
            border_style=BORDER_DIM,
            box=ROUNDED,
            width=WIDTH,
        )
    table = Table(
        show_header=True,
        header_style=f"bold {DIM}",
        border_style=BORDER_DIM,
        box=SQUARE,
        expand=True,
    )
    table.add_column("#", width=3, justify="right")
    table.add_column("Contract")
    table.add_column("Outcome", width=8)
    table.add_column(_team_abbr(TeamSide.NS), width=5, justify="right")
    table.add_column(_team_abbr(TeamSide.EW), width=5, justify="right")
    table.add_column("Verdict", width=len(NOT_STEPPABLE))
    for row in rows:
        totals = row.totals
        table.add_row(
            Text(str(row.number), style=FG if row.steppable else DIM),
            _format_replay_contract(row),
            Text(
                str(row.outcome) if row.outcome is not None else "—",
                style=DIM if row.outcome is None else FG,
            ),
            # A dot, not a zero: the record holds no score line for this
            # round, which is not the same as a round that scored nothing.
            Text(str(totals[TeamSide.NS]) if totals else "·", style=FG),
            Text(str(totals[TeamSide.EW]) if totals else "·", style=FG),
            _format_replay_verdict(row.verdict, steppable=row.steppable),
        )
    return Panel(
        table,
        title=title,
        border_style=BORDER_DIM,
        box=ROUNDED,
        width=WIDTH,
    )


def _format_replay_verdict(
    verdict: Optional[Verdict], *, steppable: bool
) -> Text:
    """The verdict cell, carrying the not-steppable note when it applies.

    The note wins over the verdict: a round the driver refuses cannot be
    watched whatever verification made of it, and that is the fact the
    viewer needs from this column first.

    Args:
        verdict: What verification made of the round, or ``None``.
        steppable: Whether the round can be replayed at all.

    Returns:
        The cell.
    """

    if not steppable:
        return Text(NOT_STEPPABLE, style=DIM)
    if verdict is None:
        return Text("—", style=DIM)
    style = {
        Verdict.VERIFIED: GREEN_FG,
        Verdict.PARTIAL: DIM,
        Verdict.SUSPECT: RED,
    }[verdict]
    return Text(str(verdict), style=style)


def _format_replay_contract(row: "ReplayRow") -> Text:
    """The contract cell: side, value, suit glyph, and any doubling.

    Args:
        row: The round's row.

    Returns:
        The cell; a dim ``all passed`` when the round was passed out.
    """

    contract = row.contract
    if contract is None:
        return Text("all passed", style=DIM)
    side = row.declarer_side
    text = Text()
    text.append(_team_abbr(side), style=f"bold {_team_color(side)}")
    # ``SlamLevel.__str__`` yields "Slam" / "Solo Slam"; a numeric value
    # renders as "80"…"180".
    text.append(f" {contract.value} ", style=FG)
    text.append(_suit_glyph(contract.suit), style=_suit_color(contract.suit))
    # Parenthesised rather than spelled "by S": the column is 13 cells
    # wide, and the four characters this saves are what let a three-digit
    # contract sit on one line instead of wrapping.
    text.append(f" ({_position_short(contract.declarer)})", style=DIM)
    if contract.redoubled_by is not None:
        text.append(" redoubled", style=f"bold {RED}")
    elif contract.doubled_by is not None:
        text.append(" doubled", style=RED)
    return text


def _replay_summary_prompt_text(rows: Sequence["ReplayRow"]) -> Text:
    """The picker's key list: the steppable range, and ``[q]`` to leave.

    Args:
        rows: The rows on screen.

    Returns:
        The prompt line. With nothing steppable it offers ``[q]`` alone
        rather than an empty range.
    """

    steppable = [row.number for row in rows if row.steppable]
    text = Text()
    if steppable:
        text.append(
            f"[{min(steppable)}-{max(steppable)}]", style=f"bold {FG}"
        )
        text.append(" step a round  ·  ", style=FG)
    text.append("[q]", style=f"bold {GOLD}")
    text.append(" quit", style=FG)
    return text


def _replay_summary_rejection_text(rows: Sequence["ReplayRow"]) -> Text:
    """The notice shown when the picker's answer names no steppable round.

    Args:
        rows: The rows on screen.

    Returns:
        The notice.
    """

    steppable = [row.number for row in rows if row.steppable]
    if not steppable:
        return Text(
            "✗ No round in this record can be replayed. [q] to quit.",
            style=RED,
        )
    return Text(
        "✗ Pick one of the rounds the table does not mark "
        f"'{NOT_STEPPABLE}' ({min(steppable)}-{max(steppable)}), or [q].",
        style=RED,
    )


def _replay_step_prompt_text(
    *, can_go_back: bool, can_skip_auction: bool = False
) -> Text:
    """The step keys, as one line, offering only the keys that apply.

    One line, short words and plain two-space gaps, because it is printed
    bare under a frame that already fills most of a terminal and must not
    wrap on an 80-column one: ``trick`` and ``round`` stand for "run to
    the end of the trick / round", ``skip bids`` for "run to the
    contract". ``[p]`` is omitted at a round's first stop, ``[a]`` once
    the auction is over.

    Args:
        can_go_back: Whether a previous stop exists to return to.
        can_skip_auction: Whether the round is still bidding.

    Returns:
        The key line.
    """

    keys: list[tuple[str, str]] = [
        ("[n]", "next"),
        ("[t]", "trick"),
        ("[r]", "round"),
    ]
    if can_skip_auction:
        keys.append(("[a]", "skip bids"))
    if can_go_back:
        keys.append(("[p]", "back"))
    text = Text()
    for key, label in keys:
        text.append(key, style=f"bold {FG}")
        text.append(f" {label}  ", style=FG)
    text.append("[q]", style=f"bold {GOLD}")
    text.append(" rounds", style=FG)
    return text


def _replay_step_rejection_text(
    *, can_go_back: bool, can_skip_auction: bool = False
) -> Text:
    """The notice shown when a step key is not one on offer.

    Args:
        can_go_back: Whether a previous stop exists, which decides
            whether ``[p]`` is named as an option.
        can_skip_auction: Whether the round is still bidding, which
            decides whether ``[a]`` is.

    Returns:
        The notice.
    """

    keys = ["[n]", "[t]", "[r]"]
    if can_skip_auction:
        keys.append("[a]")
    if can_go_back:
        keys.append("[p]")
    keys.append("[q]")
    return Text(
        f"✗ {' '.join(keys)}, or [Enter] for the next action.", style=RED
    )


def _replay_contract_text(round_: Any) -> Text:
    """The prompt line where ``[a]`` comes to rest: the contract just set.

    Args:
        round_: The round whose auction just closed.

    Returns:
        The prompt line.
    """

    text = Text()
    text.append("Contract set: ", style=f"bold {GOLD}")
    contract = getattr(round_, "contract", None)
    if contract is not None:
        text.append_text(_format_contract_short(contract, suit_glyph=True))
    text.append(" — [n] plays the first card.", style=FG)
    return text


def _replay_deal_text(round_: Any) -> Text:
    """The deal frame's prompt line: which round this is and who dealt it.

    The round is read through :func:`getattr`, as
    ``recap.py:_panel_round_recap`` does, so a stub round needs no more
    attributes than the line actually prints.

    Args:
        round_: The round just dealt.

    Returns:
        The prompt line.
    """

    dealer = getattr(round_, "dealer", None)
    seat = _position_short(dealer.position) if dealer else "—"
    text = Text()
    text.append(
        f"Round #{getattr(round_, 'round_number', '?')}",
        style=f"bold {GOLD}",
    )
    text.append(f" — {seat} deals. Hands face up.", style=FG)
    return text
