"""Bidding screen rendering for the Rich terminal UI.

The auction view: the running bidding-history panel, the per-seat
bidding diamond (each seat shows its latest bid), the adaptive bid
prompt (only advertising actions legal for the next bidder), and the
brief AI post-bid announcement. Pure builders consuming the legacy
``(player, wire_bid)`` history that ``RichView`` projects.
"""

from __future__ import annotations

from typing import Optional

from contrai_core import BasePlayer
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_engine.view.bidding_rules import (
    _double_available_to,
    _min_legal_contract_value,
    _redouble_available_to,
)
from contrai_engine.view.formatting import (
    _bid_legacy_label,
    _position_color,
    _position_short,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.theme import (
    BORDER,
    DIM,
    FG,
    GOLD,
    TITLE,
    YELLOW,
)


def _render_bidding_diamond(
    bidding_history: list,
    *,
    pending_position: Optional[str],
    width: int,
) -> Text:
    """Render the 4-seat diamond with each player's latest bid.

    Mirrors :func:`contrai_engine.view.screens.trick._render_diamond`
    (N top, E right, S bottom, W left) but for the auction: each seat
    shows that player's most recent bid, so announces map onto the table
    spatially the same way cards do during play. The seat about to bid
    is marked ``?``; seats that have not bid yet show ``·``.

    ``bidding_history`` is the legacy ``(player, wire_bid)`` list the
    rest of the bidding renderer already consumes, where ``wire_bid``
    is one of ``"Pass"`` / ``"Double"`` / ``"Redouble"`` / a
    ``(value, suit)`` tuple.
    """
    # Collapse the history to the latest bid standing at each seat;
    # a later bid by the same player overwrites the earlier one.
    latest_by_pos: dict[str, str | tuple] = {}
    for player, bid in bidding_history:
        latest_by_pos[player.position] = bid

    def slot(pos: str) -> Text:
        t = Text()
        label = _position_short(pos)
        pcolor = _position_color(pos)
        t.append(f"{label} ", style=f"bold {pcolor}")
        if pos == pending_position:
            t.append("?", style=f"bold {YELLOW}")
        elif pos in latest_by_pos:
            t.append_text(_bid_legacy_label(latest_by_pos[pos]))
        else:
            t.append("·", style=DIM)
        return t

    # Same skeleton as _render_diamond (blank row, N, W/E, S), minus
    # the belote badges — those belong to the play phase.
    out = Text()
    out.append("\n")
    n = slot("North")
    pad_left = max(0, (width - n.cell_len) // 2)
    out.append(" " * pad_left)
    out.append_text(n)
    out.append("\n")
    w = slot("West")
    e = slot("East")
    used = w.cell_len + e.cell_len
    gap = max(2, width - used)
    out.append_text(w)
    out.append(" " * gap)
    out.append_text(e)
    out.append("\n")
    s = slot("South")
    pad_left = max(0, (width - s.cell_len) // 2)
    out.append(" " * pad_left)
    out.append_text(s)
    return out


def _panel_bidding_history(bids: list) -> Panel:
    """One-line-per-round history of bids so far.

    Each line starts with the bidding-round number (``#1``, ``#2``,
    …) and lays the four seats out in fixed-width columns so bids
    line up vertically across rounds:
        #1  S Pass     E Pass     N 80 ♥     W Pass
        #2  S 100 ♥    E Pass     N 130 ♥    W ×2
    """
    # Fixed column widths so cells stack in vertical lanes. The bid
    # cell holds at most "S 180 ♥" (7 cells); pad to leave a gap.
    round_w = 4
    cell_w = 11
    body = Text()
    if not bids:
        body.append("(no bids yet)", style=DIM)
    else:
        for i, (player, bid) in enumerate(bids):
            if i % 4 == 0:
                # New bidding round: break the line (except the very
                # first) and emit the round-number gutter.
                if i > 0:
                    body.append("\n")
                label = f"#{i // 4 + 1}"
                body.append(label, style=f"bold {DIM}")
                body.append(" " * max(1, round_w - len(label)), style=FG)
            cell = Text()
            cell.append(_position_short(player.position),
                        style=f"bold {_position_color(player.position)}")
            cell.append(" ", style=FG)
            cell.append_text(_bid_legacy_label(bid))
            # Right-pad the cell to keep the seats in vertical lanes.
            body.append_text(cell)
            body.append(" " * max(1, cell_w - cell.cell_len), style=FG)
    return Panel(
        body,
        title=Text("Bidding so far", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )


def _bidding_prompt_text(
    history: list,
    next_player: Optional[BasePlayer] = None,
) -> Text:
    t = Text()
    # Find what last non-self event was — for "West passed.".
    if history:
        last_player, last_bid = history[-1]
        label = _position_short(last_player.position)
        if last_bid == "Pass":
            t.append(f"{label} passed. ", style=FG)
        elif last_bid == "Double":
            t.append(f"{label} doubled. ", style=f"bold {GOLD}")
        elif last_bid == "Redouble":
            t.append(f"{label} redoubled. ", style=f"bold {GOLD}")
        elif isinstance(last_bid, tuple):
            value, suit = last_bid
            t.append(f"{label} bid {value} ", style=FG)
            t.append(_suit_glyph(suit), style=_suit_color(suit))
            t.append(". ", style=FG)
    t.append("Your bid? ", style=FG)
    # Adaptive example — only advertise actions that are actually
    # legal for the next bidder, so the hint never invites a move
    # the auction will reject (e.g. doubling one's own partner).
    if next_player is not None and _redouble_available_to(history, next_player):
        # Contractor just got doubled: redouble is the only
        # meaningful active option besides passing.
        t.append("(pass / redouble)", style=DIM)
    else:
        # The worked contract example tracks the auction: show the
        # cheapest *legal* raise (100 once 90 stands), never the bare
        # 80 floor, so the hint can't suggest a bid the auction would
        # reject. Dropped entirely past 180, where only Slam remains.
        options: list[str] = []
        min_value = _min_legal_contract_value(history)
        if min_value is not None:
            options.append(f"'{min_value} H'")
        options.append("'pass'")
        if next_player is not None and _double_available_to(history, next_player):
            options.append("'double'")
        t.append(f"(e.g. {' / '.join(options)})", style=DIM)
    return t


def _ai_bid_announcement(player: BasePlayer, bid) -> Text:
    """Prompt text shown during an AI's brief post-bid pause."""
    label = _position_short(player.position)
    t = Text()
    if bid == "Pass":
        t.append(f"{label} passes.", style=DIM)
    elif bid == "Double":
        t.append(f"{label} doubles.", style=f"bold {GOLD}")
    elif bid == "Redouble":
        t.append(f"{label} redoubles.", style=f"bold {GOLD}")
    elif isinstance(bid, tuple):
        value, suit = bid
        t.append(f"{label} bids {value} ", style=FG)
        t.append(_suit_glyph(suit), style=_suit_color(suit))
        t.append(".", style=FG)
    else:
        t.append(f"{label} is thinking…", style=DIM)
    return t
