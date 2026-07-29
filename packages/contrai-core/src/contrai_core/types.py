"""Enum types for card suits and ranks.

Shared across all ContrAI packages. Enum values are the human-readable display
strings (``Rank.JACK.value == "Jack"``, ``Suit.SPADES.value == "Spades"``) so
``str(card)`` renders as e.g. ``"Jack ♠"`` directly from ``rank.value`` plus the
suit glyph — no separate display map needed.
"""

from enum import Enum


class Suit(Enum):
    """Card suits in contrée.

    ``NO_TRUMP`` is a contract trump option only — no physical card has it.
    Use :data:`CARD_SUITS` (or compare against ``Suit.NO_TRUMP``) when
    iterating only over real card suits.
    """

    SPADES = "Spades"
    HEARTS = "Hearts"
    DIAMONDS = "Diamonds"
    CLUBS = "Clubs"
    NO_TRUMP = "NoTrump"
    ALL_TRUMP = "AllTrump"

    def __str__(self) -> str:
        """Render as the plain display name, e.g. ``"Spades"``.

        Load-bearing for every f-string and panel label that embeds a suit
        directly: ``f"{bid.value} {bid.suit}"`` reads ``"100 Spades"``
        (``__format__`` delegates to ``__str__`` by default). Without this
        override, the default ``Enum.__str__`` would print
        ``"Suit.SPADES"``, which leaked into ``Contract.__str__`` and every
        message built from it.
        """

        return self.value


#: The four card-bearing suits (excludes ``Suit.NO_TRUMP``).
CARD_SUITS = (Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS)


def is_trump(card_suit: Suit, contract_suit: Suit | None) -> bool:
    """Whether ``card_suit`` is trump under ``contract_suit``.

    The single spelling of the question the whole trick-taking rulebook
    rests on. It used to be written out inline as ``card.suit ==
    trump_suit`` at every boundary, which is only *accidentally* right for
    the trump options that name no suit: it happens to answer correctly
    for ``NO_TRUMP`` (no physical card carries that suit, so every trump
    branch collapses to plain follow-suit — which is what a no-trump
    contract wants) and it silently answers *the same way* for
    ``ALL_TRUMP``, where every card should be trump instead. Funnelling
    the comparison through here turns that spread-out coincidence into one
    explicit decision.

    Args:
        card_suit: The suit of a physical card.
        contract_suit: The trump the round's contract settled on, or
            ``None`` when no contract is established yet.

    Returns:
        ``True`` if a card of ``card_suit`` plays as trump.

    Raises:
        NotImplementedError: If ``contract_suit`` is ``Suit.ALL_TRUMP``.
            All-trump is not implemented; raising here is what keeps it
            from quietly playing out as a no-trump round.
    """

    if contract_suit is None or contract_suit is Suit.NO_TRUMP:
        return False
    if contract_suit is Suit.ALL_TRUMP:
        raise NotImplementedError(
            "All-trump contracts are not implemented: every suit would be "
            "trump, which changes card ordering, point values and the "
            "follow obligations. Bid a suit or no-trump instead."
        )
    return card_suit is contract_suit


def trump_suits(contract_suit: Suit | None) -> tuple[Suit, ...]:
    """The card suits that are trump under ``contract_suit``.

    The enumerating sibling of :func:`is_trump`, for the callers that need
    to know *whether any suit at all* is trump (``if not
    trump_suits(...)``) rather than to test one card. Derived from
    :func:`is_trump` and not the other way round: ``is_trump`` is the hot
    path — several calls per legality check, per winner evaluation, and
    millions under any future search — so it stays two identity checks and
    a comparison with no tuple to allocate.

    Args:
        contract_suit: The trump the round's contract settled on, or
            ``None`` when no contract is established yet.

    Returns:
        The trump card suits: empty for ``None``/``NO_TRUMP``, a single
        suit for a suit contract.

    Raises:
        NotImplementedError: If ``contract_suit`` is ``Suit.ALL_TRUMP``,
            propagated from :func:`is_trump`.
    """

    return tuple(suit for suit in CARD_SUITS if is_trump(suit, contract_suit))


class Rank(Enum):
    """The eight card ranks in a contrée deck (32-card subset: 7..Ace)."""

    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "Jack"
    QUEEN = "Queen"
    KING = "King"
    ACE = "Ace"
