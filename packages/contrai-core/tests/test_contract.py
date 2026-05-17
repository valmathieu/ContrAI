"""Tests for the Contract class.

Covers contract construction (direct + legacy), multiplier semantics,
Capot vs numeric is_made / base-points logic, and equality.

Note: ``is_made`` currently approximates Capot success as
``points >= 162`` (see ``Contract.is_made`` / ``Round.calculate_round_scores``).
Per contree-domain.md §7.2 a Capot requires winning *all 8 tricks* —
with the Belote 20-point bonus in play those two conditions diverge.
The tests below pin current behaviour; a strict "8-trick" check is
tracked as future work.
"""

import pytest

from contrai_core.bid import ContractBid, PassBid
from contrai_core.contract import Contract
from contrai_core.player import BasePlayer
from contrai_core.team import Team
from contrai_core.types import Suit


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
def capot_contract(north, team_ns):
    return Contract(ContractBid(north, "Capot", Suit.HEARTS))


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

    def test_from_legacy_matches_direct_construction(self, north, team_ns):
        direct = Contract(ContractBid(north, 90, Suit.HEARTS))
        legacy = Contract.from_legacy(north, 90, Suit.HEARTS)
        assert direct == legacy

    def test_from_legacy_propagates_flags(self, north, team_ns):
        legacy = Contract.from_legacy(
            north, 100, Suit.SPADES, double=True, redouble=True
        )
        assert legacy.double is True
        assert legacy.redouble is True


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
# is_made (incl. Capot edge case — see module docstring)
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
    def test_capot_threshold(self, capot_contract, team_points, expected):
        # TODO: strict rule is "all 8 tricks". With Belote (+20) a team could
        # in principle reach 162 without taking all tricks — see module
        # docstring and contree-domain.md §7.2.
        assert capot_contract.is_made(team_points) is expected


# ---------------------------------------------------------------------------
# Capot helpers and team accessors
# ---------------------------------------------------------------------------


class TestContractCapotHelpers:
    def test_is_capot_true(self, capot_contract):
        assert capot_contract.is_capot() is True

    def test_is_capot_false_for_numeric(self, numeric_contract):
        assert numeric_contract.is_capot() is False

    def test_get_base_points_numeric(self, numeric_contract):
        assert numeric_contract.get_base_points() == 100

    def test_get_base_points_capot(self, capot_contract):
        assert capot_contract.get_base_points() == 250


class TestContractTeamAccessors:
    def test_get_attacking_team(self, numeric_contract, team_ns):
        assert numeric_contract.get_attacking_team() is team_ns

    def test_get_defending_team_returns_none_today(self, numeric_contract):
        # Documented placeholder in contract.py — defending team is computed
        # at game level today, not on the Contract itself.
        assert numeric_contract.get_defending_team() is None


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
