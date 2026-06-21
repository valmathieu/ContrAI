# AiPlayer — holds pluggable strategies and delegates to them.
#
# ``AiPlayer`` owns no strategic logic of its own. It holds a bidding
# strategy and a card-play strategy behind the :mod:`.strategy`
# interfaces, injected at construction, and routes the engine's calls to
# them. The defaults are the expert rule-based strategies, so
# ``AiPlayer("Bot", "South")`` keeps producing today's bot.

from contrai_core.auction import Auction
from contrai_core.bid import Bid

from .base import Player
from .rule_based import RuleBasedBiddingStrategy, RuleBasedCardPlayStrategy


class AiPlayer(Player):
    """AI player delegating bidding and card play to injected strategies.

    Each strategy is supplied as a *factory* (``player -> strategy``, i.e.
    the strategy class itself) so the strategy can take a back-reference
    to this player while the player is still being built. Defaults
    reproduce today's expert bot; pass different factories (or use
    :func:`make_ai_player`) to mix and match levels.
    """

    def __init__(self, name, position,
                 bidding=RuleBasedBiddingStrategy,
                 cardplay=RuleBasedCardPlayStrategy):
        """Build an AI player with injected strategies.

        Args:
            name: Display name.
            position: Seat position (``'North'`` / ``'South'`` / …).
            bidding: A factory ``player -> BiddingStrategy``. Defaults to
                :class:`RuleBasedBiddingStrategy` (the ``"expert"`` level).
            cardplay: A factory ``player -> CardPlayStrategy``. Defaults to
                :class:`RuleBasedCardPlayStrategy` (the ``"expert"`` level).
        """

        super().__init__(name, position)
        self.bidding = bidding(self)
        self.cardplay = cardplay(self)

    def choose_bid(self, auction: Auction) -> Bid:
        """Delegate to the injected bidding strategy."""
        return self.bidding.choose_bid(auction)

    def choose_card(self, trick, contract, playable_cards):
        """Delegate to the injected card-play strategy."""
        return self.cardplay.choose_card(trick, contract, playable_cards)

    def initialize_card_tracking(self):
        """Delegate card-tracking reset to the card-play strategy."""
        self.cardplay.initialize_card_tracking()

    def update_card_tracking(self, card, player, led_suit, trump_suit):
        """Delegate a played-card update to the card-play strategy."""
        self.cardplay.update_card_tracking(card, player, led_suit, trump_suit)
