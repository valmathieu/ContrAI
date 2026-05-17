"""Hand: typed container for the cards a player currently holds."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .card import Card
from .types import Suit, Rank


class Hand:
    """A player's cards in hand.

    Wraps an internal ``list[Card]`` and forwards the list operations the
    engine relies on (``append``, ``extend``, ``remove``, ``clear``,
    ``in``, iteration, ``len``, indexing) so existing call sites keep
    working unchanged. Adds query helpers (suit/rank counting, lookup by
    suit) that AI code would otherwise re-implement ad hoc with list
    comprehensions.

    Hands start empty and are filled incrementally by :class:`Deck.deal`;
    the engine clears them between rounds. No size invariant is enforced
    anywhere — call :meth:`is_complete` if you need to assert a
    fully-dealt 8-card hand.

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
    # list-compatible API
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

    def __getitem__(self, idx: int | slice):
        """Index into the hand by integer or slice.

        Args:
            idx: An ``int`` (returns a :class:`Card`) or ``slice``
                (returns a ``list[Card]``).
        """
        return self.cards[idx]

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
        return sum(1 for card in self.cards if card.suit == suit)

    def count_rank(self, rank: Rank) -> int:
        """Count the number of cards of a given rank in the hand.

        Args:
            rank: The rank to count.

        Returns:
            The number of cards in the hand whose ``.rank`` equals
            ``rank``.
        """
        return sum(1 for card in self.cards if card.rank == rank)

    def has_card(self, suit: Suit, rank: Rank) -> bool:
        """Return ``True`` iff a specific card is in the hand.

        Args:
            suit: The suit to look up.
            rank: The rank to look up.

        Returns:
            ``True`` if a card matching both ``suit`` and ``rank`` is
            present, ``False`` otherwise.
        """
        return any(c.suit == suit and c.rank == rank for c in self.cards)

    def cards_of_suit(self, suit: Suit) -> list[Card]:
        """Return the cards of a given suit as a new list.

        Args:
            suit: The suit to filter by.

        Returns:
            A new ``list[Card]`` containing the matching cards in their
            order within the hand. Mutating the returned list does not
            affect the hand.
        """
        return [card for card in self.cards if card.suit == suit]

    def is_complete(self) -> bool:
        """Return ``True`` iff the hand contains exactly 8 unique cards.

        A full Contree hand. Not enforced anywhere by the class itself;
        this is a convenience for tests and downstream invariant checks.
        Uniqueness is judged by ``(suit, rank)`` pairs.
        """
        if len(self.cards) != 8:
            return False
        return len({(c.suit, c.rank) for c in self.cards}) == 8

    def __repr__(self) -> str:
        """Return a debug representation listing every card."""
        return f"Hand({self.cards!r})"
