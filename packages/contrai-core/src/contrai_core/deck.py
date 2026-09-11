"""Deck class for managing a deck of cards in the contrée game."""

import random
from collections.abc import Sequence

from .card import Card
from .exceptions import InvalidCardCountError, InvalidPlayerCountError
from .types import Rank, Suit

class Deck:
    def __init__(self):
        self.cards = [Card(suit, rank) for suit in Suit for rank in Rank]

    @classmethod
    def stacked(cls, hands: Sequence[Sequence[Card]]) -> "Deck":
        """Build a deck whose :meth:`deal` reproduces exactly ``hands``.

        The inverse of :meth:`deal`'s fixed 3-2-3 layout. ``deal`` reads
        each seat's three batches from three *disjoint* slices of
        ``cards``: for the seat at index ``i`` they are
        ``cards[i*3:i*3+3]``, ``cards[i*2+12:i*2+14]`` and
        ``cards[i*3+20:i*3+23]``. Writing each hand back into those three
        slots is therefore the whole inversion — no shuffle to neutralise,
        no order to guess.

        The hands are in **deal order**, the same order :meth:`deal` takes
        its players in: the seat after the dealer first, the dealer last.
        A caller holding hands keyed by seat re-orders them itself, which
        is what keeps this method free of any seating rule.

        Replaying a recorded game is what this is for — the deal comes
        from the file rather than from the RNG — and a test wanting a
        chosen deal uses it for the same reason.

        Args:
            hands: The four 8-card hands, in deal order.

        Returns:
            A fresh deck stacked for that deal.

        Raises:
            InvalidPlayerCountError: If there are not exactly 4 hands.
            InvalidCardCountError: If a hand does not hold exactly 8
                cards, or if the four hands do not hold 32 *distinct*
                cards between them.
        """

        if len(hands) != 4:
            raise InvalidPlayerCountError(4, len(hands), "Stacking a deck")
        cards: list[Card] = [None] * 32  # type: ignore[list-item]
        for index, hand in enumerate(hands):
            if len(hand) != 8:
                raise InvalidCardCountError(
                    8, len(hand), f"Stacking a deck (hand {index})"
                )
            cards[index * 3 : index * 3 + 3] = hand[0:3]
            cards[index * 2 + 12 : index * 2 + 14] = hand[3:5]
            cards[index * 3 + 20 : index * 3 + 23] = hand[5:8]
        # A repeated card would leave ``deal`` handing the same card to
        # two seats, which every downstream invariant then trips over far
        # from the cause. Catch it here, where the cause is visible.
        distinct = len(set(cards))
        if distinct != 32:
            raise InvalidCardCountError(32, distinct, "Stacking a deck")

        deck = cls()
        deck.cards = cards
        return deck

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
