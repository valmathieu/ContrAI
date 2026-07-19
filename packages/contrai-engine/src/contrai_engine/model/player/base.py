"""Engine-side player abstractions built on :class:`contrai_core.BasePlayer`.

Defines :class:`Player`, the abstract seat contract the engine's
:class:`Round` drives (``choose_bid`` / ``choose_card``), and
:class:`HumanPlayer`, whose choices are deferred to the view.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.player import BasePlayer

if TYPE_CHECKING:
    from contrai_core.card import Card
    from contrai_core.play import PlayObservation


class Player(BasePlayer, ABC):
    """Abstract engine player: a :class:`BasePlayer` that can make decisions.

    Concrete subclasses implement the two decision hooks the engine
    calls during a round — :meth:`choose_bid` during the auction and
    :meth:`choose_card` during trick play. Returning ``None`` from
    either hook signals that the decision is delegated to the view
    (the human-input path).
    """

    @property
    def is_human(self):
        """Returns True if this is a human player."""
        return isinstance(self, HumanPlayer)

    @abstractmethod
    def choose_bid(self, auction: Auction) -> Optional[Bid]:
        """Choose a :class:`Bid` for the current auction state.

        Args:
            auction: The current :class:`Auction`. Use
                ``auction.legal_actions(self)`` to enumerate legal
                bids, or query ``auction.last_contract_bid`` /
                ``auction.partner_bid(self)`` for the strategy
                helpers.

        Returns:
            A :class:`Bid` instance (validated by the engine via
            :meth:`Auction.apply`), or ``None`` to defer to the view
            (the contract for :class:`HumanPlayer`).
        """

    @abstractmethod
    def choose_card(self, observation: 'PlayObservation') -> Optional['Card']:
        """Choose a :class:`Card` to play into the current trick.

        Args:
            observation: The :class:`~contrai_core.PlayObservation` for
                this seat — its own hand, the public trick history, the
                contract/auction, and ``observation.legal_cards`` (the
                legal subset for this turn). The returned card must be one
                of the legal cards — Round raises ``IllegalPlayError``
                otherwise.

        Returns:
            A :class:`Card` drawn from ``observation.legal_cards``, or
            ``None`` to defer to the view (the contract for
            :class:`HumanPlayer`).
        """


class HumanPlayer(Player):
    """A human-controlled seat whose decisions come from the view.

    Both hooks return ``None`` so the engine routes the actual input
    through the view's ``request_*_action`` methods.
    """

    def choose_bid(self, auction: Auction) -> None:
        """Defer to the view's :meth:`request_bid_action`.

        Returns ``None`` by design — Round's bidding loop then
        consults the view to actually drive the human's input.
        """

        return None

    def choose_card(self, observation: 'PlayObservation') -> None:
        """Defer to the view's :meth:`request_card_action`.

        Returns ``None`` by design — Round's trick loop drives the
        human's card choice through the view instead, exactly as
        :meth:`choose_bid` defers bidding to ``request_bid_action``.
        The override exists only to satisfy the abstract base; its
        return value is never consumed for a human.
        """

        return None
