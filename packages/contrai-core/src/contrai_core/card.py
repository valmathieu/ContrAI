# Card class: represents a playing card

from __future__ import annotations

from .types import Suit, Rank


class Card:
    """
    Represents a playing card for the game of contrée.

    Each card has a suit and a rank, and provides methods to get its point value and order,
    depending on whether it is a trump card or not.

    Attributes:
        suit (Suit): The suit of the card.
        rank (Rank): The rank of the card.
        points_normal (int): The point value of the card in a non-trump suit.
        points_trump (int): The point value of the card in the trump suit.
        order_normal (int): The order of the card in a non-trump suit.
        order_trump (int): The order of the card in the trump suit.

    Methods:
        __str__(): Returns a string representation of the card with suit symbol.
        __repr__(): Returns a string representation for debugging.
        get_points(trump_suit=None): Returns the point value of the card, considering trump.
        get_order(trump_suit=None): Returns the order of the card, considering trump.
    """

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

    def __init__(self, suit: Suit, rank: Rank):
        self.suit = suit
        self.rank = rank
        self.points_normal = Card.NORMAL_POINTS[rank]
        self.points_trump = Card.TRUMP_POINTS[rank]
        self.order_normal = Card.NORMAL_ORDER[rank]
        self.order_trump = Card.TRUMP_ORDER[rank]

    def __str__(self) -> str:
        return f"{self.rank.value} {Card.SUIT_SYMBOLS[self.suit]}"

    def __repr__(self) -> str:
        return f"Card({self.suit!r}, {self.rank!r})"

    def get_points(self, trump_suit: Suit | None = None) -> int:
        if trump_suit and self.suit == trump_suit:
            return self.points_trump
        return self.points_normal

    def get_order(self, trump_suit: Suit | None = None) -> int:
        if trump_suit and self.suit == trump_suit:
            return self.order_trump
        return self.order_normal
