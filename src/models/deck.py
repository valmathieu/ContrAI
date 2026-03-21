"""
Deck and Card representations for La Contrée.

This module defines the basic Object-Oriented representations of cards,
suits, ranks, and the 32-card deck used in the game.
"""

from enum import Enum
from dataclasses import dataclass

class Suit(Enum):
    """Enumeration of the 4 card suits."""
    HEARTS = "Hearts"
    DIAMONDS = "Diamonds"
    CLUBS = "Clubs"
    SPADES = "Spades"

class Rank(Enum):
    """Enumeration of the 8 card ranks in a Contrée deck."""
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"
    ACE = "A"

@dataclass(frozen=True)
class Card:
    """
    Represents a single playing card.
    
    Attributes:
        rank (Rank): The rank of the card (7, 8, 9, 10, J, Q, K, A).
        suit (Suit): The suit of the card.
    """
    rank: Rank
    suit: Suit

    def __str__(self) -> str:
        """String representation of the card, e.g., 'A of Hearts'."""
        return f"{self.rank.value} of {self.suit.value}"

    @property
    def id(self) -> str:
        """A short identifier, e.g., 'AH', '7S'."""
        return f"{self.rank.value}{self.suit.value[0]}"

class Deck:
    """
    Represents a standard 32-card deck for La Contrée.
    """
    def __init__(self) -> None:
        """Initialize the deck with all 32 cards."""
        self.cards: list[Card] = [
            Card(rank, suit) for suit in Suit for rank in Rank
        ]

    def get_all_cards(self) -> list[Card]:
        """
        Get the list of all cards in the deck.
        
        Returns:
            list[Card]: The 32 cards.
        """
        return self.cards
