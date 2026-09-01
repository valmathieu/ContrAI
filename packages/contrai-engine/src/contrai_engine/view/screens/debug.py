"""Debug-mode panel rendering for the Rich terminal UI.

Thin Rich panels over the Rich-free projections in
:mod:`contrai_engine.debug_state` (``cards_still_in_play`` /
``hand_snapshot`` / ``last_decisions``). These renderers are deliberately throwaway — the
projections underneath are the stable surface; the panels here are free
to change shape as the debug mode evolves.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from contrai_core import BasePlayer, Card, ContractSuit, Position, Suit
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_engine.debug_state import (
    cards_still_in_play,
    hand_snapshot,
    last_decisions,
)
from contrai_engine.view.formatting import (
    _format_card_compact,
    _position_color,
    _position_short,
    _rank_short,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.theme import BORDER, DIM, TITLE

# Row order for the all-hands panel: the canonical anticlockwise
# seating (N, W, S, E) that ``Position`` itself defines and that
# ``debug_state.deal_lines`` uses for the log dump. Panel and log list
# seats identically, so a reader can cross-reference the debug strip
# against ``contrai-debug.log`` line for line.
_SEAT_ORDER = (Position.NORTH, Position.WEST, Position.SOUTH, Position.EAST)


def _format_card_row(cards: list[Card]) -> Text:
    """Space-joined compact card labels, e.g. ``"A♠ K♠ Q♥"``.

    Args:
        cards: The cards to render, in the order given — callers are
            responsible for sorting.

    Returns:
        A ``Text`` with one :func:`_format_card_compact` label per
        card, separated by single spaces. Empty when ``cards`` is
        empty; callers substitute their own placeholder for that case.
    """
    row = Text()
    for index, card in enumerate(cards):
        if index:
            row.append(" ")
        row.append_text(_format_card_compact(card))
    return row


def _format_rank_row(cards: list[Card]) -> Text:
    """Space-joined rank labels, e.g. ``"A 10 7"`` — suit left implicit.

    Used by the in-play summary, where every group is already headed by
    its own suit glyph: repeating that glyph on each card would roughly
    double the line's width without adding information.

    Args:
        cards: The cards to render, all of one suit, in the order
            given — callers are responsible for sorting.

    Returns:
        A ``Text`` with one short rank label per card, separated by
        single spaces and colored by the card's suit.
    """
    row = Text()
    for index, card in enumerate(cards):
        if index:
            row.append(" ")
        row.append(
            _rank_short(card.rank),
            style=f"bold {_suit_color(card.suit)}",
        )
    return row


def _panel_debug_hands(
    players: Iterable[BasePlayer],
    trump: Optional[ContractSuit],
    *,
    seed: Optional[int] = None,
) -> Panel:
    """The debug strip's all-hands panel: every seat's cards, face up.

    One row per seat in canonical N/W/S/E seating order, each showing that
    player's current hand via :func:`~contrai_engine.debug_state.hand_snapshot`
    (trump-first sorted, the same ordering the human's own hand row
    uses). A closing line groups every card still held at the table by
    suit via :func:`~contrai_engine.debug_state.cards_still_in_play`,
    so a reader can sanity-check the unplayed cards at a glance.

    Args:
        players: The four seats to render, in any order — rows are
            laid out in the canonical N/W/S/E order regardless of the
            order ``players`` iterates in.
        trump: The active trump, or ``None`` before a contract exists.
        seed: The deal's random seed, or ``None`` when it wasn't
            fixed. Shown in the title only when given.

    Returns:
        A single ``Panel`` with the four seat rows and the closing
        in-play summary line.
    """
    players = list(players)
    by_position = {player.position: player for player in players}

    title = "Debug — all hands"
    if seed is not None:
        title += f" · seed {seed}"

    body = Text()
    for position in _SEAT_ORDER:
        player = by_position.get(position)
        if player is None:
            # Defensive only — every real caller supplies all four
            # seats; a partial roster just skips the missing row
            # instead of raising.
            continue
        body.append(
            f"{_position_short(position)}: ",
            style=f"bold {_position_color(position)}",
        )
        hand = hand_snapshot(player, trump)
        if hand:
            body.append_text(_format_card_row(hand))
        else:
            body.append("(empty)", style=DIM)
        body.append("\n")

    body.append("\n")
    body.append("In play: ", style=DIM)
    grouped = cards_still_in_play(players)
    for index, suit in enumerate(Suit):
        if index:
            body.append(" · ", style=DIM)
        body.append(_suit_glyph(suit), style=_suit_color(suit))
        body.append(" ")
        cards = grouped.get(suit, [])
        if cards:
            body.append_text(_format_rank_row(cards))
        else:
            body.append("—", style=DIM)

    return Panel(
        body,
        title=Text(title, style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )



def _panel_ai_rationale(round_) -> Panel:
    """The debug strip's rationale panel: why each AI seat acted.

    One block per recent AI decision — oldest first, so the newest is
    printed *below* the explanations already on screen — reading the
    Rich-free projection in
    :func:`~contrai_engine.debug_state.last_decisions`. Each shows what
    was played or bid, the rule that fired, the sentence explaining it,
    the alternatives weighed, and any table knob the branch cited.

    A human seat never appears: ``Round`` records no decision for it, so
    a person's reasoning stays their own.

    Args:
        round_: The round to read, or ``None`` before one exists.

    Returns:
        A single ``Panel``, showing a dim placeholder while no AI seat
        has decided anything yet.
    """
    entries = last_decisions(round_)

    body = Text()
    if not entries:
        body.append("(no AI decision yet this round)", style=DIM)
    for index, entry in enumerate(entries):
        if index:
            body.append("\n")
        body.append(f"{entry['action']} ", style="bold")
        body.append(entry["rule"], style=f"bold {TITLE}")
        body.append("\n  ")
        body.append(entry["detail"], style=DIM)
        if entry["considered"]:
            body.append("\n  over: ", style=DIM)
            body.append(" · ".join(entry["considered"]), style=DIM)
        for citation in entry["citations"]:
            body.append("\n  ", style=DIM)
            body.append(
                f"{citation['knob']} = {citation['value']}",
                style=f"bold {DIM}",
            )
            body.append(f" — {citation['effect']}", style=DIM)

    return Panel(
        body,
        title=Text("Debug — AI rationale", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
    )
def _autoplay_pause_text(message: str) -> Text:
    """Dim autoplay notice replacing the usual press-Enter prompt.

    Args:
        message: The event copy to show, e.g. what an AI seat just did.

    Returns:
        A dim ``Text`` reading ``"(autoplay) <message>"``.
    """
    return Text(f"(autoplay) {message}", style=DIM)
