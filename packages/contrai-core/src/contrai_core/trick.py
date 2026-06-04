# Trick class for the contrée card game.
# This class represents a single trick in the game.

from __future__ import annotations
from typing import List, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .card import Card
    from .player import BasePlayer as Player
    from .types import Suit

class Trick:
    """
    Represents a single trick in the card game.

    A trick contains up to 4 cards played by players in order,
    with methods to determine the winner based on trump rules.
    """

    def __init__(self) -> None:
        """Initialize a new, empty trick.

        A trick is a dumb container of plays; it does not own the trump
        suit. Trump is round-level state living on the ``Contract`` and is
        passed to :meth:`get_current_winner` at call time — mirroring how
        :meth:`contrai_core.Card.get_order` / ``get_points`` take trump as
        a parameter rather than storing it.
        """
        self.plays: List[Tuple[Player, Card]] = []

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

    def get_current_winner(self, trump_suit: Optional[Suit]) -> Optional[Player]:
        """
        Return the player currently winning this (possibly partial) trick.

        Works on incomplete tricks — useful while a trick is being played
        for legality checks (e.g. *partner is currently master*) and view
        rendering (live winner highlight).

        Args:
            trump_suit: The trump suit to evaluate against, taken from the
                round's contract. Pass ``None`` (or ``Suit.NO_TRUMP``) when
                no suit is trump — every trump-related branch then reduces
                to the follow-suit rule. The argument is required: there is
                no construction-time trump to fall back to, so callers must
                state trump explicitly rather than risk a silent no-trump
                evaluation.

        Returns:
            Player who is currently winning, or None if no card has been
            played yet.
        """
        if not self.plays:
            return None

        lead_suit = self.plays[0][1].suit
        best_player = self.plays[0][0]
        best_card = self.plays[0][1]
        best_is_trump = trump_suit is not None and best_card.suit == trump_suit

        for player, card in self.plays[1:]:
            card_is_trump = trump_suit is not None and card.suit == trump_suit

            if card_is_trump and not best_is_trump:
                # Trump beats non-trump
                best_player = player
                best_card = card
                best_is_trump = True
            elif card_is_trump and best_is_trump:
                # Compare trump cards (Jack > 9 > Ace > 10 > King > Queen > 8 > 7)
                if card.get_order(trump_suit) > best_card.get_order(trump_suit):
                    best_player = player
                    best_card = card
            elif not card_is_trump and not best_is_trump and card.suit == lead_suit:
                # Compare cards of the same suit (non-trump)
                if card.get_order() > best_card.get_order():
                    best_player = player
                    best_card = card

        return best_player
