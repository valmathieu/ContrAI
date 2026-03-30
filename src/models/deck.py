"""
Deck and Card representations for La Contrée.

This module defines the basic Object-Oriented representations of cards,
suit slots (abstract — suit identity is irrelevant for probability analysis),
ranks, and the 32-card deck used in the game.
"""

from enum import Enum
from dataclasses import dataclass


class SuitSlot(Enum):
    """
    Abstract suit slots for La Contrée.

    Suit identity (Hearts vs Clubs) does not affect probability calculations —
    only whether a slot is trump or non-trump matters.  The three non-trump
    slots are distinguished purely for grouping cards in the hand.
    """

    TRUMP  = "trump"   # 🃏 — the declared trump suit
    BLUE   = "blue"    # 🔵 — first non-trump slot
    GREEN  = "green"   # 🟢 — second non-trump slot
    PURPLE = "purple"  # 🟣 — third non-trump slot

    @property
    def emoji(self) -> str:
        """Display emoji for this slot."""
        _map = {"trump": "🃏", "blue": "🔵", "green": "🟢", "purple": "🟣"}
        return _map[self.value]

    @property
    def label(self) -> str:
        """Human-readable label for this slot."""
        _map = {"trump": "Trump", "blue": "Blue", "green": "Green", "purple": "Purple"}
        return _map[self.value]

    @property
    def color(self) -> str:
        """CSS/display color associated with this slot."""
        _map = {"trump": "#FFD700", "blue": "#4A90D9", "green": "#5CB85C", "purple": "#9B59B6"}
        return _map[self.value]


class Rank(Enum):
    """Enumeration of the 8 card ranks in a Contrée deck."""

    SEVEN = "7"
    EIGHT = "8"
    NINE  = "9"
    TEN   = "10"
    JACK  = "J"
    QUEEN = "Q"
    KING  = "K"
    ACE   = "A"

    def point_value(self, is_trump: bool) -> int:
        """
        Return the Contrée point value for this rank.

        In Contrée the Jack and Nine of trump are worth far more than in other
        suits — they are the two highest trumps.  All other suits share the
        standard Belote scale.

        Args:
            is_trump: True if this card belongs to the trump SuitSlot.

        Returns:
            int: Point value in the range 0–20.
        """
        if is_trump:
            # Trump scale: J=20, 9=14, A=11, 10=10, K=4, Q=3, 8/7=0
            trump_values: dict["Rank", int] = {
                Rank.JACK:  20,
                Rank.NINE:  14,
                Rank.ACE:   11,
                Rank.TEN:   10,
                Rank.KING:   4,
                Rank.QUEEN:  3,
            }
            return trump_values.get(self, 0)
        else:
            # Non-trump scale: A=11, 10=10, K=4, Q=3, J=2, 9/8/7=0
            non_trump_values: dict["Rank", int] = {
                Rank.ACE:   11,
                Rank.TEN:   10,
                Rank.KING:   4,
                Rank.QUEEN:  3,
                Rank.JACK:   2,
            }
            return non_trump_values.get(self, 0)


@dataclass(frozen=True)
class Card:
    """
    Represents a single playing card.

    Attributes:
        rank (Rank): The rank of the card (7–A).
        suit (SuitSlot): The abstract suit slot (TRUMP, BLUE, GREEN, or PURPLE).
    """

    rank: Rank
    suit: SuitSlot

    def __str__(self) -> str:
        """String representation, e.g. 'J 🃏' or 'A 🔵'."""
        return f"{self.rank.value} {self.suit.emoji}"

    @property
    def id(self) -> str:
        """Short identifier, e.g. 'Jt', 'Ab', '7g'."""
        return f"{self.rank.value}{self.suit.value[0]}"

    @property
    def point_value(self) -> int:
        """Contrée point value of this card given its slot."""
        return self.rank.point_value(is_trump=self.suit == SuitSlot.TRUMP)


class Deck:
    """Represents a standard 32-card deck for La Contrée."""

    def __init__(self) -> None:
        """Initialize the deck with all 32 cards (8 ranks × 4 suit slots)."""
        self.cards: list[Card] = [
            Card(rank, slot) for slot in SuitSlot for rank in Rank
        ]

    def get_all_cards(self) -> list[Card]:
        """
        Return all 32 cards in the deck.

        Returns:
            list[Card]: All 32 cards.
        """
        return self.cards
