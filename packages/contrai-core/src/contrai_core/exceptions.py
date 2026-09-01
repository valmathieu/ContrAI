"""Custom exceptions for the contrée domain.

Every domain-rule violation raised by ``contrai-core`` (and the engine
layered on top of it) subclasses :class:`ContraiError`, so a single
``except ContraiError`` catches the whole family. Each concrete error
*also* subclasses :class:`ValueError` — every one of them reports a
value the domain rejects — so a plain ``except ValueError`` handler or
``pytest.raises(ValueError)`` check catches them just as well. The dual
base is an invariant, not a convenience: it is asserted for every
concrete error in ``test_exceptions.py``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:                      # annotations only → no runtime/circular import
    from collections.abc import Iterable

    from .bid import Bid
    from .card import Card


class ContraiError(Exception):
    """Base class for every ContrAI domain error.

    Concrete domain errors inherit from ``(ContraiError, ValueError)``.
    ``ContraiError`` itself deliberately defines **no** ``__init__`` so a
    subclass's ``super().__init__(message)`` resolves through the MRO to
    :meth:`ValueError.__init__`, storing the message the way callers
    expect. The dual inheritance is what lets one ``except
    ContraiError`` catch the whole family while a broader ``except
    ValueError`` still catches each member individually.
    """


class InvalidPlayerCountError(ContraiError, ValueError):
    """
    Raised when an operation requires a specific number of players but receives a different count.

    This exception is typically raised in contexts where the game rules require exactly 4 players
    (for dealing cards, forming teams, etc.) or exactly 2 players (for forming a team).
    """

    def __init__(
        self, expected_count: int, actual_count: int, context: str = ""
    ) -> None:
        """
        Initialize the InvalidPlayerCountError.

        Args:
            expected_count: The expected number of players.
            actual_count: The actual number of players received.
            context: Additional context about where the error occurred.
        """
        if context:
            message = f"{context}: Expected {expected_count} players, got {actual_count}"
        else:
            message = f"Expected {expected_count} players, got {actual_count}"

        super().__init__(message)
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.context = context

class InvalidCardCountError(ContraiError, ValueError):
    """
    Raised when an operation requires a specific number of cards but receives a different count.

    This exception is typically raised in contexts where the cards have to be dealt.
    """

    def __init__(
        self, expected_count: int, actual_count: int, context: str = ""
    ) -> None:
        """
        Initialize the InvalidCardCountError.

        Args:
            expected_count: The expected number of cards.
            actual_count: The actual number of cards received.
            context: Additional context about where the error occurred.
        """
        if context:
            message = f"{context}: Expected {expected_count} cards, got {actual_count}"
        else:
            message = f"Expected {expected_count} cards, got {actual_count}"

        super().__init__(message)
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.context = context


class InvalidCardError(ContraiError, ValueError):
    """Raised when a :class:`Card` is built from something that is not a card.

    A physical card carries one of the four :class:`~contrai_core.Suit`
    members. A contract's trump may also be a
    :class:`~contrai_core.TrumpVariant` — those name no suit, so they can
    reach a card constructor only by mistake, and the mistake is worth
    catching where it is made rather than several tricks later.
    """

    def __init__(self, message: str, context: str = "") -> None:
        """Initialize the InvalidCardError.

        Args:
            message: Human-readable description of the invalid card data.
            context: Optional free-form context appended to the message.
        """
        super().__init__(f"{context}: {message}" if context else message)
        self.context = context


class IllegalBidError(ContraiError, ValueError):
    """Raised when a bid is applied to an :class:`Auction` in which it is illegal.

    The auction state machine surfaces illegal bids as a loud failure
    rather than silently downgrading them to a Pass. The offending bid
    and the prior bid history are attached so callers can render
    diagnostics — engine wiring bugs around bidding should be obvious
    rather than swallowed.
    """

    def __init__(
        self, bid: Bid, bids: Iterable[Bid], context: str = ""
    ) -> None:
        """Initialize the IllegalBidError.

        Args:
            bid: The bid that was rejected.
            bids: The chronological iterable of prior bids the bid was
                applied against. Coerced to a tuple for diagnostics.
            context: Optional free-form context (e.g. originating call
                site or player position) appended to the message.
        """
        bids_tuple = tuple(bids)
        base = (
            f"Illegal bid {bid!r} for auction with "
            f"{len(bids_tuple)} prior bid(s)"
        )
        message = f"{context}: {base}" if context else base
        super().__init__(message)
        self.bid = bid
        self.bids = bids_tuple
        self.context = context


class PlayRuleViolation(StrEnum):
    """Why a card play is illegal — one member per obligation branch.

    Each value maps to one of the obligation branches the engine's
    card-play legality oracle enforces. ``StrEnum`` keeps the members
    string-comparable for clean logging / JSON serialization by future
    RL / scraper / server consumers.
    """

    MUST_FOLLOW_SUIT = "must_follow_suit"
    """Held a card of the led suit but played off-suit. Covers the
    trump-led "must follow trump" case, since the led suit *is* trump
    there."""

    MUST_TRUMP = "must_trump"
    """Void in the led suit, not protected by a partner-master, held
    trump but discarded a non-trump instead."""

    MUST_OVERTRUMP = "must_overtrump"
    """Held a higher trump than required (trump led, or over an
    opponent's ruff) but played a lower trump."""

    OUT_OF_TURN = "out_of_turn"
    """Attempted to play when it was not this player's turn — including
    any play attempted once the play phase is over."""

    CARD_NOT_IN_HAND = "card_not_in_hand"
    """Attempted to play a card the player does not hold — either never
    dealt to them or already played earlier in the round."""


class IllegalPlayError(ContraiError, ValueError):
    """Raised when a card play violates a follow / trump obligation.

    Mirrors :class:`IllegalBidError`: an illegal card is surfaced as a
    loud failure rather than silently corrected to a legal one. The
    offending card, the machine-readable :class:`PlayRuleViolation`
    reason, and the set of legal alternatives are attached so callers
    can render diagnostics and explainable rationales.
    """

    def __init__(
        self,
        card: Card,
        reason: PlayRuleViolation,
        legal_cards: Iterable[Card],
        context: str = "",
    ) -> None:
        """Initialize the IllegalPlayError.

        Args:
            card: The card whose play was rejected.
            reason: The :class:`PlayRuleViolation` classifying *why* the
                play was illegal.
            legal_cards: The cards that would have been legal. Coerced to
                a tuple for diagnostics.
            context: Optional free-form context (e.g. player position or
                call site) appended to the message.
        """
        legal = tuple(legal_cards)
        base = (
            f"Illegal play {card!r}: {reason.value} "
            f"({len(legal)} legal alternative(s))"
        )
        super().__init__(f"{context}: {base}" if context else base)
        self.card = card
        self.reason = reason
        self.legal_cards = legal
        self.context = context


class TrickStateError(ContraiError, ValueError):
    """Raised on an illegal mutation of a :class:`Trick`'s state.

    Currently surfaces the single case of adding a card to an
    already-complete (four-card) trick — an engine sequencing bug rather
    than a player choice.
    """

    def __init__(self, message: str, context: str = "") -> None:
        """Initialize the TrickStateError.

        Args:
            message: Human-readable description of the illegal mutation.
            context: Optional free-form context appended to the message.
        """
        super().__init__(f"{context}: {message}" if context else message)
        self.context = context


class InvalidContractError(ContraiError, ValueError):
    """Raised when contract / contract-bid data is internally inconsistent.

    Unifies the two construction-time checks: an unknown contract value
    or trump suit on a :class:`ContractBid`, and a redouble recorded
    without an underlying double on a :class:`Contract`.
    """

    def __init__(self, message: str, context: str = "") -> None:
        """Initialize the InvalidContractError.

        Args:
            message: Human-readable description of the inconsistency.
            context: Optional free-form context appended to the message.
        """
        super().__init__(f"{context}: {message}" if context else message)
        self.context = context


class InvalidRuleConfigError(ContraiError, ValueError):
    """Raised when a :class:`~contrai_core.RuleConfig` is self-contradictory.

    Surfaces the construction-time checks of the table-rule catalogue: a
    table marking neither made nor announced points keeps no score at all,
    and a target score off the 500–5000 ladder has no meaning.
    """

    def __init__(self, message: str, context: str = "") -> None:
        """Initialize the InvalidRuleConfigError.

        Args:
            message: Human-readable description of the contradiction.
            context: Optional free-form context appended to the message.
        """
        super().__init__(f"{context}: {message}" if context else message)
        self.context = context
