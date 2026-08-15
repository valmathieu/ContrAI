"""TrumpRules: the per-contract trick-rules seam.

Every trick-taking decision that depends on what the auction settled on —
which cards are trump, what each card scores, how cards rank inside a suit,
who takes a trick, where a belote can live — is answered by a
:class:`TrumpRules` object resolved from the contract's trump via
:func:`rules_for`. Callers hold one rules object per decision site instead of
re-deriving trumpness card by card, which is what makes each future contract
regime (all-trump) a new leaf class plus its tables rather than a sweep over
every call site.

Design invariants:

- **No global "order" scale.** The trump and plain orderings are two
  overlapping 0–7 scales, so a single ``order(card)`` number would compare
  cards across scales and be silently wrong. Same-suit comparisons go through
  :meth:`TrumpRules.rank_in_suit`; cross-suit trick competition goes through
  :meth:`TrumpRules.trick_rank`, which is led-suit-aware and totally ordered
  by construction.
- **Obligations stay out.** Follow/trump/over-trump obligation structure
  lives in :meth:`contrai_core.PlayState.legal_actions`; rules objects only
  supply the predicates and comparators it asks with.
- **Sealed hierarchy.** The set of regimes is closed by design —
  ``isinstance`` narrowing is this workspace's only guardrail, so
  :class:`TrumpRules` rejects subclasses defined outside this module.
- **Singletons.** Rules objects are stateless values; :func:`rules_for` hands
  out one shared instance per regime so identity checks are meaningful and
  the hot path allocates nothing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .types import ContractSuit, Rank, Suit, TrumpVariant

if TYPE_CHECKING:
    from .card import Card


# The five per-card tables: two scoring pairs plus a third scoring scale,
# and two orderings. Trump and plain disagree on both the scoring and the
# ordering of 9 and Jack — which is exactly why the two scales must never
# be compared number against number. No trump borrows the plain *ordering*
# but scores on its own scale: with no 20-point Jack and no 14-point 9 to
# carry the difference, the ace is rescaled from 11 to 19 so that a suit
# is worth 38 and the deck 152, the same total as every other contract
# mode (contree-domain.md §3.4, §3.5).
_PLAIN_POINTS = {
    Rank.SEVEN: 0,
    Rank.EIGHT: 0,
    Rank.NINE: 0,
    Rank.JACK: 2,
    Rank.QUEEN: 3,
    Rank.KING: 4,
    Rank.TEN: 10,
    Rank.ACE: 11,
}
_TRUMP_POINTS = {
    Rank.SEVEN: 0,
    Rank.EIGHT: 0,
    Rank.NINE: 14,
    Rank.JACK: 20,
    Rank.QUEEN: 3,
    Rank.KING: 4,
    Rank.TEN: 10,
    Rank.ACE: 11,
}
_NO_TRUMP_POINTS = {
    Rank.SEVEN: 0,
    Rank.EIGHT: 0,
    Rank.NINE: 0,
    Rank.JACK: 2,
    Rank.QUEEN: 3,
    Rank.KING: 4,
    Rank.TEN: 10,
    Rank.ACE: 19,
}
_PLAIN_ORDER = {
    Rank.SEVEN: 0,
    Rank.EIGHT: 1,
    Rank.NINE: 2,
    Rank.JACK: 3,
    Rank.QUEEN: 4,
    Rank.KING: 5,
    Rank.TEN: 6,
    Rank.ACE: 7,
}
_TRUMP_ORDER = {
    Rank.SEVEN: 0,
    Rank.EIGHT: 1,
    Rank.QUEEN: 2,
    Rank.KING: 3,
    Rank.TEN: 4,
    Rank.ACE: 5,
    Rank.NINE: 6,
    Rank.JACK: 7,
}

# The same orderings as rank ladders, weakest first — what
# :meth:`TrumpRules.higher_ranks` slices. Derived from the order tables so
# the two spellings of each scale cannot drift apart.
_PLAIN_LADDER: tuple[Rank, ...] = tuple(
    sorted(_PLAIN_ORDER, key=_PLAIN_ORDER.__getitem__)
)
_TRUMP_LADDER: tuple[Rank, ...] = tuple(
    sorted(_TRUMP_ORDER, key=_TRUMP_ORDER.__getitem__)
)


class TrumpRules(ABC):
    """Per-contract trick rules: trumpness, points, ranking, belote.

    One instance answers every regime-dependent card question for one
    contract trump. Resolve the right instance with :func:`rules_for`;
    never construct or subclass outside this module — the hierarchy is
    sealed so that ``isinstance`` checks against the known leaves stay
    exhaustive.
    """

    def __init_subclass__(cls, **kwargs) -> None:
        """Reject subclasses defined outside this module.

        The rules hierarchy is a closed enumeration of contract regimes,
        not an extension point: exhaustive ``isinstance`` narrowing over
        the leaves is the workspace's only type guardrail, and an
        out-of-module subclass would silently break it.

        Raises:
            TypeError: If the subclass is not defined in this module.
        """

        super().__init_subclass__(**kwargs)
        if cls.__module__ != TrumpRules.__module__:
            raise TypeError(
                f"TrumpRules is a sealed hierarchy; cannot subclass it from "
                f"module {cls.__module__!r}. Contract regimes are defined "
                f"only in contrai_core.rules."
            )

    @abstractmethod
    def is_trump(self, suit: Suit) -> bool:
        """Whether a card of ``suit`` plays as trump under this regime.

        Args:
            suit: The suit of a physical card.

        Returns:
            ``True`` if cards of ``suit`` are trump.
        """

    @abstractmethod
    def points(self, card: Card) -> int:
        """The point value of ``card`` under this regime.

        Args:
            card: The card to score.

        Returns:
            The card's point value on this regime's own scale. A suit
            contract scores its trump suit from the trump table and the
            other three from the plain one; no trump scores every suit
            from a third table of its own. The three disagree, but each
            regime's 32 cards come to the same 152 points
            (contree-domain.md §3.5).
        """

    @abstractmethod
    def rank_in_suit(self, card: Card) -> int:
        """The strength of ``card`` **within its own suit**.

        Only comparable between cards of the same suit: the trump and
        plain scales overlap, so comparing these numbers across suits is
        meaningless. For cross-suit trick competition use
        :meth:`trick_rank`.

        Args:
            card: The card to rank.

        Returns:
            The card's order index on its suit's scale — higher beats
            lower among cards of the same suit.
        """

    @abstractmethod
    def trick_rank(self, card: Card, led_suit: Suit) -> tuple[int, int] | None:
        """The trick-competition rank of ``card`` in a ``led_suit`` trick.

        The one totally ordered comparison in the game: given the suit
        that was led, every card either competes for the trick with a
        well-defined strength or cannot take it at all. The maximum
        ``trick_rank`` over a trick's cards (with ``None`` filtered out)
        is the winner — lexicographic tuple comparison encodes "any trump
        beats any led-suit card, otherwise higher rank wins".

        Args:
            card: The card to rank.
            led_suit: The suit of the trick's first card.

        Returns:
            ``(1, trump_rank)`` for a trump card, ``(0, plain_rank)`` for
            a led-suit card that is not trump, ``None`` for a card that
            cannot take the trick.
        """

    @property
    @abstractmethod
    def belote_suits(self) -> tuple[Suit, ...]:
        """The suits in which holding King + Queen scores the Belote bonus.

        Returns:
            The trump suit as a one-tuple under a suit contract; empty
            when no suit is trump.
        """

    @abstractmethod
    def higher_ranks(self, rank: Rank, suit: Suit) -> tuple[Rank, ...]:
        """The ranks that beat ``rank`` within ``suit``, weakest first.

        The ladder complement of :meth:`rank_in_suit`: card-tracking code
        asks it to know which cards must still fall before a held card is
        master in its suit.

        Args:
            rank: The rank to rank above.
            suit: The suit the comparison lives in — it selects the trump
                or plain ladder.

        Returns:
            The ranks strictly above ``rank`` on the applicable ladder,
            weakest first; empty if ``rank`` is already the highest.
        """


@dataclass(frozen=True, slots=True)
class SingleSuitRules(TrumpRules):
    """The classic regime: exactly one suit is trump.

    Attributes:
        suit: The trump suit.
    """

    suit: Suit

    def is_trump(self, suit: Suit) -> bool:
        return suit is self.suit

    def points(self, card: Card) -> int:
        if card.suit is self.suit:
            return _TRUMP_POINTS[card.rank]
        return _PLAIN_POINTS[card.rank]

    def rank_in_suit(self, card: Card) -> int:
        if card.suit is self.suit:
            return _TRUMP_ORDER[card.rank]
        return _PLAIN_ORDER[card.rank]

    def trick_rank(self, card: Card, led_suit: Suit) -> tuple[int, int] | None:
        if card.suit is self.suit:
            return (1, _TRUMP_ORDER[card.rank])
        if card.suit is led_suit:
            return (0, _PLAIN_ORDER[card.rank])
        return None

    @property
    def belote_suits(self) -> tuple[Suit, ...]:
        return (self.suit,)

    def higher_ranks(self, rank: Rank, suit: Suit) -> tuple[Rank, ...]:
        ladder = _TRUMP_LADDER if suit is self.suit else _PLAIN_LADDER
        return ladder[ladder.index(rank) + 1:]


@dataclass(frozen=True, slots=True)
class NoTrumpRules(TrumpRules):
    """The regime with no trump suit: plain follow-suit everywhere.

    Ranking is the plain ladder — no card outranks its suit's ace — but
    scoring is **not** the plain table: the ace is worth 19, so a suit
    holds 38 points and the deck 152, matching a suit contract's total
    (contree-domain.md §3.4). Borrowing the plain table here would leave
    the deck at 120 and make the upper contract values unreachable.

    Also the regime of a round with no established contract yet (the
    ``None`` key of :func:`rules_for`) — with no trump named, every
    trump-dependent rule collapses to the same answers. The shared
    scoring scale is the no-trump one, which is inert: no card points
    are counted before a contract exists.
    """

    def is_trump(self, suit: Suit) -> bool:
        return False

    def points(self, card: Card) -> int:
        return _NO_TRUMP_POINTS[card.rank]

    def rank_in_suit(self, card: Card) -> int:
        return _PLAIN_ORDER[card.rank]

    def trick_rank(self, card: Card, led_suit: Suit) -> tuple[int, int] | None:
        if card.suit is led_suit:
            return (0, _PLAIN_ORDER[card.rank])
        return None

    @property
    def belote_suits(self) -> tuple[Suit, ...]:
        return ()

    def higher_ranks(self, rank: Rank, suit: Suit) -> tuple[Rank, ...]:
        return _PLAIN_LADDER[_PLAIN_LADDER.index(rank) + 1:]


# One shared instance per regime — rules objects are stateless values, so a
# single instance each serves every round and identity comparison works.
_SINGLE_SUIT_RULES: dict[Suit, SingleSuitRules] = {
    suit: SingleSuitRules(suit) for suit in Suit
}
_NO_TRUMP_RULES = NoTrumpRules()


def rules_for(contract_suit: ContractSuit | None) -> TrumpRules:
    """Resolve the :class:`TrumpRules` for a contract's trump.

    The single entry point to the rules hierarchy. ``None`` (no contract
    established yet) resolves to the same no-trump singleton as an explicit
    ``NO_TRUMP`` contract: with no trump named, the two situations play by
    identical card rules, and they share the no-trump *scoring* scale too —
    harmless, because no card points are counted before a contract exists.

    Args:
        contract_suit: The trump the round's contract settled on, or
            ``None`` when no contract is established yet.

    Returns:
        The shared rules instance for that regime.

    Raises:
        NotImplementedError: If ``contract_suit`` is
            ``TrumpVariant.ALL_TRUMP``. All-trump is not implemented;
            raising here is what keeps it from quietly playing out as a
            no-trump round. The auction does not offer it
            (:attr:`contrai_core.ContractBid.VALID_SUITS`), so reaching
            this means a caller built the contract by hand.
        KeyError: If ``contract_suit`` is not a :data:`ContractSuit`
            member.
    """

    if contract_suit is None or contract_suit is TrumpVariant.NO_TRUMP:
        return _NO_TRUMP_RULES
    if contract_suit is TrumpVariant.ALL_TRUMP:
        raise NotImplementedError(
            "All-trump contracts are not implemented: every suit would be "
            "trump, which changes card ordering, point values and the "
            "follow obligations. Bid a suit or no-trump instead."
        )
    return _SINGLE_SUIT_RULES[contract_suit]
