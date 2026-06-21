# Player and HumanPlayer classes

from abc import ABC, abstractmethod
from typing import Optional

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.player import BasePlayer


class Player(BasePlayer, ABC):
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

        pass

    @abstractmethod
    def choose_card(self, trick, contract, playable_cards):
        pass


class HumanPlayer(Player):
    def choose_bid(self, auction: Auction) -> None:
        """Defer to the view's :meth:`request_bid_action`.

        Returns ``None`` by design — Round's bidding loop then
        consults the view to actually drive the human's input.
        """

        return None

    def choose_card(self, trick, contract, playable_cards):
        # This method should be called by the controller via the view
        return None  # To be implemented in controller/view
