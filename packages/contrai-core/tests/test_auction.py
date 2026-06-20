"""Tests for the :class:`Auction` rule oracle and state machine.

The auction owns the chronological bid history and answers the
"what is legal now?" / "is bidding over?" / "what contract did this
produce?" questions previously scattered across ``Bid.is_valid_after``
and ``BidValidator``. These tests cover:

- :meth:`Auction.is_legal` for each :class:`Bid` subtype, including
  the Slam / Solo Slam precedence rules and the
  Double-freezes-the-auction rule.
- :meth:`Auction.legal_actions` enumeration shape and the "only Pass
  is legal" cases (partner just doubled / redoubled) that drive the
  engine's auto-pass shortcut.
- :meth:`Auction.apply` (happy path + ``IllegalBidError`` on illegal
  bids — *no* silent downgrade to Pass).
- :meth:`Auction.is_terminal` and :meth:`Auction.contract` covering
  both end conditions.
- The state-query properties: ``last_contract_bid``, ``has_double``,
  ``has_redouble``, ``consecutive_passes``, and ``partner_bid``.

Every auction sequence in this module respects the engine's
anticlockwise speaking cycle ``N → W → S → E`` (each player speaks at
their turn, starting from any of the four seats). When the rule under
test would otherwise put a player out of turn, the sequence is
extended with the appropriate intervening :class:`PassBid`\\ s, or the
"checked" player is reassigned to the next legitimate speaker — the
rule oracle is pure but the histories we feed it must be reachable
from real play.

Fixture parameters are listed in cycle order starting from N
(``north, west, south, east`` for any subset, in that order), with
``four_players`` appended when team identity is needed (Double /
Redouble legality, ``partner_bid``).
"""

import pytest

from contrai_core import (
    Auction,
    BasePlayer,
    ContractBid,
    DoubleBid,
    IllegalBidError,
    PassBid,
    RedoubleBid,
    SlamLevel,
    Suit,
    Team,
)


# ---------------------------------------------------------------------------
# Fixtures: four real players + their N-S / E-W teams.
# Auction rules compare team identity for Double / Redouble legality, so
# we want real Team/BasePlayer instances rather than mocks.
# ---------------------------------------------------------------------------


@pytest.fixture
def north():
    return BasePlayer("North", "North")


@pytest.fixture
def west():
    return BasePlayer("West", "West")


@pytest.fixture
def south():
    return BasePlayer("South", "South")


@pytest.fixture
def east():
    return BasePlayer("East", "East")


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
def four_players(team_ns, team_ew, north, west, south, east):
    """Force all four-team fixtures so every player has a team assigned.

    The return value is rarely unpacked — most tests pull the seat
    fixtures they need directly and depend on ``four_players`` only
    for the side effect of wiring teams onto every ``BasePlayer``.
    """
    return north, west, south, east


# ---------------------------------------------------------------------------
# Construction & basic shape
# ---------------------------------------------------------------------------


class TestConstruction:
    """Auction is a frozen dataclass with a tuple of bids."""

    def test_empty_default(self):
        auction = Auction()
        assert auction.bids == ()
        assert auction.consecutive_passes == 0
        assert auction.last_contract_bid is None

    def test_empty_classmethod(self):
        assert Auction.empty() == Auction()

    def test_is_frozen(self, north):
        auction = Auction((PassBid(north),))
        # Frozen dataclass forbids attribute reassignment.
        with pytest.raises(Exception):
            auction.bids = ()

    def test_equality_by_bids(self, north):
        a = Auction((PassBid(north),))
        b = Auction((PassBid(north),))
        assert a == b
        # Different sequences differ.
        assert a != Auction(())


# ---------------------------------------------------------------------------
# PassBid is always legal
# ---------------------------------------------------------------------------


