"""Tests for the Bid hierarchy and BidValidator.

Covers PassBid / ContractBid / DoubleBid / RedoubleBid validity, Capot
precedence rules from contree-domain.md §5.2, and the team-based
double/redouble authorisation logic.
"""

import pytest

from contrai_core.bid import (
    BidValidator,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
)
from contrai_core.player import BasePlayer
from contrai_core.team import Team
from contrai_core.types import Suit


# ---------------------------------------------------------------------------
# Fixtures: real BasePlayer + Team instances (matches test_team.py house style).
# DoubleBid/RedoubleBid validation compares team identity, so real objects are
# clearer than mocks.
# ---------------------------------------------------------------------------


@pytest.fixture
def north():
    return BasePlayer("North", "North")


@pytest.fixture
def south():
    return BasePlayer("South", "South")


@pytest.fixture
def east():
    return BasePlayer("East", "East")


@pytest.fixture
def west():
    return BasePlayer("West", "West")


@pytest.fixture
def team_ns(north, south):
    team = Team("North-South", [north, south])
    north.team = team
    south.team = team
    return team


@pytest.fixture
def team_ew(east, west):
    team = Team("East-West", [east, west])
    east.team = team
    west.team = team
    return team


@pytest.fixture
def four_players(team_ns, team_ew, north, south, east, west):
    """Force fixture instantiation so all four players have teams assigned."""
    return north, east, south, west


# ---------------------------------------------------------------------------
# PassBid
# ---------------------------------------------------------------------------


class TestPassBid:
    """A PassBid is always valid and can never be doubled."""

    def test_pass_valid_on_empty_history(self, north):
        assert PassBid(north).is_valid_after([]) is True

    def test_pass_valid_after_any_bids(self, north, east, four_players):
        history = [
            ContractBid(east, 90, Suit.HEARTS),
            PassBid(north),
        ]
        assert PassBid(north).is_valid_after(history) is True

    def test_pass_cannot_be_doubled(self, north):
        assert PassBid(north).can_be_doubled() is False

    def test_pass_str(self, north):
        assert str(PassBid(north)) == "Pass"

    def test_pass_equality(self, north, south):
        # PassBid equality is type-only; player identity is ignored.
        assert PassBid(north) == PassBid(south)
        assert PassBid(north) != ContractBid(north, 80, Suit.SPADES)


# ---------------------------------------------------------------------------
# ContractBid: construction validation
# ---------------------------------------------------------------------------


class TestContractBidConstruction:
    """Validation of value + suit at construction time."""

    @pytest.mark.parametrize(
        "value", [80, 90, 100, 110, 120, 130, 140, 150, 160, "Capot"]
    )
    def test_valid_values(self, north, value):
        bid = ContractBid(north, value, Suit.SPADES)
        assert bid.value == value
        assert bid.suit == Suit.SPADES

    @pytest.mark.parametrize("suit", list(Suit))
    def test_valid_suits(self, north, suit):
        # NO_TRUMP is in VALID_SUITS today (list(Suit)) — pin current behaviour.
        bid = ContractBid(north, 80, suit)
        assert bid.suit == suit

    @pytest.mark.parametrize("bad_value", [70, 85, 170, 0, -10, "capot", "CAPOT", "80"])
    def test_invalid_value_raises(self, north, bad_value):
        with pytest.raises(ValueError, match="Invalid contract value"):
            ContractBid(north, bad_value, Suit.SPADES)

    def test_invalid_suit_raises(self, north):
        with pytest.raises(ValueError, match="Invalid trump suit"):
            ContractBid(north, 80, "Spades")  # raw string is not a Suit enum

    def test_player_is_stored(self, north):
        bid = ContractBid(north, 100, Suit.HEARTS)
        assert bid.player is north


# ---------------------------------------------------------------------------
# ContractBid: ordering / precedence (incl. Capot)
# ---------------------------------------------------------------------------


