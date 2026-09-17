"""Rebuilding what the wire leaves out: the four hands and the last trick.

The socket is economical in two ways, and both have to be undone before a
round can be recorded.

It sends the deck's order **before the deal**, not the hands — the client
deals for itself. Contrée deals in packets of three, two and three, going
round the table from the seat after the dealer, so the hands follow from the
stock and one fact: who received first.

And it sends **twenty-eight plays, not thirty-two**. By the eighth trick every
seat holds one card, so there is nothing to decide and nothing to transmit;
the client plays them out on its own. The record has a flag for exactly this
(``derived``), so the four cards are reconstructed here and marked rather than
quietly omitted.

Neither function decides a trick winner. That is ``contrai_core``'s job, and
the verifier's job is to check core's answer against the site's — a parser
that also computed winners would be checking itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from contrai_core import Card, Position

from ..exceptions import ParseError

#: Contrée's deal: three cards, then two, then three, each packet going once
#: round the table.
DEAL_PACKETS = (3, 2, 3)

#: How many cards a full deck holds, and how many each seat ends up with.
DECK_SIZE = 32
HAND_SIZE = 8


def deal_hands(
    stock: Sequence[Card],
    first_receiver: Position,
    rotation: Sequence[Position],
) -> dict[Position, tuple[Card, ...]]:
    """Deal a stock 3-2-3 around the table.

    Args:
        stock: The deck in its pre-deal order, as the wire sent it.
        first_receiver: The seat after the dealer, which takes the first
            packet.
        rotation: The four seats in the table's own order.

    Returns:
        Each seat's eight cards, in the order they were dealt.

    Raises:
        ParseError: If the stock is not a full deck. A short stock is what a
            mis-decoded payload looks like, and dealing it would hand three
            seats a full hand and one a partial.
    """

    if len(stock) != DECK_SIZE:
        raise ParseError(
            f"A deal needs {DECK_SIZE} cards, the wire gave {len(stock)}"
        )
    start = rotation.index(first_receiver)
    order = [rotation[(start + step) % len(rotation)] for step in range(len(rotation))]
    hands: dict[Position, list[Card]] = {seat: [] for seat in order}
    cursor = 0
    for packet in DEAL_PACKETS:
        for seat in order:
            hands[seat].extend(stock[cursor : cursor + packet])
            cursor += packet
    return {seat: tuple(cards) for seat, cards in hands.items()}


def resolve_dealer(
    stock: Sequence[Card],
    played: Mapping[Position, set[Card]],
    rotation: Sequence[Position],
) -> Position | None:
    """Find the deal whose hands contain every card actually played.

    Only four candidates exist, not twenty-four: the rotation is fixed and
    only the entry point moves, so the dealer is whichever of the four deals
    is consistent with what the seats were seen to play.

    Args:
        stock: The deck in its pre-deal order.
        played: Which cards each seat was observed playing.
        rotation: The four seats in the table's own order.

    Returns:
        The dealer, or ``None`` when the round does not pin one down — an
        all-pass round has no plays at all, and a round cut short may fit
        more than one deal. Returning nothing is the point: a guessed dealer
        produces a record that replays with the auction starting at the wrong
        seat, and nothing downstream could tell.
    """

    if len(stock) != DECK_SIZE or not any(played.values()):
        return None
    candidates = [
        first_receiver
        for first_receiver in rotation
        if _fits(deal_hands(stock, first_receiver, rotation), played)
    ]
    if len(candidates) != 1:
        return None
    index = rotation.index(candidates[0])
    return rotation[(index - 1) % len(rotation)]


def final_trick(
    hands: Mapping[Position, tuple[Card, ...]],
    played: set[Card],
    leader: Position,
    rotation: Sequence[Position],
) -> tuple[tuple[Position, Card], ...]:
    """Reconstruct the eighth trick, which is forced and never transmitted.

    Args:
        hands: The four dealt hands.
        played: Every card already seen played this round.
        leader: The seat that won trick seven, and so leads this one.
        rotation: The four seats in the table's own order.

    Returns:
        ``(seat, card)`` in play order, starting at the leader.

    Raises:
        ParseError: If any seat holds other than one card. That means the
            capture ended mid-round, and reconstructing from an incomplete
            hand would not complete a trick — it would invent one.
    """

    remaining: dict[Position, Card] = {}
    for seat, hand in hands.items():
        left = [card for card in hand if card not in played]
        if len(left) != 1:
            raise ParseError(
                f"{seat.value} holds {len(left)} cards before the last trick, "
                "so the round was not observed to its end"
            )
        remaining[seat] = left[0]

    start = rotation.index(leader)
    order = [rotation[(start + step) % len(rotation)] for step in range(len(rotation))]
    return tuple((seat, remaining[seat]) for seat in order)


def _fits(
    hands: Mapping[Position, tuple[Card, ...]],
    played: Mapping[Position, set[Card]],
) -> bool:
    """Whether every observed play comes out of the hand that deal gave it."""

    return all(cards <= set(hands[seat]) for seat, cards in played.items())
