"""Trick containers for the contrée card game.

Two shapes of trick live here: the mutable :class:`Trick` the engine fills
play by play, and the immutable :class:`TrickRecord` value that types a
*completed* trick in the play-phase histories. The winner rule itself is
the module-level :func:`current_winner` / :func:`_best_play` pair, shared
by both.
"""

from __future__ import annotations
from typing import Iterable, List, Sequence, Tuple, Optional, TypeVar, TYPE_CHECKING

from .exceptions import TrickStateError
from .rules import rules_for

if TYPE_CHECKING:
    from .card import Card
    from .player import BasePlayer as Player
    from .types import ContractSuit, Suit

# The winner rule never inspects who played a card — it only ranks the
# cards and hands back the "who" slot of the best play. Keeping that slot
# generic lets one implementation serve both the omniscient records
# (``Play`` carrying a ``BasePlayer``) and the sealed observation records
# (``ObservedPlay`` carrying a ``Position``).
PlayerT = TypeVar("PlayerT")

# The record type a completed trick holds — a ``Play`` in state contexts,
# an ``ObservedPlay`` in observations. ``TrickRecord`` is generic over it
# the same way ``current_winner`` is generic over the "who" slot: the
# rules only read each record's ``card``, everything else rides along.
RecordT = TypeVar("RecordT")

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
        :func:`contrai_core.rules_for` resolves the trick rules from a
        contract's trump at each decision site rather than storing them.
        """
        self.plays: List[Tuple[Player, Card]] = []

    def add_play(self, player: Player, card: Card) -> None:
        """
        Add a card play to this trick.

        Args:
            player: The player playing the card
            card: The card being played

        Raises:
            TrickStateError: If trick is already complete (4 cards)
        """
        if self.is_complete():
            raise TrickStateError("Cannot add a card to a complete trick")

        self.plays.append((player, card))

    def get_cards(self) -> List[Card]:
        """Get all cards played in this trick."""
        return [card for _, card in self.plays]

    def get_led_suit(self) -> Optional[Suit]:
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

    def get_current_winner(
        self, trump_suit: Optional[ContractSuit]
    ) -> Optional[Player]:
        """
        Return the player currently winning this (possibly partial) trick.

        Works on incomplete tricks — useful while a trick is being played
        for legality checks (e.g. *partner is currently master*) and view
        rendering (live winner highlight).

        Args:
            trump_suit: The trump suit to evaluate against, taken from the
                round's contract. Pass ``None`` (or
                ``TrumpVariant.NO_TRUMP``) when
                no suit is trump — every trump-related branch then reduces
                to the follow-suit rule. The argument is required: there is
                no construction-time trump to fall back to, so callers must
                state trump explicitly rather than risk a silent no-trump
                evaluation.

        Returns:
            Player who is currently winning, or None if no card has been
            played yet.

        Raises:
            NotImplementedError: If ``trump_suit`` is
                ``TrumpVariant.ALL_TRUMP``, propagated from
                :func:`contrai_core.rules_for`.
        """
        return current_winner(self.plays, trump_suit)


def current_winner(
    plays: List[Tuple[PlayerT, Card]], trump_suit: Optional[ContractSuit]
) -> Optional[PlayerT]:
    """
    Determine who currently wins a (possibly partial) trick.

    Works on incomplete plays — useful while a trick is being played for
    legality checks (e.g. *partner is currently master*) and view
    rendering (live winner highlight).

    Generic over the "who" slot of each play: hand it ``(BasePlayer,
    Card)`` pairs (``Trick``, ``PlayState``) and it returns the winning
    player; hand it the sealed ``(Position, Card)`` observation records
    and it returns the winning seat. The cards alone decide the winner —
    the "who" value is only carried through.

    Args:
        plays: The ordered (who, card) pairs played so far, in play
            order. The first entry sets the led suit.
        trump_suit: The trump suit to evaluate against, taken from the
            round's contract. Pass ``None`` (or
            ``TrumpVariant.NO_TRUMP``) when no
            suit is trump — every trump-related branch then reduces to the
            follow-suit rule. The argument is required: there is no
            construction-time trump to fall back to, so callers must state
            trump explicitly rather than risk a silent no-trump evaluation.

    Returns:
        Whoever is currently winning — the "who" value of the best play,
        whatever type the caller put there — or None if no card has been
        played yet.

    Raises:
        NotImplementedError: If ``trump_suit`` is
            ``TrumpVariant.ALL_TRUMP``, propagated from
            :func:`contrai_core.rules_for`.
    """
    best = _best_play(plays, trump_suit)
    return None if best is None else best[0]


def _best_play(
    plays: Sequence[Tuple[PlayerT, Card]], trump_suit: Optional[ContractSuit]
) -> Optional[Tuple[PlayerT, Card]]:
    """Return the winning play of a (possibly partial) trick.

    The single implementation of the winner ladder: trump beats non-trump,
    higher trump beats lower trump, and among non-trumps only the led suit
    competes. :func:`current_winner` peels the "who" slot off the result;
    :meth:`TrickRecord.winner` hands the whole record back.

    Args:
        plays: The ordered (who, card) pairs played so far, in play order.
            The first entry sets the led suit.
        trump_suit: The trump suit to evaluate against; ``None`` /
            ``TrumpVariant.NO_TRUMP`` reduce every trump branch to the
            follow-suit rule.

    Returns:
        The winning play — whatever record type the caller put in — or
        ``None`` if no card has been played yet.

    Raises:
        NotImplementedError: If ``trump_suit`` is
            ``TrumpVariant.ALL_TRUMP``, propagated from
            :func:`contrai_core.rules_for`.
    """
    if not plays:
        return None

    rules = rules_for(trump_suit)
    lead_suit = plays[0][1].suit

    # ``trick_rank`` totally orders the cards that can take a
    # ``lead_suit`` trick — (1, trump scale) above (0, plain scale) —
    # and answers None for the cards that cannot. The led card itself
    # always competes, so ``best_rank`` starts non-None and the running
    # max is the winner.
    best = plays[0]
    best_rank = rules.trick_rank(best[1], lead_suit)
    for play in plays[1:]:
        rank = rules.trick_rank(play[1], lead_suit)
        if rank is not None and rank > best_rank:
            best = play
            best_rank = rank

    return best


class TrickRecord(tuple[RecordT, ...]):
    """A completed trick: exactly four play records, as an immutable tuple.

    A thin ``tuple`` subclass — it iterates, unpacks, slices, and compares
    exactly like the bare four-record tuples it types, so every consumer
    that reads a completed trick as a plain sequence keeps working
    unchanged. What it adds is the completed-trick invariant (exactly four
    records, enforced at construction) and the two derived facts every
    reader wants: :attr:`led_suit` and :meth:`winner`.

    Generic over the record type: a trick out of
    :attr:`contrai_core.PlayState.completed_tricks` holds
    :class:`~contrai_core.Play` records, one out of
    :attr:`contrai_core.PlayObservation.completed_tricks` holds sealed
    :class:`~contrai_core.ObservedPlay` records. The rules only read each
    record's ``card``; the "who" slot rides along untouched.

    Nothing is cached: ``led_suit`` and ``winner`` recompute on every
    call, mirroring how ``PlayState`` recomputes its derived views from
    the flat play history.
    """

    __slots__ = ()

    def __new__(cls, plays: Iterable[RecordT]) -> "TrickRecord[RecordT]":
        """Build a completed-trick record from exactly four plays.

        Args:
            plays: The trick's play records, in play order. A single
                iterable, consumed once.

        Returns:
            The immutable four-record trick.

        Raises:
            TrickStateError: If ``plays`` does not hold exactly four
                records — a completed trick has no other size.
        """

        records = tuple(plays)
        if len(records) != 4:
            raise TrickStateError(
                f"A completed trick holds exactly 4 plays, got "
                f"{len(records)}."
            )
        return super().__new__(cls, records)

    @property
    def led_suit(self) -> Suit:
        """The suit of the trick's first card — the suit that was led."""

        return self[0].card.suit

    def winner(self, trump_suit: Optional[ContractSuit]) -> RecordT:
        """Return the record that won this trick.

        Takes the contract's trump — not a rules object — so call sites
        keep the same signature whichever contract regime a future round
        plays under.

        Args:
            trump_suit: The trump suit to evaluate against, taken from
                the round's contract; ``None`` / ``TrumpVariant.NO_TRUMP``
                reduce every trump branch to the follow-suit rule.

        Returns:
            The winning play record — a :class:`~contrai_core.Play` in
            state contexts, an :class:`~contrai_core.ObservedPlay` in
            observations — never ``None``, since a completed trick always
            has four plays.

        Raises:
            NotImplementedError: If ``trump_suit`` is
                ``TrumpVariant.ALL_TRUMP``, propagated from
                :func:`contrai_core.rules_for`.
        """

        best = _best_play(self, trump_suit)
        assert best is not None  # four plays — a winner always exists
        return best
