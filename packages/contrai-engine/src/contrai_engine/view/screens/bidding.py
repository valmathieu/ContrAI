"""Bidding screen rendering for the Rich terminal UI.

The auction view: the running bidding-history panel, the per-seat
bidding diamond (each seat shows its latest bid), the adaptive bid
prompt (only advertising actions legal for the next bidder), and the
brief AI post-bid announcement. Pure builders consuming the chronological
``list[Bid]`` history that ``RichView`` passes straight from the
:class:`~contrai_core.auction.Auction`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from contrai_core import Auction, BasePlayer, Position, TrumpVariant
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
)
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_engine.view.formatting import (
    _bid_label,
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
    RED,
    TITLE,
    TRUMP_GLYPH,
    YELLOW,
)


def _render_bidding_diamond(
    bidding_history: list,
    *,
    pending_position: Optional[Position],
    width: int,
) -> Text:
    """Render the 4-seat diamond with each player's latest bid.

    Mirrors :func:`contrai_engine.view.screens.trick._render_diamond`
    (N top, E right, S bottom, W left) but for the auction: each seat
    shows that player's most recent bid, so bids map onto the table
    spatially the same way cards do during play. The seat about to bid
    is marked ``?``; seats that have not bid yet show ``·``.

    ``bidding_history`` is the chronological ``list[Bid]`` the rest of
    the bidding renderer consumes, straight from ``Auction.bids``.
    """
    # Collapse the history to the latest bid standing at each seat;
    # a later bid by the same player overwrites the earlier one.
    latest_by_pos: dict[Position, Bid] = {}
    for bid in bidding_history:
        latest_by_pos[bid.player.position] = bid

    def slot(pos: Position) -> Text:
        t = Text()
        label = _position_short(pos)
        pcolor = _position_color(pos)
        t.append(f"{label} ", style=f"bold {pcolor}")
        if pos == pending_position:
            t.append("?", style=f"bold {YELLOW}")
        elif pos in latest_by_pos:
            t.append_text(_bid_label(latest_by_pos[pos]))
        else:
            t.append("·", style=DIM)
        return t

    # Same skeleton as _render_diamond (blank row, N, W/E, S), minus
    # the belote badges — those belong to the play phase.
    out = Text()
    out.append("\n")
    n = slot(Position.NORTH)
    pad_left = max(0, (width - n.cell_len) // 2)
    out.append(" " * pad_left)
    out.append_text(n)
    out.append("\n")
    w = slot(Position.WEST)
    e = slot(Position.EAST)
    used = w.cell_len + e.cell_len
    gap = max(2, width - used)
    out.append_text(w)
    out.append(" " * gap)
    out.append_text(e)
    out.append("\n")
    s = slot(Position.SOUTH)
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
    # cell holds at most "S 240 NT" (8 cells); pad to leave a gap.
    round_w = 4
    cell_w = 11
    body = Text()
    if not bids:
        body.append("(no bids yet)", style=DIM)
    else:
        for i, bid in enumerate(bids):
            if i % 4 == 0:
                # New bidding round: break the line (except the very
                # first) and emit the round-number gutter.
                if i > 0:
                    body.append("\n")
                label = f"#{i // 4 + 1}"
                body.append(label, style=f"bold {DIM}")
                body.append(" " * max(1, round_w - len(label)), style=FG)
            cell = Text()
            cell.append(_position_short(bid.player.position),
                        style=f"bold {_position_color(bid.player.position)}")
            cell.append(" ", style=FG)
            cell.append_text(_bid_label(bid))
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
    auction: Auction,
    next_player: Optional[BasePlayer] = None,
) -> Text:
    """Adaptive bid prompt: recap the last bid, then hint legal actions.

    The action hint is derived straight from
    :meth:`Auction.legal_actions` for ``next_player``, so it never
    advertises a move the auction would reject (e.g. doubling one's own
    partner, or a numeric raise once a Slam stands).
    """
    t = Text()
    # Recap the last event — for "West passed.".
    bids = auction.bids
    if bids:
        last_bid = bids[-1]
        label = _position_short(last_bid.player.position)
        if isinstance(last_bid, PassBid):
            t.append(f"{label} passed. ", style=FG)
        elif isinstance(last_bid, RedoubleBid):
            t.append(f"{label} redoubled. ", style=f"bold {GOLD}")
        elif isinstance(last_bid, DoubleBid):
            t.append(f"{label} doubled. ", style=f"bold {GOLD}")
        elif isinstance(last_bid, ContractBid):
            t.append(f"{label} bid {last_bid.value} ", style=FG)
            t.append(_suit_glyph(last_bid.suit), style=_suit_color(last_bid.suit))
            t.append(". ", style=FG)
    t.append("Your bid? ", style=FG)
    # Adaptive example — only advertise actions that are actually legal
    # for the next bidder. The enumerated legal action space is the
    # single source of truth here.
    legal = auction.legal_actions(next_player) if next_player is not None else ()
    if any(isinstance(b, RedoubleBid) for b in legal):
        # Contractor just got doubled: redouble is the only
        # meaningful active option besides passing.
        t.append("(pass / redouble)", style=DIM)
    else:
        # The worked contract example tracks the auction: show the
        # cheapest *legal* raise (100 once 90 stands), never the bare
        # 80 floor. Dropped entirely past 180, where only Slam remains.
        options: list[str] = []
        min_value = _cheapest_legal_raise(legal)
        if min_value is not None:
            options.append(f"'{min_value} H'")
            # An extended table offers no trump and all trump too, and
            # their input tags are worth advertising. Derived from the
            # legal set rather than from the ruleset, so a variant already
            # past its own ladder top simply stops being suggested.
            variants = sorted(
                {
                    b.suit for b in legal
                    if isinstance(b, ContractBid)
                    and isinstance(b.suit, TrumpVariant)
                    and b.value == min_value
                },
                key=lambda v: v.value,
            )
            options.extend(
                f"'{min_value} {TRUMP_GLYPH[v]}'" for v in variants
            )
        options.append("'pass'")
        if any(isinstance(b, DoubleBid) for b in legal):
            options.append("'double'")
        t.append(f"(e.g. {' / '.join(options)})", style=DIM)
    return t


def _cheapest_legal_raise(legal: Iterable[Bid]) -> Optional[int]:
    """Smallest numeric contract value among the given legal actions.

    Returns ``None`` when no numeric raise remains — past 180 only the
    Slam family is left, and its value is a ``SlamLevel``, not an
    ``int``, so it is filtered out here.
    """
    return min(
        (
            b.value
            for b in legal
            if isinstance(b, ContractBid) and isinstance(b.value, int)
        ),
        default=None,
    )


def _bid_rejection_text(auction: Auction, player: BasePlayer) -> Text:
    """Notice shown when the human's bid input does not parse.

    The worked contract example tracks the auction exactly like
    :func:`_bidding_prompt_text`: once 90 stands the notice suggests
    '100 h', and past 180 — where only the Slam family remains — the
    numeric example is dropped entirely.
    """
    examples = ["'pass'", "'double'", "'redouble'"]
    min_value = _cheapest_legal_raise(auction.legal_actions(player))
    if min_value is not None:
        examples.insert(0, f"'{min_value} h'")
    return Text(
        f"✗ Unrecognized bid. Try {', '.join(examples)}.",
        style=RED,
    )


def _ai_bid_announcement(player: BasePlayer, bid: Bid) -> Text:
    """Prompt text shown during an AI's brief post-bid pause."""
    label = _position_short(player.position)
    t = Text()
    if isinstance(bid, PassBid):
        t.append(f"{label} passes.", style=DIM)
    elif isinstance(bid, RedoubleBid):
        t.append(f"{label} redoubles.", style=f"bold {GOLD}")
    elif isinstance(bid, DoubleBid):
        t.append(f"{label} doubles.", style=f"bold {GOLD}")
    elif isinstance(bid, ContractBid):
        t.append(f"{label} bids {bid.value} ", style=FG)
        t.append(_suit_glyph(bid.suit), style=_suit_color(bid.suit))
        t.append(".", style=FG)
    else:
        t.append(f"{label} is thinking…", style=DIM)
    return t