class TestPassLegality:
    """Pass is always legal regardless of history."""

    def test_pass_legal_on_empty(self, north):
        assert Auction().is_legal(PassBid(north)) is True

    def test_pass_legal_after_contract(self, north, east):
        # East starts; the cycle N→W→S→E puts N next after E.
        auction = Auction((ContractBid(east, 90, Suit.HEARTS),))
        assert auction.is_legal(PassBid(north)) is True

    def test_pass_legal_after_double(self, north, west, south, east):
        # East starts with a contract, N (next in cycle) doubles, W passes;
        # S is the natural next speaker.
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(north),
                PassBid(west),
            ),
        )
        assert auction.is_legal(PassBid(south)) is True

    def test_pass_legal_at_terminal(self, north, west, south, east):
        # Three passes after a non-pass — auction is terminal, but the
        # legality oracle still answers True for Pass (terminality is
        # a separate concern). N starts, W/S/E pass; N is up again.
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                PassBid(east),
            ),
        )
        assert auction.is_legal(PassBid(north)) is True


# ---------------------------------------------------------------------------
# ContractBid precedence
# ---------------------------------------------------------------------------


class TestContractBidPrecedence:
    """Precedence + auction-freeze rules."""

    def test_first_contract_always_legal(self, north):
        assert Auction().is_legal(ContractBid(north, 80, Suit.SPADES)) is True

    def test_higher_numeric_legal(self, north, east):
        auction = Auction((ContractBid(east, 90, Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 100, Suit.SPADES)) is True

    def test_lower_numeric_illegal(self, north, east):
        auction = Auction((ContractBid(east, 110, Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 100, Suit.SPADES)) is False

    def test_equal_numeric_illegal(self, north, east):
        auction = Auction((ContractBid(east, 100, Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 100, Suit.SPADES)) is False

    def test_slam_over_any_numeric_legal(self, north, west, east):
        for value in (80, 90, 100, 130, 160):
            auction = Auction(
                (ContractBid(east, value, Suit.HEARTS), PassBid(north)),
            )
            assert (
                auction.is_legal(ContractBid(west, SlamLevel.SLAM, Suit.SPADES))
                is True
            )

    def test_solo_slam_over_any_numeric_legal(
        self, north, west, south, east
    ):
        for value in (80, 90, 100, 130, 160):
            auction = Auction(
                (
                    ContractBid(east, value, Suit.HEARTS),
                    PassBid(north),
                    PassBid(west),
                ),
            )
            assert (
                auction.is_legal(ContractBid(south, SlamLevel.SOLO_SLAM, Suit.SPADES))
                is True
            )

    def test_numeric_over_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, SlamLevel.SLAM, Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 160, Suit.SPADES)) is False

    def test_numeric_over_solo_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, SlamLevel.SOLO_SLAM, Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 160, Suit.SPADES)) is False

    def test_slam_over_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, SlamLevel.SLAM, Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, SlamLevel.SLAM, Suit.SPADES)) is False
        )

    def test_solo_slam_after_slam_illegal(self, north, east):
        """Asymmetric block: SoloSlam (1000) outranks Slam (500), but
        once a Slam is on the table the auction is closed to further
        contract bids — including the otherwise-higher SoloSlam."""
        auction = Auction((ContractBid(east, SlamLevel.SLAM, Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, SlamLevel.SOLO_SLAM, Suit.SPADES))
            is False
        )

    def test_slam_after_solo_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, SlamLevel.SOLO_SLAM, Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, SlamLevel.SLAM, Suit.SPADES)) is False
        )

    def test_solo_slam_over_solo_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, SlamLevel.SOLO_SLAM, Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, SlamLevel.SOLO_SLAM, Suit.SPADES))
            is False
        )

    def test_passes_do_not_change_precedence(self, north, west, east):
        # E starts with 100; N passes — W is the natural next speaker.
        auction = Auction(
            (ContractBid(east, 100, Suit.HEARTS), PassBid(north)),
        )
        assert auction.is_legal(ContractBid(west, 110, Suit.HEARTS)) is True
        assert auction.is_legal(ContractBid(west, 100, Suit.HEARTS)) is False

    def test_double_freezes_auction(
        self, north, west, south, east, four_players
    ):
        # E starts, intervening passes from N and W, then S doubles —
        # E is the natural next speaker after the double.
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                PassBid(north),
                PassBid(west),
                DoubleBid(south),
            ),
        )
        # No new numeric bid can reopen a frozen auction.
        assert auction.is_legal(ContractBid(east, 110, Suit.HEARTS)) is False
        # Even Slam / SoloSlam can't.
        assert (
            auction.is_legal(ContractBid(east, SlamLevel.SLAM, Suit.SPADES))
            is False
        )
        assert (
            auction.is_legal(ContractBid(east, SlamLevel.SOLO_SLAM, Suit.SPADES))
            is False
        )

    def test_redouble_also_freezes_auction(
        self, north, west, south, east, four_players
    ):
        # E contracts, N/W pass, S doubles, E redoubles — N is next.
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                PassBid(north),
                PassBid(west),
                DoubleBid(south),
                RedoubleBid(east),
            ),
        )
        assert auction.is_legal(ContractBid(north, 130, Suit.SPADES)) is False

    def test_passes_after_double_still_freeze_auction(
        self, north, west, south, east, four_players
    ):
        # After E contracts, N/W pass, S doubles, then E passes — N is
        # the next speaker and the auction is still frozen.
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                PassBid(north),
                PassBid(west),
                DoubleBid(south),
                PassBid(east),
            ),
        )
        assert auction.is_legal(ContractBid(north, 110, Suit.HEARTS)) is False


