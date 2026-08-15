"""Tests for the Contract class.

Covers contract construction (direct + legacy), multiplier semantics,
Slam / Solo Slam vs numeric base-points logic, and equality — plus
``ObservedContract``, the seat-keyed projection ``Contract.observed()``
builds for the imperfect-information observation surface.

Note: whether a contract was *made* is decided in
``Round.calculate_round_scores`` — it requires trick counts (and, for
Solo Slam, per-player trick counts) that ``Contract`` does not see, so
``Contract`` deliberately exposes no ``is_made`` predicate.
"""

import dataclasses

import pytest

from contrai_core import (
    BasePlayer,
    Contract,
    ContractBid,
    InvalidContractError,
    PassBid,
    Position,
    SlamLevel,
    Suit,
    Team,
)


@pytest.fixture
def north():
    """North-seat declarer-to-be, initially without a team."""
    return BasePlayer("North", Position.NORTH)


@pytest.fixture
def south():
    """South-seat partner, initially without a team."""
    return BasePlayer("South", Position.SOUTH)


@pytest.fixture
def east():
    """East-seat opponent — the coincheur in the doubled-contract cases."""
    return BasePlayer("East", Position.EAST)


@pytest.fixture
def team_ns(north, south):
    """North-South team, wired onto both seats."""
    team = Team("North-South", [north, south])
    north.team = team
    south.team = team
    return team


@pytest.fixture
def numeric_contract(north):
    """100-in-Spades contract declared by North."""
    return Contract(ContractBid(north, 100, Suit.SPADES))


@pytest.fixture
def slam_contract(north):
    """Slam-in-Hearts contract declared by North."""
    return Contract(ContractBid(north, SlamLevel.SLAM, Suit.HEARTS))


@pytest.fixture
def solo_slam_contract(north):
    """Solo-Slam-in-Hearts contract declared by North."""
    return Contract(ContractBid(north, SlamLevel.SOLO_SLAM, Suit.HEARTS))


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

    def test_construction_with_double(self, north, south):
        # The doubled state is derived from the recorded doubler.
        contract = Contract(
            ContractBid(north, 80, Suit.CLUBS), double_player=south
        )
        assert contract.double is True
        assert contract.redouble is False

    def test_construction_with_redouble(self, north, south):
        contract = Contract(
            ContractBid(north, 80, Suit.CLUBS),
            double_player=south,
            redouble_player=north,
        )
        assert contract.double is True
        assert contract.redouble is True

    def test_double_redouble_players_default_to_none(self, north):
        contract = Contract(ContractBid(north, 80, Suit.CLUBS))
        assert contract.double_player is None
        assert contract.redouble_player is None

    def test_construction_records_double_and_redouble_players(
        self, north, south
    ):
        contract = Contract(
            ContractBid(north, 100, Suit.HEARTS),
            double_player=south,
            redouble_player=north,
        )
        assert contract.double_player is south
        assert contract.redouble_player is north

    def test_redouble_without_double_is_rejected(self, north, south):
        # A surcoinche can only stand on top of a coinche, so the
        # constructor refuses a redoubler with no doubler underneath it.
        with pytest.raises(InvalidContractError):
            Contract(
                ContractBid(north, 80, Suit.CLUBS), redouble_player=south
            )


# ---------------------------------------------------------------------------
# Multiplier
# ---------------------------------------------------------------------------


class TestContractMultiplier:
    def test_normal_multiplier(self, north):
        contract = Contract(ContractBid(north, 80, Suit.SPADES))
        assert contract.get_multiplier() == 1

    def test_double_multiplier(self, north, south):
        contract = Contract(
            ContractBid(north, 80, Suit.SPADES), double_player=south
        )
        assert contract.get_multiplier() == 2

    def test_redouble_multiplier(self, north, south):
        contract = Contract(
            ContractBid(north, 80, Suit.SPADES),
            double_player=south,
            redouble_player=north,
        )
        assert contract.get_multiplier() == 4


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
        # substitute + base × 1 = 250 + 250 = 500.
        amount = (
            slam_contract.get_slam_card_substitute()
            + slam_contract.get_base_points() * slam_contract.get_multiplier()
        )
        assert amount == 500

    def test_at_risk_total_solo_slam_doubled(self, north, south):
        # Solo Slam doubled: only the announced half is multiplied, so
        # 500 + 500 × 2 = 1500 — not (500 + 500) × 2.
        contract = Contract(
            ContractBid(north, SlamLevel.SOLO_SLAM, Suit.HEARTS), double_player=south
        )
        amount = (
            contract.get_slam_card_substitute()
            + contract.get_base_points() * contract.get_multiplier()
        )
        assert amount == 1500


# ---------------------------------------------------------------------------
# Dunders
# ---------------------------------------------------------------------------


