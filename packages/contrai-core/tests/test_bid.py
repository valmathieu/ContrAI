"""Tests for the :class:`Bid` value-carrier hierarchy.

Bids are now frozen dataclasses with no auction-state behaviour —
:meth:`Bid.is_valid_after` and ``BidValidator`` moved to
:class:`contrai_core.Auction` (covered in ``test_auction.py``). What
remains here is the data contract of each variant:

- Construction validation (``ContractBid`` rejects unknown value / suit).
- Equality / hashing semantics (player excluded from comparison,
  variant types still distinct).
- :meth:`ContractBid.get_numeric_value` and the strict ``__gt__``
  ordering used inside the AI's bidding helpers.
- ``__str__`` for the rendering layer.
"""

import pytest

from contrai_core import (
    BasePlayer,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    Suit,
    Team,
)


# ---------------------------------------------------------------------------
# Fixtures — four positioned players + their teams. Some equality tests
# rely on two seats from the same team being constructible, so we keep
# the team-wired fixtures even though Bid equality itself is now
# player-agnostic.
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


# ---------------------------------------------------------------------------
# PassBid
# ---------------------------------------------------------------------------


class TestPassBid:
    def test_str(self, north):
        assert str(PassBid(north)) == "Pass"

    def test_equality_ignores_player(self, north, south):
        # Player is field(compare=False); two PassBids compare equal
        # regardless of who made them.
        assert PassBid(north) == PassBid(south)

    def test_distinct_from_other_variants(self, north):
        assert PassBid(north) != ContractBid(north, 80, Suit.SPADES)
        assert PassBid(north) != DoubleBid(north)
        assert PassBid(north) != RedoubleBid(north)

    def test_player_stored(self, north):
        assert PassBid(north).player is north


# ---------------------------------------------------------------------------
# ContractBid: construction validation
# ---------------------------------------------------------------------------


class TestContractBidConstruction:
    """Frozen dataclass validates value + suit in __post_init__."""

    @pytest.mark.parametrize(
        "value", [80, 90, 100, 110, 120, 130, 140, 150, 160, "Slam", "SoloSlam"]
    )
    def test_valid_values(self, north, value):
        bid = ContractBid(north, value, Suit.SPADES)
        assert bid.value == value
        assert bid.suit == Suit.SPADES

    @pytest.mark.parametrize("suit", list(Suit))
    def test_valid_suits(self, north, suit):
        # NO_TRUMP and ALL_TRUMP are in VALID_SUITS today (list(Suit)).
        bid = ContractBid(north, 80, suit)
        assert bid.suit == suit

    @pytest.mark.parametrize(
        "bad_value",
        [70, 85, 170, 0, -10, "slam", "SLAM", "Capot", "solo", "Solo Slam", "80"],
    )
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
# ContractBid: ordering / numeric value
# ---------------------------------------------------------------------------


class TestContractBidComparison:
    """Numeric value extraction and __gt__."""

    def test_get_numeric_value_for_numeric(self, north):
        assert ContractBid(north, 80, Suit.SPADES).get_numeric_value() == 80
        assert ContractBid(north, 160, Suit.SPADES).get_numeric_value() == 160

    def test_get_numeric_value_for_slam(self, north):
        # 250 = the contract base value (what the bidder commits to);
        # it is one half of the Slam at-risk amount, the other being
        # the flat card-pile substitute. Outranks the 160 numeric ceiling.
        assert ContractBid(north, "Slam", Suit.SPADES).get_numeric_value() == 250

    def test_get_numeric_value_for_solo_slam(self, north):
        # 500 = the Solo Slam contract base value; outranks Slam (250).
        assert ContractBid(north, "SoloSlam", Suit.SPADES).get_numeric_value() == 500

    def test_gt_numeric(self, north):
        a = ContractBid(north, 100, Suit.SPADES)
        b = ContractBid(north, 90, Suit.HEARTS)
        assert a > b
        assert not (b > a)

    def test_gt_slam_over_max_numeric(self, north):
        slam = ContractBid(north, "Slam", Suit.SPADES)
        max_numeric = ContractBid(north, 160, Suit.HEARTS)
        assert slam > max_numeric
        assert not (max_numeric > slam)

    def test_gt_solo_slam_over_slam(self, north):
        solo = ContractBid(north, "SoloSlam", Suit.SPADES)
        slam = ContractBid(north, "Slam", Suit.HEARTS)
        assert solo > slam
        assert not (slam > solo)

    def test_gt_with_non_contract_bid_returns_false(self, north):
        assert (ContractBid(north, 100, Suit.SPADES) > PassBid(north)) is False


# ---------------------------------------------------------------------------
# ContractBid: __str__ + equality semantics
# ---------------------------------------------------------------------------


class TestContractBidDunders:
    def test_str(self, north):
        bid = ContractBid(north, 100, Suit.SPADES)
        assert str(bid) == f"100 {Suit.SPADES}"

    def test_str_slam(self, north):
        bid = ContractBid(north, "Slam", Suit.SPADES)
        assert str(bid) == f"Slam {Suit.SPADES}"

    def test_str_solo_slam(self, north):
        bid = ContractBid(north, "SoloSlam", Suit.SPADES)
        assert str(bid) == f"SoloSlam {Suit.SPADES}"

    def test_equality_ignores_player(self, north, south):
        # Player is excluded from comparison; two ContractBids with
        # the same value + suit but different players still compare equal.
        a = ContractBid(north, 100, Suit.SPADES)
        b = ContractBid(south, 100, Suit.SPADES)
        assert a == b

    def test_equality_by_value_and_suit(self, north):
        a = ContractBid(north, 100, Suit.SPADES)
        c = ContractBid(north, 110, Suit.SPADES)
        d = ContractBid(north, 100, Suit.HEARTS)
        assert a != c
        assert a != d

    def test_distinct_from_other_variants(self, north):
        a = ContractBid(north, 100, Suit.SPADES)
        assert a != PassBid(north)
        assert a != DoubleBid(north)


# ---------------------------------------------------------------------------
# DoubleBid / RedoubleBid — value-carrier behaviour
# ---------------------------------------------------------------------------


class TestDoubleBid:
    def test_str(self, east):
        assert str(DoubleBid(east)) == "Double"

    def test_equality_ignores_player(self, north, east):
        assert DoubleBid(east) == DoubleBid(north)

    def test_distinct_from_other_variants(self, east):
        assert DoubleBid(east) != PassBid(east)
        assert DoubleBid(east) != RedoubleBid(east)


class TestRedoubleBid:
    def test_str(self, north):
        assert str(RedoubleBid(north)) == "Redouble"

    def test_equality_ignores_player(self, north, south):
        assert RedoubleBid(north) == RedoubleBid(south)

    def test_distinct_from_other_variants(self, north):
        assert RedoubleBid(north) != PassBid(north)
        assert RedoubleBid(north) != DoubleBid(north)


# ---------------------------------------------------------------------------
# Immutability — frozen dataclass forbids field reassignment
# ---------------------------------------------------------------------------


class TestImmutability:
    """Frozen dataclasses raise on any attribute reassignment."""

    def test_pass_bid_is_frozen(self, north, south):
        bid = PassBid(north)
        with pytest.raises(Exception):
            bid.player = south

    def test_contract_bid_is_frozen(self, north):
        bid = ContractBid(north, 80, Suit.SPADES)
        with pytest.raises(Exception):
            bid.value = 100
        with pytest.raises(Exception):
            bid.suit = Suit.HEARTS
