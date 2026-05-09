"""Enum types for card suits and ranks.

Shared across all ContrAI packages. Enum values preserve the engine's display
strings (``Rank.JACK.value == "Jack"``, ``Suit.SPADES.value == "Spades"``) so
``str(card)`` output is unchanged.
"""

from enum import Enum


class Suit(Enum):
    """Card suits in Contree.

    ``NO_TRUMP`` is a contract trump option only — no physical card has it.
    Use :data:`CARD_SUITS` (or compare against ``Suit.NO_TRUMP``) when
    iterating only over real card suits.
    """

    SPADES = "Spades"
    HEARTS = "Hearts"
    DIAMONDS = "Diamonds"
    CLUBS = "Clubs"
    NO_TRUMP = "NoTrump"


#: The four card-bearing suits (excludes ``Suit.NO_TRUMP``).
CARD_SUITS = (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)


class Rank(Enum):
    """The eight card ranks in a Contree deck (32-card subset: 7..Ace)."""

    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "Jack"
    QUEEN = "Queen"
    KING = "King"
    ACE = "Ace"