# ---------------------------------------------------------------------------
# Contract legality is suit-independent
# ---------------------------------------------------------------------------


class TestContractLegalitySuitIndependence:
    """Contract legality is a function of value only — never of suit.

    The rule helper has been split into a suit-agnostic
    :meth:`Auction._is_contract_value_legal` precisely so callers (most
    importantly :meth:`Auction.legal_actions`) don't probe the same
    question six times per value. These tests pin that invariant: the
    answer for any given ``value`` must be identical across every
    :class:`Suit`.
    """

    @pytest.mark.parametrize("value", ContractBid.VALID_VALUES)
    def test_value_legal_iff_every_suit_legal_on_empty(self, north, value):
        auction = Auction()
        value_answer = auction._is_contract_value_legal(value)
        for suit in ContractBid.VALID_SUITS:
            assert (
                auction._is_contract_legal(ContractBid(north, value, suit))
                is value_answer
            )

    @pytest.mark.parametrize("value", ContractBid.VALID_VALUES)
    def test_value_legal_iff_every_suit_legal_after_contract(
        self, north, east, value
    ):
        auction = Auction((ContractBid(east, 100, Suit.HEARTS),))
        value_answer = auction._is_contract_value_legal(value)
        for suit in ContractBid.VALID_SUITS:
            assert (
                auction._is_contract_legal(ContractBid(north, value, suit))
                is value_answer
            )

    def test_value_legal_false_when_frozen_by_double(
        self, north, west, south, east, four_players
    ):
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                PassBid(north),
                PassBid(west),
                DoubleBid(south),
            ),
        )
        # Freeze blocks every value, including the Slam family.
        for value in ContractBid.VALID_VALUES:
            assert auction._is_contract_value_legal(value) is False

    def test_value_legal_false_when_slam_announced(self, east):
        auction = Auction((ContractBid(east, SlamLevel.SLAM, Suit.HEARTS),))
        # Slam closes the auction to every contract value (including SoloSlam).
        for value in ContractBid.VALID_VALUES:
            assert auction._is_contract_value_legal(value) is False


# ---------------------------------------------------------------------------
# Monotonicity invariant — load-bearing for legal_actions's short-circuit
# ---------------------------------------------------------------------------


