"""
Hand representation and validation for a player's hand.
"""

from src.models.deck import Card, Suit, Rank

class Hand:
    """
    Represents a player's hand consisting of exactly 8 cards.
    """
    def __init__(self, cards: list[Card]) -> None:
        """
        Initialize the hand.

        Args:
            cards (list[Card]): A list of exactly 8 unique cards.

        Raises:
            ValueError: If the hand does not contain exactly 8 unique cards.
        """
        if len(cards) != 8:
            raise ValueError(f"A hand must contain exactly 8 cards, got {len(cards)}.")
        if len(set(cards)) != 8:
            raise ValueError("A hand must not contain duplicate cards.")
        
        self.cards = cards

    def has_card(self, rank: Rank, suit: Suit) -> bool:
        """
        Check if the hand contains a specific card.
        
        Args:
            rank (Rank): The rank to check.
            suit (Suit): The suit to check.
            
        Returns:
            bool: True if the card is in the hand, False otherwise.
        """
        return any(c.rank == rank and c.suit == suit for c in self.cards)

    def count_suit(self, suit: Suit) -> int:
        """
        Count the number of cards of a specific suit in the hand.
        
        Args:
            suit (Suit): The suit to count.
            
        Returns:
            int: The number of cards of that suit.
        """
        return sum(1 for c in self.cards if c.suit == suit)
    
    def count_rank(self, rank: Rank) -> int:
        """
        Count the number of cards of a specific rank in the hand.
        
        Args:
            rank (Rank): The rank to count.
            
        Returns:
            int: The number of cards of that rank (e.g. number of Aces).
        """
        return sum(1 for c in self.cards if c.rank == rank)
    
    def get_suit_cards(self, suit: Suit) -> list[Card]:
        """
        Get all cards of a specific suit from the hand.
        
        Args:
            suit (Suit): The suit to filter by.
            
        Returns:
            list[Card]: The cards of that suit.
        """
        return [c for c in self.cards if c.suit == suit]