class TestContractDunders:
    def test_str_normal(self, north):
        contract = Contract(ContractBid(north, 100, Suit.SPADES))
        assert "100" in str(contract)
        # Literal, not str(Suit.SPADES): comparing the rendering against
        # itself would pass no matter what __str__ returned.
        assert "Spades" in str(contract)
        assert north.name in str(contract)
        assert "Doubled" not in str(contract)
        assert "Redoubled" not in str(contract)

    def test_str_doubled(self, north, south):
        contract = Contract(
            ContractBid(north, 100, Suit.SPADES), double_player=south
        )
        assert "Doubled" in str(contract)

    def test_str_redoubled(self, north, south):
        contract = Contract(
            ContractBid(north, 100, Suit.SPADES),
            double_player=south,
            redouble_player=north,
        )
        assert "Redoubled" in str(contract)

    def test_equality_same_bid_and_flags(self, north):
        a = Contract(ContractBid(north, 100, Suit.SPADES))
        b = Contract(ContractBid(north, 100, Suit.SPADES))
        assert a == b

    def test_inequality_different_flags(self, north, south):
        a = Contract(ContractBid(north, 100, Suit.SPADES))
        b = Contract(ContractBid(north, 100, Suit.SPADES), double_player=south)
        assert a != b

    def test_inequality_against_non_contract(self, numeric_contract, north):
        assert numeric_contract != PassBid(north)
        assert numeric_contract != "100 Spades"


# ---------------------------------------------------------------------------
# ObservedContract — the seat-keyed projection
# ---------------------------------------------------------------------------


class TestObservedContract:
    """``Contract.observed()`` must keep the terms and drop the people.

    The projection is what a ``PlayObservation`` carries, so it has to
    answer every contract question a card-play strategy asks while
    holding no reference through which another seat's hand is reachable.
    """

    def test_declarer_is_the_bidders_seat(self, numeric_contract):
        assert numeric_contract.observed().declarer is Position.NORTH

    def test_terms_are_preserved(self, numeric_contract):
        observed = numeric_contract.observed()
        assert observed.value == 100
        assert observed.suit is Suit.SPADES

    def test_undoubled_contract_has_no_callers(self, numeric_contract):
        observed = numeric_contract.observed()
        assert observed.doubled_by is None
        assert observed.redoubled_by is None
        assert observed.double is False
        assert observed.redouble is False

    def test_doubler_and_redoubler_become_seats(self, north, south, east):
        contract = Contract(
            ContractBid(north, 110, Suit.CLUBS),
            double_player=east,
            redouble_player=south,
        )
        observed = contract.observed()
        assert observed.doubled_by is Position.EAST
        assert observed.redoubled_by is Position.SOUTH
        assert observed.double is True
        assert observed.redouble is True

    @pytest.mark.parametrize(
        "doubler, redoubler, expected",
        [(None, None, 1), ("east", None, 2), ("east", "south", 4)],
    )
    def test_multiplier_matches_the_source_contract(
        self, north, south, east, doubler, redoubler, expected
    ):
        seats = {"east": east, "south": south, None: None}
        contract = Contract(
            ContractBid(north, 100, Suit.SPADES),
            double_player=seats[doubler],
            redouble_player=seats[redoubler],
        )
        observed = contract.observed()
        assert observed.get_multiplier() == contract.get_multiplier() == expected

    def test_slam_predicates_match_the_source_contract(
        self, numeric_contract, slam_contract, solo_slam_contract
    ):
        for contract in (numeric_contract, slam_contract, solo_slam_contract):
            observed = contract.observed()
            assert observed.is_slam() == contract.is_slam()
            assert observed.is_solo_slam() == contract.is_solo_slam()
            assert observed.is_slam_family() == contract.is_slam_family()

    def test_slam_value_survives_as_the_enum_member(self, slam_contract):
        assert slam_contract.observed().value is SlamLevel.SLAM

    def test_no_live_reference_is_carried(self, north, south, east, team_ns):
        contract = Contract(
            ContractBid(north, 110, Suit.CLUBS),
            double_player=east,
            redouble_player=south,
        )
        observed = contract.observed()
        for gone in ("player", "team", "double_player", "redouble_player"):
            assert not hasattr(observed, gone)
        assert not any(
            isinstance(getattr(observed, f), (BasePlayer, Team))
            for f in observed.__dataclass_fields__
        )

    def test_scoring_helpers_are_not_mirrored(self, numeric_contract):
        # Scoring reads the authoritative Contract off the round, never
        # an observation — mirroring these here would be dead weight.
        observed = numeric_contract.observed()
        assert not hasattr(observed, "get_base_points")
        assert not hasattr(observed, "get_slam_card_substitute")

    def test_is_frozen(self, numeric_contract):
        observed = numeric_contract.observed()
        with pytest.raises(dataclasses.FrozenInstanceError):
            observed.declarer = Position.WEST

    def test_compares_by_value(self, north):
        a = Contract(ContractBid(north, 100, Suit.SPADES)).observed()
        b = Contract(ContractBid(north, 100, Suit.SPADES)).observed()
        c = Contract(ContractBid(north, 110, Suit.SPADES)).observed()
        assert a == b
        assert a != c

    def test_str_names_the_declaring_seat(self, north, east):
        contract = Contract(
            ContractBid(north, 100, Suit.SPADES), double_player=east
        )
        assert str(contract.observed()) == "100 Spades by North (Doubled)"
