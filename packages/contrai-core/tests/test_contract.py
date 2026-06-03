"""Tests for the Contract class.

Covers contract construction (direct + legacy), multiplier semantics,
Slam / Solo Slam vs numeric ``is_made`` / base-points logic, and
equality.

Note: ``is_made`` approximates Slam-family success as
``team_points >= 162`` (see ``Contract.is_made`` /
``Round.calculate_round_scores``). The strict per-player "bidder won
all 8 tricks personally" predicate for Solo Slam lives in ``Round``
(it requires per-player trick counts that ``Contract`` does not see).
"""

import pytest

from contrai_core import (
    BasePlayer,
    Contract,
    ContractBid,
    PassBid,
    Suit,
    Team,
)


@pytest.fixture
def north():
    return BasePlayer("North", "North")


@pytest.fixture
def south():
    return BasePlayer("South", "South")


@pytest.fixture
def team_ns(north, south):
    team = Team("North-South", [north, south])
    north.team = team
    south.team = team
    return team


@pytest.fixture
def numeric_contract(north, team_ns):
    return Contract(ContractBid(north, 100, Suit.SPADES))


@pytest.fixture
def slam_contract(north, team_ns):
    return Contract(ContractBid(north, "Slam", Suit.HEARTS))


@pytest.fixture
def solo_slam_contract(north, team_ns):
    return Contract(ContractBid(north, "SoloSlam", Suit.HEARTS))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestContractConstruction:
    def test_wraps_contract_bid(self, north, team_ns):
        bid = ContractBid(north, 110, Suit.DIAMONDS)
        contract = Contract(bid)
        assert contract.contract_bid is bid
        assert contract.player is north
        assert contract.team is team_ns
        assert contract.value == 110
        assert contract.suit is Suit.DIAMONDS
        assert contract.double is False
        assert contract.redouble is False

    def test_construction_with_double(self, north, team_ns):
        contract = Contract(ContractBid(north, 80, Suit.CLUBS), double=True)
        assert contract.double is True
        assert contract.redouble is False

    def test_construction_with_redouble(self, north, team_ns):
        contract = Contract(
            ContractBid(north, 80, Suit.CLUBS), double=True, redouble=True
        )
        assert contract.double is True
        assert contract.redouble is True

    def test_double_redouble_players_default_to_none(self, north, team_ns):
        contract = Contract(ContractBid(north, 80, Suit.CLUBS))
        assert contract.double_player is None
        assert contract.redouble_player is None

    def test_construction_records_double_and_redouble_players(
        self, north, south, team_ns
    ):
        contract = Contract(
            ContractBid(north, 100, Suit.HEARTS),
            double=True,
            redouble=True,
            double_player=south,
            redouble_player=north,
        )
        assert contract.double_player is south
        assert contract.redouble_player is north


# ---------------------------------------------------------------------------
# Multiplier
# ---------------------------------------------------------------------------


class TestContractMultiplier:
    def test_normal_multiplier(self, north, team_ns):
        contract = Contract(ContractBid(north, 80, Suit.SPADES))
        assert contract.get_multiplier() == 1

    def test_double_multiplier(self, north, team_ns):
        contract = Contract(ContractBid(north, 80, Suit.SPADES), double=True)
        assert contract.get_multiplier() == 2

    def test_redouble_multiplier(self, north, team_ns):
        contract = Contract(
            ContractBid(north, 80, Suit.SPADES), double=True, redouble=True
        )
        assert contract.get_multiplier() == 4

    def test_redouble_dominates_double_flag(self, north, team_ns):
        # Current behaviour: redouble flag wins regardless of double flag state.
        contract = Contract(
            ContractBid(north, 80, Suit.SPADES), double=False, redouble=True
        )
        assert contract.get_multiplier() == 4


# ---------------------------------------------------------------------------
# is_made (incl. Slam-family edge case — see module docstring)
# ---------------------------------------------------------------------------


class TestContractIsMade:
    @pytest.mark.parametrize(
        "team_points,expected",
        [(79, False), (80, True), (81, True), (162, True)],
    )
    def test_numeric_threshold(self, numeric_contract, team_points, expected):
        # Override declared contract value (100) by re-fixturing locally.
        contract = Contract(
            ContractBid(numeric_contract.player, 80, Suit.SPADES)
        )
        assert contract.is_made(team_points) is expected

    def test_numeric_made_at_exact_threshold(self, numeric_contract):
        assert numeric_contract.is_made(100) is True
        assert numeric_contract.is_made(99) is False

    @pytest.mark.parametrize(
        "team_points,expected",
        [(0, False), (100, False), (161, False), (162, True), (200, True)],
    )
    def test_slam_threshold(self, slam_contract, team_points, expected):
        assert slam_contract.is_made(team_points) is expected

    @pytest.mark.parametrize(
        "team_points,expected",
        [(0, False), (100, False), (161, False), (162, True), (200, True)],
    )
    def test_solo_slam_threshold(
        self, solo_slam_contract, team_points, expected
    ):
        # Contract.is_made only checks team points — the per-player
        # "bidder won all 8 tricks personally" gate lives in Round.
        assert solo_slam_contract.is_made(team_points) is expected


