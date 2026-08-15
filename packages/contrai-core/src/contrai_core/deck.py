"""Deck class for managing a deck of cards in the contrée game."""

import random

from .card import Card
from .exceptions import InvalidCardCountError, InvalidPlayerCountError
from .types import Rank, Suit

class Deck:
    def __init__(self):
        self.cards = [Card(suit, rank) for suit in Suit for rank in Rank]

    def __repr__(self):
        """
        Returns a string representation of the Deck object for debugging.
        """
        return f"Deck({len(self.cards)} cards)"

    def __str__(self):
        """
        Returns a human-readable string representation of the Deck.
        """
        if self.is_empty():
            return "Empty deck"
        return f"Deck with {len(self.cards)} cards"

    def shuffle(self):
        """
        Shuffles the deck of cards in place.
        """
        random.shuffle(self.cards)

    def cut(self):
        """
        Cuts the deck at a random position (excluding the first and last 3 cards).
        Modifies the order of the cards in the deck.
        """
        cut_index = random.randint(3, len(self.cards) - 4)
        self.cards = self.cards[cut_index:] + self.cards[:cut_index]

    def deal(self, players: list):
        """Deal the whole deck out in the customary 3-2-3 batches.

        Each of the four players receives 3 cards, then 2, then 3 — eight
        in total, which empties the 32-card deck. The batches are handed
        over with ``hand.extend``, so every player must already expose a
        :class:`Hand` to deal into; the hand is appended to, never
        replaced, and is left holding whatever it had before.

        Args:
            players: The 4 players to deal to, in seating order.

        Raises:
            InvalidCardCountError: If the deck does not hold exactly 32
                cards — a deck already dealt from cannot be dealt again.
            InvalidPlayerCountError: If the number of players is not
                exactly 4.
        """
        if len(self.cards) != 32:
            raise InvalidCardCountError(32, len(self.cards), "Dealing cards")
        if len(players) != 4:
            raise InvalidPlayerCountError(4, len(players), "Dealing cards")
        # Deal 8 cards to each player (3-2-3 distribution)
        for i, player in enumerate(players):
            player.hand.extend(self.cards[i * 3:(i * 3) + 3])
            player.hand.extend(self.cards[(i * 2) + 12:(i * 2) + 14])
            player.hand.extend(self.cards[(i * 3) + 20:(i * 3) + 23])

        self.cards = []

    def is_empty(self) -> bool:
        """
        Check if the deck is empty (contains no cards).

        Returns:
            bool: True if the deck has no cards, False otherwise.
        """
        return len(self.cards) == 0

    def add_cards(self, cards):
        """
        Add cards to the bottom of the deck.

        Args:
            cards (list[Card]): List of cards to add to the deck
        """
        self.cards.extend(cards)
