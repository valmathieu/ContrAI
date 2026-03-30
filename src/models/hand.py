"""
Hand representation and validation for a player's hand.
"""

from src.models.deck import Card, SuitSlot, Rank


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

    def has_card(self, rank: Rank, suit: SuitSlot) -> bool:
        """
        Check if the hand contains a specific card.

        Args:
            rank (Rank): The rank to check.
            suit (SuitSlot): The suit slot to check.

        Returns:
            bool: True if the card is in the hand, False otherwise.
        """
        return any(c.rank == rank and c.suit == suit for c in self.cards)

    def count_suit(self, suit: SuitSlot) -> int:
        """
        Count the number of cards in a specific suit slot.

        Args:
            suit (SuitSlot): The slot to count.

        Returns:
            int: Number of cards in that slot.
        """
        return sum(1 for c in self.cards if c.suit == suit)

    def count_rank(self, rank: Rank) -> int:
        """
        Count cards of a specific rank across all suit slots.

        Args:
            rank (Rank): The rank to count.

        Returns:
            int: Number of cards of that rank (e.g. number of Aces).
        """
        return sum(1 for c in self.cards if c.rank == rank)

    def get_suit_cards(self, suit: SuitSlot) -> list[Card]:
        """
        Get all cards belonging to a specific suit slot.

        Args:
            suit (SuitSlot): The slot to filter by.

        Returns:
            list[Card]: Cards in that slot.
        """
        return [c for c in self.cards if c.suit == suit]

    def my_points(self) -> int:
        """
        Calculate the total Contrée point value of this hand.

        Uses the trump scale for cards in SuitSlot.TRUMP and the non-trump
        scale for all other slots.

        Returns:
            int: Total points (range: 0–162 for a full hand).
        """
        return sum(c.point_value for c in self.cards)