class TestLegalActionsMonotonicity:
    """``Auction._is_contract_value_legal`` is monotonic in
    :attr:`ContractBid.VALID_VALUES` iteration order.

    Concretely: once some value in the list clears as legal, every
    subsequent value in the list must also clear. This is what lets
    :meth:`Auction.legal_actions` stop probing the moment it finds the
    first legal value and fan the remainder of the list out across
    every suit without further checks. If a future rule ever blocks a
    *specific* high value (a hypothetical "you cannot bid 170 after a
    160-doubled contract", etc.), this invariant breaks and the
    short-circuit must be re-thought.
    """

    @pytest.fixture
    def histories(
        self, north, west, south, east, four_players,
    ) -> list[Auction]:
        """Auction histories covering every shape the rule helper sees:
        empty, after a low numeric, after the numeric ceiling, after a
        Slam, after a SoloSlam, and after a freeze by Double."""
        cb_low = ContractBid(east, 100, Suit.HEARTS)
        cb_ceiling = ContractBid(east, 160, Suit.HEARTS)
        cb_slam = ContractBid(east, SlamLevel.SLAM, Suit.HEARTS)
        cb_solo = ContractBid(east, SlamLevel.SOLO_SLAM, Suit.HEARTS)
        return [
            Auction(),
            Auction((cb_low,)),
            Auction((cb_ceiling,)),
            Auction((cb_slam,)),
            Auction((cb_solo,)),
            Auction(
                (cb_low, PassBid(north), PassBid(west), DoubleBid(south)),
            ),
        ]

    def test_value_legality_is_monotonic_in_iteration_order(self, histories):
        for auction in histories:
            seen_legal = False
            for value in ContractBid.VALID_VALUES:
                is_legal = auction._is_contract_value_legal(value)
                if seen_legal:
                    assert is_legal, (
                        f"Monotonicity broken for {auction.bids!r}: "
                        f"value={value!r} is illegal after an earlier "
                        f"value in VALID_VALUES was legal. "
                        f"legal_actions's short-circuit assumes monotonicity."
                    )
                elif is_legal:
                    seen_legal = True

    def test_short_circuit_matches_per_value_probe(
        self, north, west, south, east, four_players,
    ):
        """The short-circuited :meth:`Auction.legal_actions` produces the
        same set of :class:`ContractBid` actions as a full per-value probe
        would. Drives the equivalence the optimisation relies on across
        every history shape exercised by :attr:`histories`."""
        scenarios = [
            Auction(),
            Auction((ContractBid(east, 100, Suit.HEARTS),)),
            Auction((ContractBid(east, 160, Suit.HEARTS),)),
            Auction((ContractBid(east, SlamLevel.SLAM, Suit.HEARTS),)),
            Auction((ContractBid(east, SlamLevel.SOLO_SLAM, Suit.HEARTS),)),
            Auction(
                (
                    ContractBid(east, 100, Suit.HEARTS),
                    PassBid(north),
                    PassBid(west),
                    DoubleBid(south),
                ),
            ),
        ]
        for auction in scenarios:
            from_short_circuit = {
                a for a in auction.legal_actions(north)
                if isinstance(a, ContractBid)
            }
            from_full_probe = {
                ContractBid(north, value, suit)
                for value in ContractBid.VALID_VALUES
                for suit in ContractBid.VALID_SUITS
                if auction._is_contract_value_legal(value)
            }
            assert from_short_circuit == from_full_probe, (
                f"Short-circuit diverged from per-value probe for "
                f"{auction.bids!r}"
            )


# ---------------------------------------------------------------------------
# DoubleBid legality
# ---------------------------------------------------------------------------


