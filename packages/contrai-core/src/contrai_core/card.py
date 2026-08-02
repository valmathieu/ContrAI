"""Card class: represents a playing card."""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import InvalidCardError
from .types import Suit, Rank


@dataclass(frozen=True, slots=True, repr=False)
class Card:
    """
    Represents a playing card for the game of contrée.

    A card is pure identity: a suit and a rank. Everything contextual —
    whether it plays as trump, what it scores, how strong it is —
    depends on the round's contract and is answered by the
    :class:`~contrai_core.TrumpRules` object resolved via
    :func:`contrai_core.rules_for`, never by the card itself.

    ``Card`` is an **immutable value object**: equality and hashing are by
    ``(suit, rank)``, so two distinct instances of the same physical card
    compare equal and hash alike, and cards can live in ``set``/``dict`` by
    value (mirroring the :class:`~contrai_core.bid.Bid` precedent). There is
    deliberately **no** ``__lt__`` — a card's *strength* is context-dependent
    (it depends on the trump suit) and is obtained from the contract's
    rules object, not by comparing cards directly.

    Attributes:
        suit (Suit): The suit of the card.
        rank (Rank): The rank of the card.
    """

    suit: Suit
    rank: Rank

    SUIT_SYMBOLS = {
        Suit.SPADES: "♠",
        Suit.HEARTS: "♥",
        Suit.DIAMONDS: "♦",
        Suit.CLUBS: "♣",
    }

    def __post_init__(self) -> None:
        """Reject a suit no physical card can carry, at construction time.

        Mirrors :meth:`contrai_core.ContractBid.__post_init__`: the type is
        checked where the object is built, not where it later misbehaves.
        The path this closes is real rather than hypothetical —
        ``Hand.has_card(suit, rank)`` is implemented as ``Card(suit, rank)
        in self``, and the round's belote detection feeds it suits taken
        from the contract's rules. A suitless contract trump reaching that
        call would otherwise mint a ``Card`` in a suit no deck contains
        and quietly find nothing.

        Raises:
            InvalidCardError: If ``suit`` is not a :class:`Suit` member —
                typically a :class:`TrumpVariant` that belongs on a
                contract rather than on a card.
        """

        if not isinstance(self.suit, Suit):
            raise InvalidCardError(
                f"Invalid card suit: {self.suit!r}. A card must carry one "
                f"of the four Suit members; NO_TRUMP / ALL_TRUMP are "
                f"contract trump options (TrumpVariant), not card suits."
            )

    def __str__(self) -> str:
        return f"{self.rank.value} {Card.SUIT_SYMBOLS[self.suit]}"

    def __repr__(self) -> str:
        return f"Card({self.suit!r}, {self.rank!r})"
