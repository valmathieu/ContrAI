"""Tests for the custom exception classes."""

import pytest

from contrai_core import InvalidCardCountError, InvalidPlayerCountError


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