class TestDoubleLegality:
    def test_illegal_on_empty(self, east, four_players):
        assert Auction().is_legal(DoubleBid(east)) is False

    def test_legal_against_opponent_contract(self, north, east, four_players):
        # E starts; N (next in cycle, opposing team) may immediately
        # double the contract.
        auction = Auction((ContractBid(east, 100, Suit.SPADES),))
        assert auction.is_legal(DoubleBid(north)) is True

    def test_illegal_against_own_contract(
        self, north, west, south, four_players
    ):
        # N contracts, W passes; S (N's partner) cannot double their
        # own team's contract.
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), PassBid(west)),
        )
        assert auction.is_legal(DoubleBid(south)) is False

    def test_illegal_if_already_doubled(
        self, north, west, south, east, four_players
    ):
        # N contracts, W (opposing) doubles, S passes — E is up but
        # cannot re-double an already-doubled contract.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(west),
                PassBid(south),
            ),
        )
        assert auction.is_legal(DoubleBid(east)) is False

    def test_illegal_after_redouble(
        self, north, west, south, east, four_players
    ):
        # N contracts, W/S pass, E doubles, N redoubles — W is next
        # and cannot re-double after a redouble.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.is_legal(DoubleBid(west)) is False

    def test_legal_after_passes_since_contract(
        self, north, west, south, east, four_players
    ):
        """Passes between the contract bid and the Coinche do not close
        the window — opposing players may still come back and Double
        until the auction terminates on 3 consecutive passes.
        """
        # N contracts; W and S pass; E (opposing team) may still
        # double from the next legitimate seat.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
            ),
        )
        assert auction.is_legal(DoubleBid(east)) is True

    def test_legal_against_opponent_slam(self, north, west, four_players):
        """Slam closes the auction to numeric / Slam-family bids but
        coinche must remain available — opponents can still Double."""
        # N announces Slam; W (next in cycle, opposing team) doubles.
        auction = Auction((ContractBid(north, SlamLevel.SLAM, Suit.SPADES),))
        assert auction.is_legal(DoubleBid(west)) is True

    def test_legal_against_opponent_solo_slam(
        self, north, west, four_players
    ):
        auction = Auction((ContractBid(north, SlamLevel.SOLO_SLAM, Suit.SPADES),))
        assert auction.is_legal(DoubleBid(west)) is True


# ---------------------------------------------------------------------------
# RedoubleBid legality
# ---------------------------------------------------------------------------


class TestRedoubleLegality:
    def test_illegal_on_empty(self, north, four_players):
        assert Auction().is_legal(RedoubleBid(north)) is False

    def test_illegal_without_prior_double(self, north, west, four_players):
        # N contracts; W is next. Without a prior Double, nobody may
        # redouble — including W (opposing team).
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        assert auction.is_legal(RedoubleBid(west)) is False

    def test_legal_for_contracting_team(
        self, north, west, south, east, four_players
    ):
        # N contracts; W/S pass; E doubles. N is the natural next
        # speaker and may redouble; partner S may too (rule oracle is
        # pure, so we exercise both contracting-team seats).
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        assert auction.is_legal(RedoubleBid(north)) is True
        assert auction.is_legal(RedoubleBid(south)) is True

    def test_illegal_for_opposing_team(
        self, north, west, south, east, four_players
    ):
        # Same shape — but W (doubling team) cannot redouble.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        assert auction.is_legal(RedoubleBid(west)) is False

    def test_illegal_if_already_redoubled(
        self, north, west, south, east, four_players
    ):
        # N contracts, W/S pass, E doubles, N redoubles. Both the
        # contracting seat (S, via rule oracle) and the natural next
        # speaker (W, opposing team) are blocked from redoubling
        # again, but the "already redoubled" rule is the clean test
        # against the contracting partner.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.is_legal(RedoubleBid(south)) is False

    def test_legal_after_passes_since_double(
        self, north, west, south, east, four_players
    ):
        """Symmetric with the Double window: intervening passes after
        the Coinche do not close the Surcoinche window. The contracting
        team may still come back and Redouble until the auction's
        3-consecutive-passes terminator fires.
        """
        # N contracts, W/S pass, E doubles, N passes, W passes — S
        # (contracting team) is the next legitimate speaker and may
        # still redouble.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
                PassBid(north),
                PassBid(west),
            ),
        )
        assert auction.is_legal(RedoubleBid(south)) is True


# ---------------------------------------------------------------------------
# legal_actions enumeration
# ---------------------------------------------------------------------------


