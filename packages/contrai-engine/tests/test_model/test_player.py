# Unit tests for the Player classes (Player, HumanPlayer, AiPlayer)

import pytest
from contrai_engine.model.player import (
    AiPlayer,
    HumanPlayer,
    RuleBasedBiddingStrategy,
    RuleBasedCardPlayStrategy,
    wire_to_bid,
)
from contrai_core import (
    Auction,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
)
from contrai_core.types import Suit


class TestWireToBid:
    """Test the legacy wire-format → :class:`Bid` bridge."""

    @pytest.fixture
    def player(self):
        """A plain player to attach bids to."""
        return AiPlayer("Bot", "South")

    def test_keyword_wires_map_to_their_bid_types(self, player):
        """``'Pass'`` / ``'Double'`` / ``'Redouble'`` lift to their classes."""
        assert isinstance(wire_to_bid(player, "Pass"), PassBid)
        assert isinstance(wire_to_bid(player, "Double"), DoubleBid)
        assert isinstance(wire_to_bid(player, "Redouble"), RedoubleBid)

    def test_valid_tuple_yields_contract_bid(self, player):
        """A legal ``(value, suit)`` tuple builds a matching ContractBid."""
        bid = wire_to_bid(player, (80, Suit.HEARTS))
        assert isinstance(bid, ContractBid)
        assert bid.value == 80
        assert bid.suit == Suit.HEARTS

    def test_invalid_contract_value_falls_back_to_pass(self, player):
        """A bad contract value raises InvalidContractError, caught as a Pass.

        85 is not on ``ContractBid.VALID_VALUES``, so construction raises
        :class:`InvalidContractError`. The bridge must swallow that
        specific domain error and fall back to a :class:`PassBid`.
        """
        assert isinstance(wire_to_bid(player, (85, Suit.HEARTS)), PassBid)

    def test_unknown_payload_falls_back_to_pass(self, player):
        """An unrecognised wire payload falls back to a Pass."""
        assert isinstance(wire_to_bid(player, "garbage"), PassBid)


class TestPlayer:
    """Test the abstract Player class"""

    def test_player_creation(self):
        """Test creating a human player"""
        player = HumanPlayer("Alice", "North")
        assert player.name == "Alice"
        assert player.position == "North"
        assert len(player.hand) == 0
        assert player.team is None
        assert player.is_human is True

    def test_ai_player_creation(self):
        """Test creating an AI player"""
        player = AiPlayer("Bot", "South")
        assert player.name == "Bot"
        assert player.position == "South"
        assert len(player.hand) == 0
        assert player.team is None
        assert player.is_human is False


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
        """AiPlayer.choose_card routes straight to the injected strategy."""
        player = AiPlayer("Bot", "South")
        sentinel = object()
        calls = []

        def spy(trick, contract, playable_cards):
            calls.append((trick, contract, playable_cards))
            return sentinel

        player.cardplay.choose_card = spy  # type: ignore[method-assign]
        result = player.choose_card("trick", "contract", "cards")
        assert result is sentinel
        assert calls == [("trick", "contract", "cards")]

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

