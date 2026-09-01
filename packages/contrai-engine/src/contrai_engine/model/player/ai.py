"""AiPlayer — holds pluggable strategies and delegates to them.

``AiPlayer`` owns no strategic logic of its own. It holds a bidding
strategy and a card-play strategy behind the :mod:`.strategy`
interfaces, injected at construction, and routes the engine's calls to
them. The defaults are the expert rule-based strategies, so
``AiPlayer("Bot", Position.SOUTH)`` keeps producing today's bot.
"""

from contrai_core.auction import Auction
from contrai_core.position import Position

from .base import Player
from .rationale import BidDecision, CardDecision
from .rule_based import RuleBasedBiddingStrategy, RuleBasedCardPlayStrategy


class AiPlayer(Player):
    """AI player delegating bidding and card play to injected strategies.

    Each strategy is supplied as a *factory* (``player -> strategy``, i.e.
    the strategy class itself) so the strategy can take a back-reference
    to this player while the player is still being built. Defaults
    reproduce today's expert bot; pass different factories (or use
    :func:`make_ai_player`) to mix and match levels.
    """

    def __init__(self, name, position: Position | None = None,
                 bidding=RuleBasedBiddingStrategy,
                 cardplay=RuleBasedCardPlayStrategy):
        """Build an AI player with injected strategies.

        Args:
            name: Display name.
            position: The seat this player occupies. Omit it to leave the
                bot unseated and let ``Game`` assign the seat — the
                strategies read the seat off the player at decision time,
                never at construction.
            bidding: A factory ``player -> BiddingStrategy``. Defaults to
                :class:`RuleBasedBiddingStrategy` (the ``"expert"`` level).
            cardplay: A factory ``player -> CardPlayStrategy``. Defaults to
                :class:`RuleBasedCardPlayStrategy` (the ``"expert"`` level).
        """

        super().__init__(name, position)
        self.bidding = bidding(self)
        self.cardplay = cardplay(self)

    def choose_bid(self, auction: Auction) -> BidDecision:
        """Delegate to the injected bidding strategy.

        Returns:
            The strategy's :class:`~.rationale.BidDecision` — the bid and
            the rule that produced it — passed through untouched.
        """
        return self.bidding.choose_bid(auction)

    def choose_card(self, observation) -> CardDecision:
        """Delegate to the injected card-play strategy.

        Returns:
            The strategy's :class:`~.rationale.CardDecision` — the card
            and the rule that produced it — passed through untouched.
        """
        return self.cardplay.choose_card(observation)
