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
- :func:`seal_bid`, the projection onto the bidder's seat that the
  imperfect-information observation surface is built from.
"""

import pytest

from contrai_core import (
    BasePlayer,
    Bid,
    ContractBid,
    DoubleBid,
    InvalidContractError,
    PassBid,
    Position,
    RedoubleBid,
    SlamLevel,
    Suit,
    Team,
    TrumpVariant,
    seal_bid,
)


# ---------------------------------------------------------------------------
# Fixtures — four positioned players + their teams. Some equality tests
# rely on two seats from the same team being constructible, so we keep
# the team-wired fixtures even though Bid equality itself is now
# player-agnostic.
# ---------------------------------------------------------------------------


@pytest.fixture
def north():
    """North-seat player, initially without a team."""
    return BasePlayer("North", Position.NORTH)


@pytest.fixture
def south():
    """South-seat player, initially without a team."""
    return BasePlayer("South", Position.SOUTH)


@pytest.fixture
def east():
    """East-seat player, initially without a team."""
    return BasePlayer("East", Position.EAST)


@pytest.fixture
def west():
    """West-seat player, initially without a team."""
    return BasePlayer("West", Position.WEST)


@pytest.fixture
def team_ns(north, south):
    """North-South team, wired onto both seats."""
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
        "value",
        [80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180,
         SlamLevel.SLAM, SlamLevel.SOLO_SLAM],
    )
    def test_valid_values(self, north, value):
        bid = ContractBid(north, value, Suit.SPADES)
        assert bid.value == value
        assert bid.suit == Suit.SPADES

    @pytest.mark.parametrize("suit", ContractBid.VALID_SUITS)
    def test_valid_suits(self, north, suit):
        # Parametrized over VALID_SUITS itself: the bookable trumps are the
        # four card suits plus NO_TRUMP, and the list is what
        # Auction.legal_actions iterates.
        bid = ContractBid(north, 80, suit)
        assert bid.suit == suit

    def test_valid_suits_is_every_contract_suit_but_all_trump(self):
        assert ContractBid.VALID_SUITS == [*Suit, TrumpVariant.NO_TRUMP]
        assert TrumpVariant.ALL_TRUMP not in ContractBid.VALID_SUITS

    @pytest.mark.parametrize(
        "bad_value",
        # The old string sentinels "Slam" / "SoloSlam" are no longer
        # valid — only the SlamLevel members are.
        [70, 85, 190, 0, -10, "slam", "SLAM", "Capot", "solo", "Solo Slam",
         "80", "Slam", "SoloSlam"],
    )
    def test_invalid_value_raises(self, north, bad_value):
        with pytest.raises(InvalidContractError, match="Invalid contract value"):
            ContractBid(north, bad_value, Suit.SPADES)

    def test_invalid_suit_raises(self, north):
        with pytest.raises(InvalidContractError, match="Invalid trump suit"):
            ContractBid(north, 80, "Spades")  # raw string is not a Suit enum

    def test_all_trump_is_rejected_with_its_own_message(self, north):
        # Unimplemented rather than unknown, and the message says so — an
        # all-trump round would reorder and re-score every card, so it is
        # refused at the auction instead of played as something else.
        with pytest.raises(InvalidContractError, match="All-trump"):
            ContractBid(north, 80, TrumpVariant.ALL_TRUMP)

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
        assert (
            ContractBid(north, SlamLevel.SLAM, Suit.SPADES).get_numeric_value()
            == 250
        )

    def test_get_numeric_value_for_solo_slam(self, north):
        # 500 = the Solo Slam contract base value; outranks Slam (250).
        assert (
            ContractBid(north, SlamLevel.SOLO_SLAM, Suit.SPADES).get_numeric_value()
            == 500
        )

    def test_gt_numeric(self, north):
        a = ContractBid(north, 100, Suit.SPADES)
        b = ContractBid(north, 90, Suit.HEARTS)
        assert a > b
        assert not (b > a)

    def test_gt_slam_over_max_numeric(self, north):
        slam = ContractBid(north, SlamLevel.SLAM, Suit.SPADES)
        max_numeric = ContractBid(north, 160, Suit.HEARTS)
        assert slam > max_numeric
        assert not (max_numeric > slam)

    def test_gt_solo_slam_over_slam(self, north):
        solo = ContractBid(north, SlamLevel.SOLO_SLAM, Suit.SPADES)
        slam = ContractBid(north, SlamLevel.SLAM, Suit.HEARTS)
        assert solo > slam
        assert not (slam > solo)

    def test_gt_with_non_contract_bid_returns_false(self, north):
        assert (ContractBid(north, 100, Suit.SPADES) > PassBid(north)) is False


# ---------------------------------------------------------------------------
# ContractBid: __str__ + equality semantics
# ---------------------------------------------------------------------------


class TestContractBidDunders:
    # Suits are spelled out as literals rather than interpolated as
    # f"{Suit.SPADES}": interpolating puts the same __str__ on both sides of
    # the assertion, so it would hold whatever __str__ returned.
    def test_str(self, north):
        bid = ContractBid(north, 100, Suit.SPADES)
        assert str(bid) == "100 Spades"

    def test_str_slam(self, north):
        bid = ContractBid(north, SlamLevel.SLAM, Suit.SPADES)
        assert str(bid) == "Slam Spades"

    def test_str_solo_slam(self, north):
        # SlamLevel.__str__ uses the human label "Solo Slam" (spaced).
        bid = ContractBid(north, SlamLevel.SOLO_SLAM, Suit.SPADES)
        assert str(bid) == "Solo Slam Spades"

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


# ---------------------------------------------------------------------------
# SlamLevel — single source of truth for the all-tricks contracts
# ---------------------------------------------------------------------------


class TestSlamLevel:
    """The enum owns the 250 / 500 base values and the display labels."""

    def test_base_values(self):
        assert SlamLevel.SLAM.base_value == 250
        assert SlamLevel.SOLO_SLAM.base_value == 500

    def test_labels_via_str(self):
        assert str(SlamLevel.SLAM) == "Slam"
        assert str(SlamLevel.SOLO_SLAM) == "Solo Slam"

    def test_valid_values_ends_with_slam_members(self):
        # The slam members live last so Auction.legal_actions' monotonic
        # iteration (numeric ascending, then the all-tricks bids) holds.
        assert ContractBid.VALID_VALUES[-2:] == [
            SlamLevel.SLAM,
            SlamLevel.SOLO_SLAM,
        ]

    def test_not_an_int(self):
        # Plain Enum (not IntEnum): a Slam's value must never compare
        # equal to its numeric points, so scoring can't confuse them.
        assert SlamLevel.SLAM != 250
        assert not isinstance(SlamLevel.SLAM, int)


# ---------------------------------------------------------------------------
# seal_bid — the bid-side half of the observation trust boundary
# ---------------------------------------------------------------------------


class TestSealBid:
    """Projecting a bid onto its bidder's seat must lose only the player.

    The sealed bid is what a :class:`contrai_core.PlayObservation` hands
    a strategy, so nothing reachable from it may be a live player — and
    everything else about the announcement must survive intact.
    """

    def _all_variants(self, player):
        """One instance of each of the four variants, same bidder."""
        return [
            PassBid(player),
            ContractBid(player, 100, Suit.SPADES),
            ContractBid(player, SlamLevel.SOLO_SLAM, TrumpVariant.NO_TRUMP),
            DoubleBid(player),
            RedoubleBid(player),
        ]

    def test_player_slot_becomes_the_bidder_seat(self, north):
        for bid in self._all_variants(north):
            assert seal_bid(bid).player is Position.NORTH

    def test_no_base_player_survives(self, north):
        for bid in self._all_variants(north):
            assert not isinstance(seal_bid(bid).player, BasePlayer)

    def test_concrete_variant_is_preserved(self, north):
        # The sum type is what pattern-matching consumers dispatch on;
        # sealing must not collapse it to the Bid base.
        for bid in self._all_variants(north):
            assert type(seal_bid(bid)) is type(bid)

    def test_contract_payload_is_preserved(self, north):
        sealed = seal_bid(ContractBid(north, 150, Suit.HEARTS))
        assert sealed.value == 150
        assert sealed.suit is Suit.HEARTS
        assert sealed.get_numeric_value() == 150

    def test_slam_payload_is_preserved(self, north):
        sealed = seal_bid(ContractBid(north, SlamLevel.SLAM, Suit.CLUBS))
        assert sealed.value is SlamLevel.SLAM
        assert sealed.get_numeric_value() == 250

    def test_sealed_bid_equals_its_source(self, north):
        # ``player`` is compare=False, so reseating is equality-preserving
        # — a sealed auction history still compares to the live one.
        for bid in self._all_variants(north):
            assert seal_bid(bid) == bid

    def test_str_rendering_is_unchanged(self, north):
        for bid in self._all_variants(north):
            assert str(seal_bid(bid)) == str(bid)

    def test_source_bid_is_not_mutated(self, north):
        bid = ContractBid(north, 110, Suit.DIAMONDS)
        seal_bid(bid)
        assert bid.player is north

    def test_ordering_still_works_between_sealed_contract_bids(self, north, east):
        low = seal_bid(ContractBid(north, 90, Suit.SPADES))
        high = seal_bid(ContractBid(east, 120, Suit.HEARTS))
        assert high > low
        assert not low > high

    def test_sealed_pass_is_still_a_bid(self, north):
        assert isinstance(seal_bid(PassBid(north)), Bid)
