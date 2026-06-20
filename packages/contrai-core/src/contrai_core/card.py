# Card class: represents a playing card

from __future__ import annotations

from dataclasses import dataclass

from .types import Suit, Rank


@dataclass(frozen=True, slots=True, repr=False)
class Card:
    """
    Represents a playing card for the game of contrée.

    Each card has a suit and a rank, and provides methods to get its point value and order,
    depending on whether it is a trump card or not.

    ``Card`` is an **immutable value object**: equality and hashing are by
    ``(suit, rank)``, so two distinct instances of the same physical card
    compare equal and hash alike, and cards can live in ``set``/``dict`` by
    value (mirroring the :class:`~contrai_core.bid.Bid` precedent). There is
    deliberately **no** ``__lt__`` — a card's *strength* is context-dependent
    (it depends on the trump suit) and is obtained via :meth:`get_order`, not
    by comparing cards directly.

    Attributes:
        suit (Suit): The suit of the card.
        rank (Rank): The rank of the card.

    Methods:
        __str__(): Returns a string representation of the card with suit symbol.
        __repr__(): Returns a string representation for debugging.
        get_points(trump_suit=None): Returns the point value of the card, considering trump.
        get_order(trump_suit=None): Returns the order of the card, considering trump.
    """

    suit: Suit
    rank: Rank

    # Normal points (non-trump), keyed by Rank
    NORMAL_POINTS = {
        Rank.SEVEN: 0,
        Rank.EIGHT: 0,
        Rank.NINE: 0,
        Rank.JACK: 2,
        Rank.QUEEN: 3,
        Rank.KING: 4,
        Rank.TEN: 10,
        Rank.ACE: 11,
    }
    # Trump points
    TRUMP_POINTS = {
        Rank.SEVEN: 0,
        Rank.EIGHT: 0,
        Rank.NINE: 14,
        Rank.JACK: 20,
        Rank.QUEEN: 3,
        Rank.KING: 4,
        Rank.TEN: 10,
        Rank.ACE: 11,
    }
    # Normal order (for trick-taking)
    NORMAL_ORDER = {
        Rank.SEVEN: 0,
        Rank.EIGHT: 1,
        Rank.NINE: 2,
        Rank.JACK: 3,
        Rank.QUEEN: 4,
        Rank.KING: 5,
        Rank.TEN: 6,
        Rank.ACE: 7,
    }
    # Trump order
    TRUMP_ORDER = {
        Rank.SEVEN: 0,
        Rank.EIGHT: 1,
        Rank.QUEEN: 2,
        Rank.KING: 3,
        Rank.TEN: 4,
        Rank.ACE: 5,
        Rank.NINE: 6,
        Rank.JACK: 7,
    }
    SUIT_SYMBOLS = {
        Suit.SPADES: "♠",
        Suit.HEARTS: "♥",
        Suit.DIAMONDS: "♦",
        Suit.CLUBS: "♣",
    }

    def __str__(self) -> str:
        return f"{self.rank.value} {Card.SUIT_SYMBOLS[self.suit]}"

    def __repr__(self) -> str:
        return f"Card({self.suit!r}, {self.rank!r})"

    def get_points(self, trump_suit: Suit | None = None) -> int:
        if trump_suit and self.suit == trump_suit:
            return Card.TRUMP_POINTS[self.rank]
        return Card.NORMAL_POINTS[self.rank]

    def get_order(self, trump_suit: Suit | None = None) -> int:
        if trump_suit and self.suit == trump_suit:
            return Card.TRUMP_ORDER[self.rank]
        return Card.NORMAL_ORDER[self.rank]