class TestLegalActions:
    """Shape of the enumerated legal-actions set."""

    def test_empty_auction_includes_pass_and_all_contracts(self, north):
        actions = Auction().legal_actions(north)
        # Always starts with the Pass action.
        assert isinstance(actions[0], PassBid)
        # 13 values × 6 suits = 78 ContractBids legal at start, plus the Pass.
        contracts = [a for a in actions if isinstance(a, ContractBid)]
        assert len(contracts) == 13 * 6
        # No Double / Redouble before there's a contract to challenge.
        assert not any(isinstance(a, DoubleBid) for a in actions)
        assert not any(isinstance(a, RedoubleBid) for a in actions)

    def test_includes_double_after_opponent_contract(
        self, north, west, south, east, four_players
    ):
        # N contracts; W/S pass; E (opposing team) is next and gets
        # both Double and the legal numeric raises in its action set.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
            ),
        )
        actions = auction.legal_actions(east)
        assert any(isinstance(a, DoubleBid) for a in actions)
        contract_raises = [
            a for a in actions
            if isinstance(a, ContractBid)
            and not isinstance(a.value, SlamLevel)
            and a.value > 100
        ]
        assert contract_raises  # at least one higher-value contract exists

    def test_includes_redouble_after_being_doubled(
        self, north, west, south, east, four_players
    ):
        # N contracts, W/S pass, E doubles — N is next and gets
        # Redouble in its actions; partner S does too (pure oracle).
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        actions = auction.legal_actions(north)
        assert any(isinstance(a, RedoubleBid) for a in actions)
        partner_actions = auction.legal_actions(south)
        assert any(isinstance(a, RedoubleBid) for a in partner_actions)

    def test_only_pass_when_partner_doubled(
        self, north, west, south, east, four_players
    ):
        """E (opponent) contracts; N (S's partner) doubles; W passes;
        S is the natural next speaker and the only legal action is
        Pass — drives the engine's auto-pass shortcut."""
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(north),
                PassBid(west),
            ),
        )
        actions = auction.legal_actions(south)
        assert len(actions) == 1
        assert isinstance(actions[0], PassBid)

    def test_only_pass_when_partner_redoubled(
        self, north, west, south, east, four_players
    ):
        """N contracts, W doubles, S (N's partner) redoubles, E passes
        — N is up next and the only legal action is Pass."""
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(west),
                RedoubleBid(south),
                PassBid(east),
            ),
        )
        actions = auction.legal_actions(north)
        assert len(actions) == 1
        assert isinstance(actions[0], PassBid)

    def test_only_pass_when_partner_doubled_even_after_pass(
        self, north, west, south, east, four_players
    ):
        """The partner of a doubler still has only Pass available
        regardless of how many passes follow the Double: they cannot
        re-Double (their team already did), the auction is frozen so
        no contract is legal, and they're on the wrong team to
        Redouble."""
        # E contracts, N doubles, W passes — S (N's partner) is next.
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(north),
                PassBid(west),
            ),
        )
        actions = auction.legal_actions(south)
        assert len(actions) == 1
        assert isinstance(actions[0], PassBid)

    def test_redouble_still_available_after_intervening_pass(
        self, north, west, south, east, four_players
    ):
        """The contracting team may come back and Redouble even after
        passes intervene since the Double — only the auction's three-
        consecutive-passes terminator closes the window.
        """
        # E contracts, N doubles, W passes, S passes — E (contracting
        # team) is up after the two passes and Redouble must remain
        # on the table.
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(north),
                PassBid(west),
                PassBid(south),
            ),
        )
        actions = auction.legal_actions(east)
        assert any(isinstance(a, RedoubleBid) for a in actions)

    def test_double_still_available_after_intervening_passes(
        self, north, west, south, east, four_players,
    ):
        """Symmetric to the Redouble case: opposing players may come
        back and Coinche after intervening passes since the contract
        bid.
        """
        # N contracts, W passes, S passes — E (opposing team) is up
        # and Double must still be on the table.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
            ),
        )
        actions = auction.legal_actions(east)
        assert any(isinstance(a, DoubleBid) for a in actions)

    def test_no_auto_pass_when_opponent_doubles_contracting_team(
        self, north, west, south, east, four_players
    ):
        """Opponent (E) doubled the contract. N (contractor, NS team)
        is up next and must have BOTH Pass and Redouble — the engine
        must not auto-pass when the contracting team has the option
        to redouble.

        (Tests the contractor seat; partner S sits at the same
        contracting team and the pure rule oracle gives her the same
        Redouble option, exercised in
        :meth:`test_includes_redouble_after_being_doubled`.)
        """
        auction = Auction(
            (
                ContractBid(north, 100, Suit.HEARTS),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        actions = auction.legal_actions(north)
        assert len(actions) > 1
        assert any(isinstance(a, RedoubleBid) for a in actions)


# ---------------------------------------------------------------------------
# apply — happy path + illegal raises
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_returns_new_auction(self, north):
        auction = Auction()
        new = auction.apply(PassBid(north))
        assert new is not auction
        assert auction.bids == ()
        assert new.bids == (PassBid(north),)

    def test_apply_chains(self, north, east):
        # E opens with a pass; N (next in cycle) contracts.
        auction = (
            Auction()
            .apply(PassBid(east))
            .apply(ContractBid(north, 80, Suit.SPADES))
        )
        assert len(auction.bids) == 2
        assert auction.last_contract_bid == ContractBid(north, 80, Suit.SPADES)

    def test_apply_illegal_raises(self, north, east):
        auction = Auction((ContractBid(east, 100, Suit.HEARTS),))
        illegal = ContractBid(north, 90, Suit.SPADES)  # lower than 100
        with pytest.raises(IllegalBidError) as excinfo:
            auction.apply(illegal)
        # The exception carries the offending bid and prior history.
        assert excinfo.value.bid is illegal
        assert excinfo.value.bids == auction.bids

    def test_apply_double_against_own_team_raises(
        self, north, west, south, four_players
    ):
        # N contracts, W passes — S (N's partner) cannot double their
        # own team's contract.
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), PassBid(west)),
        )
        with pytest.raises(IllegalBidError):
            auction.apply(DoubleBid(south))


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------


