# Pluggable AI strategy interfaces + shared player-state access.
#
# ``AiPlayer`` no longer owns its bidding and card-play logic directly.
# Instead it holds two strategy objects behind the abstract interfaces
# defined here, injected at construction (see :mod:`.ai`). Today's expert
# rules are the first concrete implementation (see :mod:`.rule_based`);
# future AI levels (MCTS, learned policies — AI roadmap §6) are new
# strategy classes, never edits to ``AiPlayer``.

from abc import ABC, abstractmethod

from contrai_core.auction import Auction
from contrai_core.bid import Bid


class _PlayerStrategy:
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
    def position(self):
        """The owning player's seat position."""
        return self._player.position


class BiddingStrategy(ABC):
    """Interface for an AI bidding policy.

    Implementations decide what :class:`Bid` to make given the current
    :class:`Auction` state. The owning :class:`AiPlayer` delegates
    :meth:`AiPlayer.choose_bid` straight through to :meth:`choose_bid`.
    """

    @abstractmethod
    def choose_bid(self, auction: Auction) -> Bid:
        """Choose a :class:`Bid` for the current auction state.

        Args:
            auction: The current :class:`Auction` state.

        Returns:
            A :class:`Bid` instance the engine will validate.
        """


class CardPlayStrategy(ABC):
    """Interface for an AI card-play policy.

    Implementations choose which card to play and maintain whatever
    per-round state they need (e.g. card tracking). The owning
    :class:`AiPlayer` delegates :meth:`AiPlayer.choose_card`,
    :meth:`AiPlayer.initialize_card_tracking`, and
    :meth:`AiPlayer.update_card_tracking` to this object.
    """

    @abstractmethod
    def choose_card(self, trick, contract, playable_cards):
        """Choose a card to play in the current trick.

        Args:
            trick: The current :class:`Trick` (cards played so far).
            contract: The current :class:`Contract`, or ``None``.
            playable_cards: The cards legally playable this turn.

        Returns:
            The chosen :class:`Card`.
        """

    @abstractmethod
    def initialize_card_tracking(self) -> None:
        """Reset per-round card-tracking state. Called by the game."""

    @abstractmethod
    def update_card_tracking(self, card, player, led_suit, trump_suit) -> None:
        """Record a played card. Called by the game on every play.

        Args:
            card: The card that was played.
            player: The player who played it.
            led_suit: The suit led this trick.
            trump_suit: The current trump suit.
        """
