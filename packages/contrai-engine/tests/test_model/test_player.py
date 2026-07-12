# Unit tests for the Player classes (Player, HumanPlayer, AiPlayer)

from contrai_engine.model.player import (
    AiPlayer,
    HumanPlayer,
    RuleBasedBiddingStrategy,
    RuleBasedCardPlayStrategy,
)
from contrai_core import (
    Auction,
    PassBid,
)


class TestIsHumanProperty:
    """The engine's is_human property splits human from AI players.

    Identity state (name, position, hand, team) comes from
    :class:`contrai_core.BasePlayer` and is covered by core's
    ``test_base_player.py``; here we only assert the engine-specific
    polymorphic behavior.
    """

    def test_human_player_is_human(self):
        """HumanPlayer reports is_human True."""
        assert HumanPlayer("Alice", "North").is_human is True

    def test_ai_player_is_not_human(self):
        """AiPlayer reports is_human False."""
        assert AiPlayer("Bot", "South").is_human is False


class TestAiPlayerStrategyInjection:
    """Test that AiPlayer injects and delegates to its strategies."""

    def test_default_strategies_are_rule_based(self):
        """An AiPlayer built with defaults gets the rule-based pair."""
        player = AiPlayer("Bot", "South")
        assert isinstance(player.bidding, RuleBasedBiddingStrategy)
        assert isinstance(player.cardplay, RuleBasedCardPlayStrategy)
        # Each strategy reads player state live through its back-reference.
        assert player.bidding._player is player
        assert player.cardplay._player is player

    def test_choose_bid_delegates_to_bidding_strategy(self):
        """AiPlayer.choose_bid routes straight to the injected strategy."""
        player = AiPlayer("Bot", "South")
        sentinel = PassBid(player)
        calls = []

        def spy(auction):
            calls.append(auction)
            return sentinel

        player.bidding.choose_bid = spy  # type: ignore[method-assign]
        auction = Auction()
        result = player.choose_bid(auction)
        assert result is sentinel
        assert calls == [auction]

    def test_choose_card_delegates_to_cardplay_strategy(self):
        """AiPlayer.choose_card routes the observation straight to the strategy."""
        player = AiPlayer("Bot", "South")
        sentinel = object()
        calls = []

        def spy(observation):
            calls.append(observation)
            return sentinel

        player.cardplay.choose_card = spy  # type: ignore[method-assign]
        result = player.choose_card("observation")
        assert result is sentinel
        assert calls == ["observation"]

    def test_custom_injected_factories_are_used(self):
        """Factories passed at construction replace the defaults."""

        class StubBidding(RuleBasedBiddingStrategy):
            pass

        class StubCardPlay(RuleBasedCardPlayStrategy):
            pass

        player = AiPlayer("Bot", "South", bidding=StubBidding, cardplay=StubCardPlay)
        assert type(player.bidding) is StubBidding
        assert type(player.cardplay) is StubCardPlay
        assert player.bidding._player is player
        assert player.cardplay._player is player

