"""Enum types for card suits, contract trump options, and ranks.

Shared across all ContrAI packages. Enum values are the human-readable display
strings (``Rank.JACK.value == "Jack"``, ``Suit.SPADES.value == "Spades"``) so
``str(card)`` renders as e.g. ``"Jack ♠"`` directly from ``rank.value`` plus the
suit glyph — no separate display map needed.

Two different questions get two different types. "What suit is this card?" has
exactly four answers and is :class:`Suit`. "What did the auction settle on?" has
six — those four plus the options that name no suit — and is
:data:`ContractSuit`, the union of :class:`Suit` and :class:`TrumpVariant`.
Keeping them apart is what makes :func:`is_trump` the only place that has to
know how a suitless trump option behaves; a single wide enum let
``card.suit == trump_suit`` type-check everywhere while being merely
*accidentally* right.

:data:`ContractSuit` is a plain ``Suit | TrumpVariant`` assignment, not a
PEP 695 ``type ContractSuit = …`` alias, on purpose: ``isinstance(x,
ContractSuit)`` works on the plain form and raises ``TypeError: isinstance()
arg 2 must be a type…`` on the alias form. This workspace configures no type
checker, so ``isinstance`` narrowing is the only guardrail there is, and a
spelling that breaks it is disqualifying. Both forms read identically to
mypy/pyright should one be added later.
"""

from enum import Enum


class Suit(Enum):
    """The four card-bearing suits. A physical card always has one of these.

    Deliberately narrower than what a contract can name: ``NO_TRUMP`` and
    ``ALL_TRUMP`` live on :class:`TrumpVariant` instead, because no card
    carries them. ``tuple(Suit)`` is therefore safe to iterate anywhere a
    real card suit is meant.

    Definition order is the documented suit preference — spades, hearts,
    diamonds, clubs — which display sorting and the AI's suit choice both
    read.
    """

    SPADES = "Spades"
    HEARTS = "Hearts"
    DIAMONDS = "Diamonds"
    CLUBS = "Clubs"

    def __str__(self) -> str:
        """Render as the plain display name, e.g. ``"Spades"``.

        Load-bearing for every f-string and panel label that embeds a suit
        directly: ``f"{bid.value} {bid.suit}"`` reads ``"100 Spades"``
        (``__format__`` delegates to ``__str__`` by default). The default
        ``Enum.__str__`` renders ``"Suit.SPADES"`` instead, which is a
        repr, not a display string.
        """

        return self.value


class TrumpVariant(Enum):
    """Contract trump options that name no suit.

    A separate enum rather than two more :class:`Suit` members: these are
    answers to "what is trump this round", never to "what suit is this
    card", and the type is what keeps them out of ``Card.suit``, out of the
    deck, and out of the void/fallen maps that are keyed by card suit.

    Only ``NO_TRUMP`` is bookable — see
    :attr:`contrai_core.ContractBid.VALID_SUITS`. ``ALL_TRUMP`` is declared
    because the contract vocabulary has it, but every path that would have
    to interpret it raises rather than guess (:func:`is_trump`).
    """

    NO_TRUMP = "NoTrump"
    ALL_TRUMP = "AllTrump"

    def __str__(self) -> str:
        """Render as the plain display name, e.g. ``"NoTrump"``.

        Same reason as :meth:`Suit.__str__`: a contract embeds its trump
        directly in text, and these members flow through the very same
        f-strings as the four card suits.
        """

        return self.value


#: Everything a contract's trump can be — the four card suits plus the two
#: suitless options. Plain union assignment, not a PEP 695 ``type`` alias, so
#: that ``isinstance(x, ContractSuit)`` keeps working (see module docstring).
ContractSuit = Suit | TrumpVariant

#: Every trump a contract could name, card suits first. Not every member is
#: bookable — :attr:`contrai_core.ContractBid.VALID_SUITS` is the subset the
#: auction accepts.
CONTRACT_SUITS: tuple[ContractSuit, ...] = (*Suit, *TrumpVariant)


def is_trump(card_suit: Suit, contract_suit: ContractSuit | None) -> bool:
    """Whether ``card_suit`` is trump under ``contract_suit``.

    The single spelling of the question the whole trick-taking rulebook
    rests on, and the one place the two suit types meet. A bare
    ``card_suit == contract_suit`` written out at each boundary is only
    *accidentally* right for the trump options that name no suit: it
    answers correctly for ``NO_TRUMP`` (no physical card carries that
    suit, so every trump branch collapses to plain follow-suit — which is
    what a no-trump contract wants) and it answers *the same way* for
    ``ALL_TRUMP``, where every card should be trump instead. Routing
    every caller through here makes that distinction one explicit
    decision rather than a coincidence repeated at each site.

    Args:
        card_suit: The suit of a physical card.
        contract_suit: The trump the round's contract settled on, or
            ``None`` when no contract is established yet.

    Returns:
        ``True`` if a card of ``card_suit`` plays as trump.

    Raises:
        NotImplementedError: If ``contract_suit`` is
            ``TrumpVariant.ALL_TRUMP``. All-trump is not implemented;
            raising here is what keeps it from quietly playing out as a
            no-trump round. The auction does not offer it
            (:attr:`contrai_core.ContractBid.VALID_SUITS`), so reaching
            this means a caller built the contract by hand.
    """

    if contract_suit is None or contract_suit is TrumpVariant.NO_TRUMP:
        return False
    if contract_suit is TrumpVariant.ALL_TRUMP:
        raise NotImplementedError(
            "All-trump contracts are not implemented: every suit would be "
            "trump, which changes card ordering, point values and the "
            "follow obligations. Bid a suit or no-trump instead."
        )
    return card_suit is contract_suit


def trump_suits(contract_suit: ContractSuit | None) -> tuple[Suit, ...]:
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
        suit for a suit contract. Always real card suits, which is what
        makes it safe to key the void and fallen maps by the result.

    Raises:
        NotImplementedError: If ``contract_suit`` is
            ``TrumpVariant.ALL_TRUMP``, propagated from :func:`is_trump`.
    """

    return tuple(suit for suit in Suit if is_trump(suit, contract_suit))


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
