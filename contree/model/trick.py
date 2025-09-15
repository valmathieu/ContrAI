# Trick class for the "contree" card game.
# This class represents a single trick in the game.

from typing import List, Tuple, Optional
from .card import Card
from .player import Player

class Trick:
    """
    Represents a single trick in the card game.

    A trick contains up to 4 cards played by players in order,
    with methods to determine the winner based on trump rules.
    """

    def __init__(self, trump_suit: Optional[str] = None):
        """
        Initialize a new trick.

        Args:
            trump_suit: The trump suit for this trick, if any
        """
        self.plays: List[Tuple[Player, Card]] = []
        self.trump_suit = trump_suit

    def add_play(self, player: Player, card: Card) -> None:
        """
        Add a card play to this trick.

        Args:
            player: The player playing the card
            card: The card being played

        Raises:
            ValueError: If trick is already complete (4 cards)
        """
        if self.is_complete():
            raise ValueError("Cannot add card to complete trick")

        self.plays.append((player, card))

    def get_cards(self) -> List[Card]:
        """Get all cards played in this trick."""
        return [card for _, card in self.plays]

    def get_led_suit(self) -> Optional[str]:
        """Get the suit of the first card played, or None if no cards played."""
        if not self.plays:
            return None
        return self.plays[0][1].suit

    def __len__(self) -> int:
        """
        Return the number of cards played in this trick.

        Returns:
            Number of cards played (0-4)
        """
        return len(self.plays)

    def get_plays(self) -> List[Tuple[Player, Card]]:
        """
        Get all plays (player, card) in this trick.

        Returns:
            List of (player, card) tuples
        """
        return self.plays.copy()

    def is_complete(self) -> bool:
        """
        Check if this trick is complete (4 cards played).

        Returns:
            True if 4 cards have been played, False otherwise
        """
        return len(self.plays) == 4

    def size(self) -> int:
        """
        Get number of cards played so far.

        Returns:
            Number of cards played (0-4)
        """
        return len(self.plays)

    def get_winner(self) -> Optional[Player]:
        """
        Determine the winner of this trick.

        Returns:
            Player who won the trick, or None if trick is empty
        """
        if not self.plays:
            return None

        lead_suit = self.get_led_suit()
        best_player = self.plays[0][0]
        best_card = self.plays[0][1]
        best_is_trump = self.trump_suit and best_card.suit == self.trump_suit

        for player, card in self.plays[1:]:
            card_is_trump = self.trump_suit and card.suit == self.trump_suit

            if card_is_trump and not best_is_trump:
                # Trump beats non-trump
                best_player = player
                best_card = card
                best_is_trump = True
            elif card_is_trump and best_is_trump:
                # Compare trump cards
                if card.get_order(self.trump_suit) > best_card.get_order(self.trump_suit):
                    best_player = player
                    best_card = card
            elif not card_is_trump and not best_is_trump and card.suit == lead_suit:
                # Compare cards of the same suit (non-trump)
                if card.get_order() > best_card.get_order():
                    best_player = player
                    best_card = card

        return best_player

    def is_empty(self) -> bool:
        """Check if the trick is empty."""
        return len(self.plays) == 0