class TestIsTerminal:
    def test_empty_not_terminal(self):
        assert Auction().is_terminal() is False

    def test_three_passes_after_contract_terminal(
        self, north, west, south, east
    ):
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                PassBid(east),
            ),
        )
        assert auction.is_terminal() is True

    def test_two_passes_after_contract_not_terminal(
        self, north, west, south
    ):
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(west),
                PassBid(south),
            ),
        )
        assert auction.is_terminal() is False

    def test_four_passes_terminal_all_pass_wipe(
        self, north, west, south, east
    ):
        auction = Auction(
            (
                PassBid(north),
                PassBid(west),
                PassBid(south),
                PassBid(east),
            ),
        )
        assert auction.is_terminal() is True

    def test_three_passes_no_contract_not_terminal(
        self, north, west, south
    ):
        auction = Auction(
            (
                PassBid(north),
                PassBid(west),
                PassBid(south),
            ),
        )
        # Need the full all-pass wipe (4 passes) before annulling.
        assert auction.is_terminal() is False

    def test_three_passes_after_double_terminal(
        self, north, west, south, east, four_players
    ):
        # N contracts, W doubles immediately (next in cycle, opposing
        # team); then 3 consecutive passes from S, E, N terminate.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(west),
                PassBid(south),
                PassBid(east),
                PassBid(north),
            ),
        )
        assert auction.is_terminal() is True


class TestContractMaterialisation:
    def test_no_contract_when_all_pass(self, north, west, south, east):
        auction = Auction(
            (
                PassBid(north),
                PassBid(west),
                PassBid(south),
                PassBid(east),
            ),
        )
        assert auction.contract() is None

    def test_simple_contract(
        self, north, west, south, east, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                PassBid(east),
            ),
        )
        contract = auction.contract()
        assert contract is not None
        assert contract.value == 80
        assert contract.suit == Suit.SPADES
        assert contract.player is north
        assert contract.double is False
        assert contract.redouble is False

    def test_doubled_contract(
        self, north, west, south, east, four_players
    ):
        # N contracts, W (next in cycle, opposing team) doubles; S/E/N
        # pass to terminate.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(west),
                PassBid(south),
                PassBid(east),
                PassBid(north),
            ),
        )
        contract = auction.contract()
        assert contract.double is True
        assert contract.redouble is False
        # The materialised contract carries the coincheur for the UI.
        assert contract.double_player is west
        assert contract.redouble_player is None

    def test_redoubled_contract(
        self, north, west, south, east, four_players
    ):
        # N contracts, W doubles, S (N's partner) redoubles, E/N/W
        # pass to terminate.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(west),
                RedoubleBid(south),
                PassBid(east),
                PassBid(north),
                PassBid(west),
            ),
        )
        contract = auction.contract()
        assert contract.double is True
        assert contract.redouble is True
        assert contract.double_player is west
        assert contract.redouble_player is south


