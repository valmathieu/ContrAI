"""Suit-oriented queries over any collection of cards.

The same four questions — how many of this suit, which cards of this
suit, do I hold this suit, do I hold this exact card — get asked from two
sides of the model that carry cards in *different* containers:

* :class:`~contrai_core.hand.Hand`, the mutable ``list[Card]`` a seat
  holds and plays out of;
* the frozen ``tuple[Card, ...]`` the card-play path works on
  (``PlayState.hands``, ``PlayObservation.hand``), whose immutability is
  what makes a determinization fork safe to hand to a search.

Neither container can be dropped in favour of the other, so the queries
live here instead: one implementation over ``Iterable[Card]``, consumed
by the ``Hand`` facade and by strategy code reading an observation's
tuple alike. Without it, the tuple side has no choice but to re-implement
the comprehensions ad hoc — and two implementations of "how many spades"
can drift.

Every function takes the cards first and never mutates them, so a
``list``, a ``tuple`` and a ``Hand`` (which is iterable) are all valid
arguments.
"""

from __future__ import annotations

from collections.abc import Iterable

from .card import Card
from .types import Rank, Suit


def count_suit(cards: Iterable[Card], suit: Suit) -> int:
    """Count the cards of a given suit.

    Args:
        cards: The cards to scan.
        suit: The suit to count.

    Returns:
        The number of cards whose ``.suit`` equals ``suit``.
    """
    return sum(1 for card in cards if card.suit == suit)


def cards_of_suit(cards: Iterable[Card], suit: Suit) -> list[Card]:
    """Collect the cards of a given suit into a new list.

    Args:
        cards: The cards to filter.
        suit: The suit to filter by.

    Returns:
        A new ``list[Card]`` holding the matching cards in their original
        order. The list is independent of the input: mutating it leaves
        the source collection untouched.
    """
    return [card for card in cards if card.suit == suit]


def has_suit(cards: Iterable[Card], suit: Suit) -> bool:
    """Report whether at least one card of ``suit`` is present.

    Short-circuits on the first match, so it is cheaper than
    ``bool(cards_of_suit(cards, suit))`` when only presence — not the
    cards themselves — is needed (e.g. lead-suit detection).

    Args:
        cards: The cards to scan.
        suit: The suit to look for.

    Returns:
        ``True`` if any card has ``.suit == suit``, ``False`` otherwise.
    """
    return any(card.suit == suit for card in cards)


def has_card(cards: Iterable[Card], suit: Suit, rank: Rank) -> bool:
    """Report whether one specific card is present.

    Asks through membership (``Card(suit, rank) in cards``) rather than
    scanning the two fields by hand. Since :class:`Card` is a frozen
    value object comparing by ``(suit, rank)``, membership is the single
    source of truth for "do I hold this card" — there is no parallel
    field-by-field comparison to drift out of sync with card equality.

    Args:
        cards: The cards to scan. Containers with their own
            ``__contains__`` (``list``, ``tuple``, ``Hand``) answer
            directly; any other iterable falls back to iteration.
        suit: The suit to look up.
        rank: The rank to look up.

    Returns:
        ``True`` if a card matching both ``suit`` and ``rank`` is
        present, ``False`` otherwise.
    """
    return Card(suit, rank) in cards
