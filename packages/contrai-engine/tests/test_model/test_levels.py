# Unit tests for the AI level registry + factory.

from contrai_engine.model.player import (
    AI_LEVELS,
    AiPlayer,
    RuleBasedBiddingStrategy,
    RuleBasedCardPlayStrategy,
    make_ai_player,
)


def test_expert_level_maps_to_rule_based_pair():
    """``AI_LEVELS["expert"]`` is the rule-based (bidding, card-play) pair."""
    assert AI_LEVELS["expert"] == (
        RuleBasedBiddingStrategy,
        RuleBasedCardPlayStrategy,
    )


def test_make_ai_player_builds_expert_by_default():
    """``make_ai_player`` defaults to the expert level."""
    player = make_ai_player("Bot", "South")
    assert isinstance(player, AiPlayer)
    assert isinstance(player.bidding, RuleBasedBiddingStrategy)
    assert isinstance(player.cardplay, RuleBasedCardPlayStrategy)


def test_make_ai_player_wires_strategies_to_the_player():
    """The built strategies hold a back-reference to the player."""
    player = make_ai_player("Bot", "South", level="expert")
    assert player.bidding._player is player
    assert player.cardplay._player is player
