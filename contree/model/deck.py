# Deck class

import random
from .card import Card

class Deck:
    """
    Represents a deck of 32 cards used in La Contrée.

    Attributes:
        cards (list[Card]): List of all cards in the deck.
    """

    def __init__(self):
        """
        Initialize a deck with all 32 cards of La Contrée.
        Creates cards for all combinations of suits and ranks.
        """
        self.cards = [Card(suit, rank) for suit in Card.SUITS for rank in Card.RANKS]

    def shuffle(self):
        """
        Randomly shuffle all cards in the deck.
        """
        random.shuffle(self.cards)

    def cut(self):
        """
        Cuts the deck at a random position (excluding the first and last 3 cards).
        Modifies the order of the cards in the deck.
        This follows La Contrée tradition where the deck is cut before dealing
        in subsequent rounds.
        """
        if len(self.cards) < 8:  # Need at least 8 cards to cut properly
            return

        cut_index = random.randint(3, len(self.cards) - 4)
        self.cards = self.cards[cut_index:] + self.cards[:cut_index]

    def deal(self, players: list):
        """
        Deals 8 cards to each player in a 3-2-3 distribution.
        Each player receives 3 cards, then 2 cards, then 3 cards, for a total of 8 cards per player.
        The function expects exactly 4 players, each with a 'hand' attribute (list).

        After dealing, the deck is cleared (cards are removed from deck and placed in players' hands).

        Raises:
            ValueError: If the number of players is not exactly 4.

        Args:
            players (list): List of 4 player objects to deal cards to.
        """
        # Validate input
        if len(players) != 4:
            raise ValueError("The number of players has to be exactly 4.")

        if len(self.cards) < 32:
            raise ValueError("Not enough cards in deck to deal. Deck should have 32 cards.")

        # Deal in 3-2-3 pattern
        # First round: 3 cards to each player
        for i, player in enumerate(players):
            player.hand.extend(self.cards[i * 3:(i * 3) + 3])

        # Second round: 2 cards to each player
        for i, player in enumerate(players):
            player.hand.extend(self.cards[(i * 2) + 12:(i * 2) + 14])

        # Third round: 3 cards to each player
        for i, player in enumerate(players):
            player.hand.extend(self.cards[(i * 3) + 20:(i * 3) + 23])

        # Clear the deck after dealing (cards are now in players' hands)
        self.cards.clear()

    def reset(self):
        """
        Reset the deck to contain all 32 cards.
        Useful for starting a new round.
        """
        self.cards = [Card(suit, rank) for suit in Card.SUITS for rank in Card.RANKS]

    def is_empty(self):
        """
        Check if the deck is empty.

        Returns:
            bool: True if the deck has no cards, False otherwise
        """
        return len(self.cards) == 0

    def cards_remaining(self):
        """
        Get the number of cards remaining in the deck.

        Returns:
            int: Number of cards left in the deck
        """
        return len(self.cards)

    def __len__(self):
        """Return the number of cards in the deck."""
        return len(self.cards)

    def __str__(self):
        """String representation of the deck."""
        return f"Deck with {len(self.cards)} cards"

    def __repr__(self):
        """Developer representation of the deck."""
        return f"Deck({len(self.cards)} cards)"