# ---------------------------------------------------------------------------
# State-query properties
# ---------------------------------------------------------------------------


class TestStateProperties:
    def test_last_contract_bid_none_when_empty(self):
        assert Auction().last_contract_bid is None

    def test_last_contract_bid_none_when_only_passes(self, north):
        auction = Auction((PassBid(north),))
        assert auction.last_contract_bid is None

    def test_last_contract_bid_returns_most_recent(
        self, north, west, south, east
    ):
        # N opens at 80; W passes; S passes; E raises to 90 — the
        # state query should return E's bid.
        first = ContractBid(north, 80, Suit.SPADES)
        second = ContractBid(east, 90, Suit.HEARTS)
        auction = Auction(
            (first, PassBid(west), PassBid(south), second),
        )
        assert auction.last_contract_bid is second

    def test_has_double_true_after_double(
        self, north, west, south, east, four_players
    ):
        # N contracts, W/S pass, E (opposing) doubles.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        assert auction.has_double is True

    def test_has_double_false_when_no_double(self, north, four_players):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        assert auction.has_double is False

    def test_has_redouble_true_after_redouble(
        self, north, west, south, east, four_players
    ):
        # N contracts, W/S pass, E doubles, N redoubles.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.has_redouble is True

    def test_has_redouble_false_when_only_double(
        self, north, west, south, east, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        assert auction.has_redouble is False

    def test_double_player_is_the_doubler(
        self, north, west, south, east, four_players
    ):
        # N contracts, W/S pass, E (opposing) doubles → E is the doubler.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        assert auction.double_player is east

    def test_double_player_none_when_no_double(self, north, four_players):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        assert auction.double_player is None

    def test_redouble_player_is_the_redoubler(
        self, north, west, south, east, four_players
    ):
        # N contracts, E doubles, N redoubles → N is the redoubler while
        # E remains the doubler.
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.redouble_player is north
        assert auction.double_player is east

    def test_redouble_player_none_when_only_double(
        self, north, west, south, east, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(west),
                PassBid(south),
                DoubleBid(east),
            ),
        )
        assert auction.redouble_player is None

    def test_consecutive_passes_counts_from_tail(
        self, north, west, south
    ):
        # N starts at 100; subsequent passes come from W, then S
        # (cycle order). Trailing non-pass resets the counter.
        cb = ContractBid(north, 100, Suit.SPADES)
        assert Auction().consecutive_passes == 0
        assert Auction((cb,)).consecutive_passes == 0
        assert Auction((cb, PassBid(west))).consecutive_passes == 1
        assert (
            Auction(
                (cb, PassBid(west), PassBid(south)),
            ).consecutive_passes
            == 2
        )
        # Trailing non-pass resets — S raises after W's pass.
        assert (
            Auction(
                (cb, PassBid(west), ContractBid(south, 110, Suit.HEARTS)),
            ).consecutive_passes
            == 0
        )

    def test_partner_bid_returns_partner_last_non_pass(
        self, north, west, south, east, four_players
    ):
        # E opens at 80; N raises to 90; W passes; S passes — S looks
        # up the partner's last non-pass, which is N's 90 ♥.
        auction = Auction(
            (
                ContractBid(east, 80, Suit.SPADES),
                ContractBid(north, 90, Suit.HEARTS),
                PassBid(west),
                PassBid(south),
            ),
        )
        assert auction.partner_bid(south) == ContractBid(
            north, 90, Suit.HEARTS
        )

    def test_partner_bid_none_when_only_passes(
        self, north, south, four_players
    ):
        auction = Auction((PassBid(north),))
        assert auction.partner_bid(south) is None