class TestContractBidPrecedence:
    """Precedence rules from contree-domain.md §5.2."""

    def test_first_contract_always_valid(self, north):
        assert ContractBid(north, 80, Suit.SPADES).is_valid_after([]) is True

    def test_higher_numeric_over_lower(self, north, east):
        history = [ContractBid(east, 90, Suit.HEARTS)]
        assert ContractBid(north, 100, Suit.SPADES).is_valid_after(history) is True

    def test_lower_numeric_over_higher_invalid(self, north, east):
        history = [ContractBid(east, 110, Suit.HEARTS)]
        assert ContractBid(north, 100, Suit.SPADES).is_valid_after(history) is False

    def test_equal_numeric_invalid(self, north, east):
        history = [ContractBid(east, 100, Suit.HEARTS)]
        assert ContractBid(north, 100, Suit.SPADES).is_valid_after(history) is False

    def test_capot_over_any_numeric_valid(self, north, east):
        # Capot outranks any numeric bid (domain §5.2).
        for value in [80, 90, 100, 130, 160]:
            history = [ContractBid(east, value, Suit.HEARTS)]
            assert (
                ContractBid(north, "Capot", Suit.SPADES).is_valid_after(history)
                is True
            )

    def test_numeric_over_capot_invalid(self, north, east):
        history = [ContractBid(east, "Capot", Suit.HEARTS)]
        assert ContractBid(north, 160, Suit.SPADES).is_valid_after(history) is False

    def test_capot_over_capot_invalid(self, north, east):
        # Capot is the top of the table — you cannot bid over it.
        history = [ContractBid(east, "Capot", Suit.HEARTS)]
        assert (
            ContractBid(north, "Capot", Suit.SPADES).is_valid_after(history) is False
        )

    def test_passes_in_between_do_not_change_precedence(self, north, east, south):
        history = [
            ContractBid(east, 100, Suit.HEARTS),
            PassBid(south),
            PassBid(north),
        ]
        assert ContractBid(east, 110, Suit.HEARTS).is_valid_after(history) is True
        assert ContractBid(east, 100, Suit.HEARTS).is_valid_after(history) is False


class TestContractBidComparison:
    """Numeric value extraction and __gt__."""

    def test_get_numeric_value_for_numeric(self, north):
        assert ContractBid(north, 80, Suit.SPADES).get_numeric_value() == 80
        assert ContractBid(north, 160, Suit.SPADES).get_numeric_value() == 160

    def test_get_numeric_value_for_capot(self, north):
        assert ContractBid(north, "Capot", Suit.SPADES).get_numeric_value() == 250

    def test_gt_numeric(self, north):
        a = ContractBid(north, 100, Suit.SPADES)
        b = ContractBid(north, 90, Suit.HEARTS)
        assert a > b
        assert not (b > a)

    def test_gt_capot_over_max_numeric(self, north):
        capot = ContractBid(north, "Capot", Suit.SPADES)
        max_numeric = ContractBid(north, 160, Suit.HEARTS)
        assert capot > max_numeric
        assert not (max_numeric > capot)

    def test_gt_with_non_contract_bid_returns_false(self, north):
        assert (ContractBid(north, 100, Suit.SPADES) > PassBid(north)) is False


class TestContractBidDunders:
    def test_str(self, north):
        bid = ContractBid(north, 100, Suit.SPADES)
        assert str(bid) == f"100 {Suit.SPADES}"

    def test_str_capot(self, north):
        bid = ContractBid(north, "Capot", Suit.SPADES)
        assert str(bid) == f"Capot {Suit.SPADES}"

    def test_equality(self, north, south):
        a = ContractBid(north, 100, Suit.SPADES)
        b = ContractBid(south, 100, Suit.SPADES)
        c = ContractBid(north, 110, Suit.SPADES)
        d = ContractBid(north, 100, Suit.HEARTS)
        # Equality compares value + suit only (not player).
        assert a == b
        assert a != c
        assert a != d
        assert a != PassBid(north)

    def test_can_be_doubled(self, north):
        assert ContractBid(north, 100, Suit.SPADES).can_be_doubled() is True


# ---------------------------------------------------------------------------
# DoubleBid validation
# ---------------------------------------------------------------------------


class TestDoubleBid:
    def test_double_invalid_on_empty_history(self, east):
        assert DoubleBid(east).is_valid_after([]) is False

    def test_double_valid_against_opponent_contract(
        self, north, east, four_players
    ):
        history = [ContractBid(north, 100, Suit.SPADES)]
        # East (opponent of North) doubles — valid.
        assert DoubleBid(east).is_valid_after(history) is True

    def test_double_invalid_against_own_team_contract(
        self, north, south, four_players
    ):
        history = [ContractBid(north, 100, Suit.SPADES)]
        # South is North's partner — cannot double own contract.
        assert DoubleBid(south).is_valid_after(history) is False

    def test_double_invalid_if_already_doubled(self, north, east, four_players):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
        ]
        # West cannot re-double once east has already doubled.
        assert DoubleBid(east).is_valid_after(history) is False

    def test_double_invalid_after_redouble(self, north, east, four_players):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
            RedoubleBid(north),
        ]
        assert DoubleBid(east).is_valid_after(history) is False

    def test_double_invalid_after_pass_since_contract(
        self, north, east, south, four_players
    ):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            PassBid(east),
        ]
        # Once the next player passes, the doubling window closes.
        assert DoubleBid(east).is_valid_after(history) is False

    def test_double_cannot_be_doubled(self, east):
        assert DoubleBid(east).can_be_doubled() is False

    def test_double_str_and_equality(self, north, east):
        assert str(DoubleBid(east)) == "Double"
        assert DoubleBid(east) == DoubleBid(north)
        assert DoubleBid(east) != PassBid(east)