# ---------------------------------------------------------------------------
# Slam helpers and team accessors
# ---------------------------------------------------------------------------


class TestContractSlamHelpers:
    def test_is_slam_true(self, slam_contract):
        assert slam_contract.is_slam() is True

    def test_is_slam_false_for_numeric(self, numeric_contract):
        assert numeric_contract.is_slam() is False

    def test_is_slam_false_for_solo_slam(self, solo_slam_contract):
        # is_slam is the narrow Slam-only predicate.
        assert solo_slam_contract.is_slam() is False

    def test_is_solo_slam_true(self, solo_slam_contract):
        assert solo_slam_contract.is_solo_slam() is True

    def test_is_solo_slam_false_for_slam(self, slam_contract):
        assert slam_contract.is_solo_slam() is False

    def test_is_solo_slam_false_for_numeric(self, numeric_contract):
        assert numeric_contract.is_solo_slam() is False

    def test_is_slam_family_true_for_slam(self, slam_contract):
        assert slam_contract.is_slam_family() is True

    def test_is_slam_family_true_for_solo_slam(self, solo_slam_contract):
        assert solo_slam_contract.is_slam_family() is True

    def test_is_slam_family_false_for_numeric(self, numeric_contract):
        assert numeric_contract.is_slam_family() is False

    def test_get_base_points_numeric(self, numeric_contract):
        assert numeric_contract.get_base_points() == 100

    def test_get_base_points_slam(self, slam_contract):
        # 250 = the contract base (auction precedence + half of the
        # at-risk amount). The other half is the flat card-pile
        # substitute returned by get_slam_card_substitute().
        assert slam_contract.get_base_points() == 250

    def test_get_base_points_solo_slam(self, solo_slam_contract):
        assert solo_slam_contract.get_base_points() == 500

    def test_get_slam_card_substitute_numeric_is_zero(self, numeric_contract):
        # Numeric contracts use the actual 162 of card points — no
        # substitute applies.
        assert numeric_contract.get_slam_card_substitute() == 0

    def test_get_slam_card_substitute_slam(self, slam_contract):
        # The 162 trick-pile is replaced by a flat 250 for Slam.
        assert slam_contract.get_slam_card_substitute() == 250

    def test_get_slam_card_substitute_solo_slam(self, solo_slam_contract):
        # Solo Slam: substitute is 500.
        assert solo_slam_contract.get_slam_card_substitute() == 500

    def test_at_risk_total_slam_normal(self, slam_contract):
        # The full at-risk amount for a Slam at normal multiplier:
        # (base + substitute) × 1 = 250 + 250 = 500.
        amount = (
            slam_contract.get_base_points()
            + slam_contract.get_slam_card_substitute()
        ) * slam_contract.get_multiplier()
        assert amount == 500

    def test_at_risk_total_solo_slam_doubled(self, north, team_ns):
        # Solo Slam doubled: (500 + 500) × 2 = 2000.
        contract = Contract(
            ContractBid(north, "SoloSlam", Suit.HEARTS), double=True
        )
        amount = (
            contract.get_base_points()
            + contract.get_slam_card_substitute()
        ) * contract.get_multiplier()
        assert amount == 2000


# ---------------------------------------------------------------------------
# Dunders
# ---------------------------------------------------------------------------


class TestContractDunders:
    def test_str_normal(self, north, team_ns):
        contract = Contract(ContractBid(north, 100, Suit.SPADES))
        assert "100" in str(contract)
        assert str(Suit.SPADES) in str(contract)
        assert north.name in str(contract)
        assert "Doubled" not in str(contract)
        assert "Redoubled" not in str(contract)

    def test_str_doubled(self, north, team_ns):
        contract = Contract(ContractBid(north, 100, Suit.SPADES), double=True)
        assert "Doubled" in str(contract)

    def test_str_redoubled(self, north, team_ns):
        contract = Contract(
            ContractBid(north, 100, Suit.SPADES), double=True, redouble=True
        )
        assert "Redoubled" in str(contract)

    def test_equality_same_bid_and_flags(self, north, team_ns):
        a = Contract(ContractBid(north, 100, Suit.SPADES))
        b = Contract(ContractBid(north, 100, Suit.SPADES))
        assert a == b

    def test_inequality_different_flags(self, north, team_ns):
        a = Contract(ContractBid(north, 100, Suit.SPADES))
        b = Contract(ContractBid(north, 100, Suit.SPADES), double=True)
        assert a != b

    def test_inequality_against_non_contract(self, numeric_contract, north):
        assert numeric_contract != PassBid(north)
        assert numeric_contract != "100 Spades"
