"""Tests for the custom exception classes."""

import pytest

from contrai_core import (
    Card,
    ContraiError,
    IllegalBidError,
    IllegalPlayError,
    InvalidCardCountError,
    InvalidContractError,
    InvalidPlayerCountError,
    PlayRuleViolation,
    Rank,
    Suit,
    TrickStateError,
)


ALL_DOMAIN_ERRORS = [
    InvalidPlayerCountError,
    InvalidCardCountError,
    IllegalBidError,
    IllegalPlayError,
    TrickStateError,
    InvalidContractError,
]


class TestContraiError:
    def test_base_is_exception_subclass(self):
        assert issubclass(ContraiError, Exception)

    @pytest.mark.parametrize("error_cls", ALL_DOMAIN_ERRORS)
    def test_domain_error_is_contrai_error(self, error_cls):
        assert issubclass(error_cls, ContraiError)

    @pytest.mark.parametrize("error_cls", ALL_DOMAIN_ERRORS)
    def test_domain_error_is_value_error(self, error_cls):
        # ValueError stays in the MRO so legacy ``except ValueError`` and
        # ``pytest.raises(ValueError)`` call sites keep working.
        assert issubclass(error_cls, ValueError)


class TestPlayRuleViolation:
    def test_three_members_exist(self):
        assert {m.name for m in PlayRuleViolation} == {
            "MUST_FOLLOW_SUIT",
            "MUST_TRUMP",
            "MUST_OVERTRUMP",
        }

    def test_str_enum_behaviour(self):
        assert PlayRuleViolation.MUST_TRUMP == "must_trump"
        assert PlayRuleViolation.MUST_FOLLOW_SUIT == "must_follow_suit"
        assert PlayRuleViolation.MUST_OVERTRUMP == "must_overtrump"


class TestIllegalPlayError:
    def test_is_contrai_and_value_error(self):
        assert issubclass(IllegalPlayError, ContraiError)
        assert issubclass(IllegalPlayError, ValueError)

    def test_attributes_are_stored(self):
        card = Card(Suit.SPADES, Rank.EIGHT)
        legal = [Card(Suit.SPADES, Rank.JACK), Card(Suit.SPADES, Rank.NINE)]
        err = IllegalPlayError(
            card, PlayRuleViolation.MUST_OVERTRUMP, legal, context="South card play"
        )
        assert err.card is card
        assert err.reason is PlayRuleViolation.MUST_OVERTRUMP
        # legal_cards is coerced to a tuple for diagnostics.
        assert err.legal_cards == tuple(legal)
        assert isinstance(err.legal_cards, tuple)
        assert err.context == "South card play"

    def test_message_with_context(self):
        card = Card(Suit.SPADES, Rank.EIGHT)
        err = IllegalPlayError(
            card, PlayRuleViolation.MUST_TRUMP, [], context="South card play"
        )
        message = str(err)
        assert message.startswith("South card play: ")
        assert "must_trump" in message
        assert "0 legal alternative(s)" in message

    def test_message_without_context(self):
        card = Card(Suit.HEARTS, Rank.KING)
        err = IllegalPlayError(
            card, PlayRuleViolation.MUST_FOLLOW_SUIT, [Card(Suit.HEARTS, Rank.ACE)]
        )
        message = str(err)
        assert not message.startswith(":")
        assert "must_follow_suit" in message
        assert "1 legal alternative(s)" in message

    def test_reason_round_trips(self):
        err = IllegalPlayError(
            Card(Suit.CLUBS, Rank.SEVEN), PlayRuleViolation.MUST_TRUMP, []
        )
        assert err.reason == PlayRuleViolation.MUST_TRUMP
        assert err.reason == "must_trump"


class TestTrickStateError:
    def test_is_contrai_and_value_error(self):
        assert issubclass(TrickStateError, ContraiError)
        assert issubclass(TrickStateError, ValueError)

    def test_message_without_context(self):
        err = TrickStateError("Cannot add a card to a complete trick")
        assert str(err) == "Cannot add a card to a complete trick"
        assert err.context == ""

    def test_message_with_context(self):
        err = TrickStateError("Cannot add a card to a complete trick", context="Trick.add_play")
        assert str(err) == "Trick.add_play: Cannot add a card to a complete trick"
        assert err.context == "Trick.add_play"


class TestInvalidContractError:
    def test_is_contrai_and_value_error(self):
        assert issubclass(InvalidContractError, ContraiError)
        assert issubclass(InvalidContractError, ValueError)

    def test_message_without_context(self):
        err = InvalidContractError("Invalid contract value: 70")
        assert str(err) == "Invalid contract value: 70"
        assert err.context == ""

    def test_message_with_context(self):
        err = InvalidContractError("Invalid trump suit: Spades", context="ContractBid")
        assert str(err) == "ContractBid: Invalid trump suit: Spades"
        assert err.context == "ContractBid"


class TestInvalidPlayerCountError:
    def test_is_value_error_subclass(self):
        assert issubclass(InvalidPlayerCountError, ValueError)

    def test_attributes_are_stored(self):
        err = InvalidPlayerCountError(expected_count=4, actual_count=3, context="Deck.deal")
        assert err.expected_count == 4
        assert err.actual_count == 3
        assert err.context == "Deck.deal"

    def test_message_with_context(self):
        err = InvalidPlayerCountError(4, 3, "Deck.deal")
        assert str(err) == "Deck.deal: Expected 4 players, got 3"

    def test_message_without_context(self):
        err = InvalidPlayerCountError(4, 3)
        assert str(err) == "Expected 4 players, got 3"

    def test_can_be_raised_and_caught_as_value_error(self):
        with pytest.raises(ValueError, match="Expected 2 players, got 0"):
            raise InvalidPlayerCountError(2, 0)


class TestInvalidCardCountError:
    def test_is_value_error_subclass(self):
        assert issubclass(InvalidCardCountError, ValueError)

    def test_attributes_are_stored(self):
        err = InvalidCardCountError(expected_count=32, actual_count=20, context="Deck.deal")
        assert err.expected_count == 32
        assert err.actual_count == 20
        assert err.context == "Deck.deal"

    def test_message_with_context(self):
        err = InvalidCardCountError(32, 20, "Deck.deal")
        assert str(err) == "Deck.deal: Expected 32 cards, got 20"

    def test_message_without_context(self):
        err = InvalidCardCountError(32, 20)
        assert str(err) == "Expected 32 cards, got 20"
