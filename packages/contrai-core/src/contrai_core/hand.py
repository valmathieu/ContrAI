"""Hand: typed container for the cards a player currently holds."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from . import card_queries
from .card import Card
from .types import Suit, Rank


class Hand:
    """A player's cards in hand.

    Wraps an internal ``list[Card]`` and forwards the mutation and
    inspection operations the engine relies on: ``append``, ``extend``,
    ``remove``, ``clear``, ``in``, iteration and ``len``. The surface
    stops there deliberately — a hand is a bag of cards a seat draws
    from and plays out of, not a sequence anyone should address by
    position. Callers wanting a positional or list-shaped view take
    ``list(hand)`` explicitly.

    On top of those it exposes the suit queries as methods, each a
    one-line delegate to :mod:`contrai_core.card_queries` — that module
    holds the single implementation the frozen ``tuple[Card, ...]`` of
    the card-play path reads through as well, so neither side has to
    re-implement the comprehensions ad hoc and the two can never drift.

    Hands start empty and are filled incrementally by :class:`Deck.deal`;
    the engine clears them between rounds. No size invariant is enforced
    anywhere — a hand holds whatever it has been given.

    Attributes:
        cards: The underlying list of :class:`Card` objects. Exposed for
            tests and introspection; in production code prefer the Hand
            methods over mutating this list directly.
    """

    def __init__(self, cards: Iterable[Card] | None = None) -> None:
        """Build a hand from an optional iterable of cards.

        Args:
            cards: Optional initial cards. The iterable is materialised
                into a fresh internal list so each ``Hand`` owns its own
                storage (no shared-mutable-default trap).
        """
        self.cards: list[Card] = list(cards) if cards is not None else []

    # ------------------------------------------------------------------
    # mutation and inspection
    # ------------------------------------------------------------------

    def append(self, card: Card) -> None:
        """Append a single card to the hand.

        Args:
            card: The card to add.
        """
        self.cards.append(card)

    def extend(self, cards: Iterable[Card]) -> None:
        """Append every card from ``cards`` to the hand.

        Args:
            cards: An iterable of cards to add (order is preserved).
        """
        self.cards.extend(cards)

    def remove(self, card: Card) -> None:
        """Remove the first occurrence of ``card`` from the hand.

        Args:
            card: The card to remove.

        Raises:
            ValueError: If ``card`` is not in the hand (delegated from
                the underlying list).
        """
        self.cards.remove(card)

    def clear(self) -> None:
        """Remove every card from the hand, leaving it empty."""
        self.cards.clear()

    def __contains__(self, card: object) -> bool:
        """Return ``True`` iff ``card`` is currently in the hand."""
        return card in self.cards

    def __iter__(self) -> Iterator[Card]:
        """Iterate over the cards in insertion order."""
        return iter(self.cards)

    def __len__(self) -> int:
        """Return the number of cards currently in the hand."""
        return len(self.cards)

    # ------------------------------------------------------------------
    # query helpers
    # ------------------------------------------------------------------

    def count_suit(self, suit: Suit) -> int:
        """Count the number of cards of a given suit in the hand.

        Args:
            suit: The suit to count.

        Returns:
            The number of cards in the hand whose ``.suit`` equals
            ``suit``.
        """
        return card_queries.count_suit(self.cards, suit)

    def has_suit(self, suit: Suit) -> bool:
        """Return ``True`` iff the hand holds at least one card of ``suit``.

        Args:
            suit: The suit to look for.

        Returns:
            ``True`` if any card in the hand has ``.suit == suit``,
            ``False`` otherwise.
        """
        return card_queries.has_suit(self.cards, suit)

    def has_card(self, suit: Suit, rank: Rank) -> bool:
        """Return ``True`` iff a specific card is in the hand.

        Args:
            suit: The suit to look up.
            rank: The rank to look up.

        Returns:
            ``True`` if a card matching both ``suit`` and ``rank`` is
            present, ``False`` otherwise.
        """
        return card_queries.has_card(self.cards, suit, rank)

    def cards_of_suit(self, suit: Suit) -> list[Card]:
        """Return the cards of a given suit as a new list.

        Args:
            suit: The suit to filter by.

        Returns:
            A new ``list[Card]`` containing the matching cards in their
            order within the hand. Mutating the returned list does not
            affect the hand.
        """
        return card_queries.cards_of_suit(self.cards, suit)

    def __repr__(self) -> str:
        """Return a debug representation listing every card."""
        return f"Hand({self.cards!r})"
