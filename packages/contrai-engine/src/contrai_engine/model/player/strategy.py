"""Pluggable AI strategy interfaces + shared player-state access.

``AiPlayer`` no longer owns its bidding and card-play logic directly.
Instead it holds two strategy objects behind the abstract interfaces
defined here, injected at construction (see :mod:`.ai`). Today's expert
rules are the first concrete implementation (see :mod:`.rule_based`);
future AI levels (MCTS, learned policies) are new
strategy classes, never edits to ``AiPlayer``.

Both hooks answer with a *decision* — :class:`~.rationale.BidDecision` /
:class:`~.rationale.CardDecision` — rather than a bare :class:`Bid` or
:class:`Card`, so every level explains itself through the same seam it
acts through. See :mod:`.rationale` for why the trace rides the return
type.
"""

from abc import ABC, abstractmethod

from contrai_core.auction import Auction
from contrai_core.position import Position

from .rationale import BidDecision, CardDecision


class PlayerStateMixin:
    """Mix-in giving a strategy live read access to its owning player.

    A strategy needs to read the player's table state (``hand``,
    ``team``, ``position``) while making decisions. It keeps a
    back-reference to the player and exposes those fields as
    properties, so re-assigning ``player.hand`` between rounds (or in
    tests) is reflected immediately — the strategy never caches a stale
    copy. This also lets the moved method bodies stay nearly verbatim:
    ``self.hand`` / ``self.team`` / ``self.position`` keep working.
    """

    def __init__(self, player):
        """Bind this strategy to the player it advises.

        Args:
            player: The owning :class:`~contrai_engine.model.player.AiPlayer`.
        """

        self._player = player

    @property
    def hand(self):
        """The owning player's current hand."""
        return self._player.hand

    @property
    def team(self):
        """The owning player's team."""
        return self._player.team

    @property
    def position(self) -> Position:
        """The owning player's seat position."""
        return self._player.position


class BiddingStrategy(ABC):
    """Interface for an AI bidding policy.

    Implementations decide what :class:`Bid` to make given the current
    :class:`Auction` state, and say **why** — the return is a
    :class:`~.rationale.BidDecision`, never a bare bid. The owning
    :class:`AiPlayer` delegates :meth:`AiPlayer.choose_bid` straight
    through to :meth:`choose_bid`.
    """

    @abstractmethod
    def choose_bid(self, auction: Auction) -> BidDecision:
        """Choose a :class:`Bid` for the current auction state.

        Args:
            auction: The current :class:`Auction` state.

        Returns:
            A :class:`~.rationale.BidDecision` — the bid the engine will
            validate, paired with the :class:`~.rationale.Rationale`
            naming the rule that produced it.
        """


class CardPlayStrategy(ABC):
    """Interface for an AI card-play policy.

    Implementations choose which card to play from a single frozen
    :class:`~contrai_core.PlayObservation` — the observing seat's own
    hand, the public trick history, the contract/auction, and the seat's
    legal plays. Any card tracking a strategy wants (fallen cards,
    inferred voids) is derived from that public history; the observation
    is the only input, so a strategy can never read another seat's hand.
    The owning :class:`AiPlayer` delegates :meth:`AiPlayer.choose_card`
    to this object, which answers with a
    :class:`~.rationale.CardDecision` — the card *and* the reasoning
    behind it, never a bare card.
    """

    @abstractmethod
    def choose_card(self, observation) -> CardDecision:
        """Choose a card to play in the current trick.

        Args:
            observation: The :class:`~contrai_core.PlayObservation` for
                the observing seat — its hand, legal cards, the contract,
                the public trick history, and the table ruleset.

        Returns:
            A :class:`~.rationale.CardDecision` — the chosen card, drawn
            from ``observation.legal_cards``, paired with the
            :class:`~.rationale.Rationale` naming the rule that produced
            it.
        """