# ---------------------------------------------------------------------------
# RedoubleBid validation
# ---------------------------------------------------------------------------


class TestRedoubleBid:
    def test_redouble_invalid_on_empty_history(self, north):
        assert RedoubleBid(north).is_valid_after([]) is False

    def test_redouble_invalid_without_double(self, north, four_players):
        history = [ContractBid(north, 100, Suit.SPADES)]
        assert RedoubleBid(north).is_valid_after(history) is False

    def test_redouble_valid_for_contracting_team(
        self, north, south, east, four_players
    ):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
        ]
        # Either member of the contracting team may redouble.
        assert RedoubleBid(north).is_valid_after(history) is True
        assert RedoubleBid(south).is_valid_after(history) is True

    def test_redouble_invalid_for_opposing_team(self, north, east, west, four_players):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
        ]
        # Doubling side cannot then redouble.
        assert RedoubleBid(west).is_valid_after(history) is False

    def test_redouble_invalid_if_already_redoubled(
        self, north, east, four_players
    ):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
            RedoubleBid(north),
        ]
        assert RedoubleBid(north).is_valid_after(history) is False

    def test_redouble_invalid_after_pass_since_double(
        self, north, east, south, four_players
    ):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
            PassBid(south),
        ]
        assert RedoubleBid(north).is_valid_after(history) is False

    def test_redouble_cannot_be_doubled(self, north):
        assert RedoubleBid(north).can_be_doubled() is False

    def test_redouble_str_and_equality(self, north, south):
        assert str(RedoubleBid(north)) == "Redouble"
        assert RedoubleBid(north) == RedoubleBid(south)
        assert RedoubleBid(north) != DoubleBid(north)


# ---------------------------------------------------------------------------
# BidValidator
# ---------------------------------------------------------------------------


class TestBidValidator:
    def test_is_bid_valid_delegates(self, north):
        # is_bid_valid is a thin wrapper over Bid.is_valid_after.
        assert (
            BidValidator.is_bid_valid(ContractBid(north, 80, Suit.SPADES), [])
            is True
        )
        assert BidValidator.is_bid_valid(PassBid(north), []) is True

    def test_get_last_contract_none_when_no_contract(self, north):
        assert BidValidator.get_last_contract([]) is None
        assert BidValidator.get_last_contract([PassBid(north)]) is None

    def test_get_last_contract_returns_most_recent(self, north, east):
        first = ContractBid(north, 80, Suit.SPADES)
        second = ContractBid(east, 90, Suit.HEARTS)
        last = BidValidator.get_last_contract([first, PassBid(east), second])
        assert last is second

    def test_has_double_true_when_double_after_contract(
        self, north, east, four_players
    ):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
        ]
        assert BidValidator.has_double(history) is True

    def test_has_double_false_when_no_double(self, north, four_players):
        assert (
            BidValidator.has_double([ContractBid(north, 100, Suit.SPADES)]) is False
        )

    def test_has_redouble_true_when_redouble_after_double(
        self, north, east, four_players
    ):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
            RedoubleBid(north),
        ]
        assert BidValidator.has_redouble(history) is True

    def test_has_redouble_false_when_only_double(self, north, east, four_players):
        history = [
            ContractBid(north, 100, Suit.SPADES),
            DoubleBid(east),
        ]
        assert BidValidator.has_redouble(history) is False

    def test_count_passes_after_last_action(self, north, east, south):
        bid = ContractBid(north, 100, Suit.SPADES)
        assert BidValidator.count_passes_after_last_action([]) == 0
        assert BidValidator.count_passes_after_last_action([bid]) == 0
        assert BidValidator.count_passes_after_last_action([bid, PassBid(east)]) == 1
        assert (
            BidValidator.count_passes_after_last_action(
                [bid, PassBid(east), PassBid(south)]
            )
            == 2
        )
        # Trailing non-pass resets the count.
        assert (
            BidValidator.count_passes_after_last_action(
                [bid, PassBid(east), ContractBid(south, 110, Suit.HEARTS)]
            )
            == 0
        )
