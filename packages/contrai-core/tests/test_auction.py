"""Tests for the :class:`Auction` rule oracle and state machine.

The auction owns the chronological bid history and answers the
"what is legal now?" / "is bidding over?" / "what contract did this
produce?" questions previously scattered across ``Bid.is_valid_after``
and ``BidValidator``. These tests cover:

- :meth:`Auction.is_legal` for each :class:`Bid` subtype, including
  the Slam / Solo Slam precedence rules from ``contree-domain.md §5.2``
  and the Double-freezes-the-auction rule from §5.3.
- :meth:`Auction.legal_actions` enumeration shape and the "only Pass
  is legal" cases (partner just doubled / redoubled) that drive the
  engine's auto-pass shortcut.
- :meth:`Auction.apply` (happy path + ``IllegalBidError`` on illegal
  bids — *no* silent downgrade to Pass).
- :meth:`Auction.is_terminal` and :meth:`Auction.contract` covering
  both end conditions from §5.4.
- The state-query properties: ``last_contract_bid``, ``has_double``,
  ``has_redouble``, ``consecutive_passes``, and ``partner_bid``.
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
    """Force all four-team fixtures so every player has a team assigned."""
    return north, east, south, west


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

    def test_is_frozen(self, north, four_players):
        auction = Auction((PassBid(north),))
        # Frozen dataclass forbids attribute reassignment.
        with pytest.raises(Exception):
            auction.bids = ()

    def test_equality_by_bids(self, north, four_players):
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

    def test_pass_legal_after_contract(self, north, east, four_players):
        auction = Auction((ContractBid(east, 90, Suit.HEARTS),))
        assert auction.is_legal(PassBid(north)) is True

    def test_pass_legal_after_double(self, north, east, south, four_players):
        auction = Auction(
            (ContractBid(east, 100, Suit.HEARTS), DoubleBid(north)),
        )
        assert auction.is_legal(PassBid(south)) is True

    def test_pass_legal_at_terminal(self, north, east, south, west, four_players):
        # Three passes after a non-pass — auction is terminal, but the
        # legality oracle still answers True for Pass (terminality is
        # a separate concern).
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(east),
                PassBid(south),
                PassBid(west),
            ),
        )
        assert auction.is_legal(PassBid(north)) is True


# ---------------------------------------------------------------------------
# ContractBid precedence (contree-domain.md §5.2 + §5.3)
# ---------------------------------------------------------------------------


class TestContractBidPrecedence:
    """Precedence + auction-freeze rules from contree-domain.md."""

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

    def test_slam_over_any_numeric_legal(self, north, east):
        for value in (80, 90, 100, 130, 160):
            auction = Auction((ContractBid(east, value, Suit.HEARTS),))
            assert (
                auction.is_legal(ContractBid(north, "Slam", Suit.SPADES))
                is True
            )

    def test_solo_slam_over_any_numeric_legal(self, north, east):
        for value in (80, 90, 100, 130, 160):
            auction = Auction((ContractBid(east, value, Suit.HEARTS),))
            assert (
                auction.is_legal(ContractBid(north, "SoloSlam", Suit.SPADES))
                is True
            )

    def test_numeric_over_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, "Slam", Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 160, Suit.SPADES)) is False

    def test_numeric_over_solo_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, "SoloSlam", Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 160, Suit.SPADES)) is False

    def test_slam_over_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, "Slam", Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, "Slam", Suit.SPADES)) is False
        )

    def test_solo_slam_after_slam_illegal(self, north, east):
        """Asymmetric block: SoloSlam (1000) outranks Slam (500), but
        once a Slam is on the table the auction is closed to further
        contract bids — including the otherwise-higher SoloSlam."""
        auction = Auction((ContractBid(east, "Slam", Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, "SoloSlam", Suit.SPADES))
            is False
        )

    def test_slam_after_solo_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, "SoloSlam", Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, "Slam", Suit.SPADES)) is False
        )

    def test_solo_slam_over_solo_slam_illegal(self, north, east):
        auction = Auction((ContractBid(east, "SoloSlam", Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, "SoloSlam", Suit.SPADES))
            is False
        )

    def test_passes_do_not_change_precedence(self, north, east, south):
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                PassBid(south),
                PassBid(north),
            ),
        )
        assert auction.is_legal(ContractBid(east, 110, Suit.HEARTS)) is True
        assert auction.is_legal(ContractBid(east, 100, Suit.HEARTS)) is False

    def test_double_freezes_auction(self, north, east, south, four_players):
        auction = Auction(
            (ContractBid(east, 100, Suit.HEARTS), DoubleBid(south)),
        )
        # No new numeric bid can reopen a frozen auction.
        assert auction.is_legal(ContractBid(north, 110, Suit.HEARTS)) is False
        # Even Slam / SoloSlam can't.
        assert (
            auction.is_legal(ContractBid(north, "Slam", Suit.SPADES))
            is False
        )
        assert (
            auction.is_legal(ContractBid(north, "SoloSlam", Suit.SPADES))
            is False
        )

    def test_redouble_also_freezes_auction(
        self, north, east, south, four_players
    ):
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(south),
                RedoubleBid(east),
            ),
        )
        assert auction.is_legal(ContractBid(north, 130, Suit.SPADES)) is False

    def test_passes_after_double_still_freeze_auction(
        self, north, east, south, west, four_players
    ):
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(south),
                PassBid(west),
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
        self, north, east, south, four_players
    ):
        auction = Auction(
            (ContractBid(east, 100, Suit.HEARTS), DoubleBid(south)),
        )
        # Freeze blocks every value, including the Slam family.
        for value in ContractBid.VALID_VALUES:
            assert auction._is_contract_value_legal(value) is False

    def test_value_legal_false_when_slam_announced(self, east):
        auction = Auction((ContractBid(east, "Slam", Suit.HEARTS),))
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
        self, north, east, south, west, four_players,
    ) -> list[Auction]:
        """Auction histories covering every shape the rule helper sees:
        empty, after a low numeric, after the numeric ceiling, after a
        Slam, after a SoloSlam, and after a freeze by Double."""
        cb_low = ContractBid(east, 100, Suit.HEARTS)
        cb_ceiling = ContractBid(east, 160, Suit.HEARTS)
        cb_slam = ContractBid(east, "Slam", Suit.HEARTS)
        cb_solo = ContractBid(east, "SoloSlam", Suit.HEARTS)
        return [
            Auction(),
            Auction((cb_low,)),
            Auction((cb_ceiling,)),
            Auction((cb_slam,)),
            Auction((cb_solo,)),
            Auction((cb_low, DoubleBid(south))),
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
        self, north, east, south, four_players,
    ):
        """The short-circuited :meth:`Auction.legal_actions` produces the
        same set of :class:`ContractBid` actions as a full per-value probe
        would. Drives the equivalence the optimisation relies on across
        every history shape exercised by :attr:`histories`."""
        scenarios = [
            Auction(),
            Auction((ContractBid(east, 100, Suit.HEARTS),)),
            Auction((ContractBid(east, 160, Suit.HEARTS),)),
            Auction((ContractBid(east, "Slam", Suit.HEARTS),)),
            Auction((ContractBid(east, "SoloSlam", Suit.HEARTS),)),
            Auction(
                (ContractBid(east, 100, Suit.HEARTS), DoubleBid(south)),
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
    def test_illegal_on_empty(self, east):
        assert Auction().is_legal(DoubleBid(east)) is False

    def test_legal_against_opponent_contract(self, north, east, four_players):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        assert auction.is_legal(DoubleBid(east)) is True

    def test_illegal_against_own_contract(self, north, south, four_players):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        # South is North's partner — can't double their own contract.
        assert auction.is_legal(DoubleBid(south)) is False

    def test_illegal_if_already_doubled(self, north, east, four_players):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), DoubleBid(east)),
        )
        assert auction.is_legal(DoubleBid(east)) is False

    def test_illegal_after_redouble(self, north, east, four_players):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.is_legal(DoubleBid(east)) is False

    def test_legal_after_pass_since_contract(
        self, north, east, four_players
    ):
        """A pass between the contract bid and the Coinche does not
        close the window — opposing players may still come back and
        Double until the auction terminates on 3 consecutive passes.
        See ``contree-domain.md §5.3``.
        """
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), PassBid(east)),
        )
        # South is on the opposing team (NS contracted, so this needs
        # the opposing player). North contracted (NS team), East passed
        # (EW), so EW are the opponents — East may still Double.
        assert auction.is_legal(DoubleBid(east)) is True

    def test_legal_after_two_passes_since_contract(
        self, north, east, south, west, four_players
    ):
        """Two intervening passes still don't close the window —
        only the auction's 3-consecutive-passes terminator does."""
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(east),
                PassBid(south),
            ),
        )
        assert auction.is_legal(DoubleBid(west)) is True

    def test_legal_against_opponent_slam(self, north, east, four_players):
        """Slam closes the auction to numeric / Slam-family bids but
        coinche must remain available — opponents can still Double."""
        auction = Auction((ContractBid(north, "Slam", Suit.SPADES),))
        assert auction.is_legal(DoubleBid(east)) is True

    def test_legal_against_opponent_solo_slam(
        self, north, east, four_players
    ):
        auction = Auction((ContractBid(north, "SoloSlam", Suit.SPADES),))
        assert auction.is_legal(DoubleBid(east)) is True


# ---------------------------------------------------------------------------
# RedoubleBid legality
# ---------------------------------------------------------------------------


class TestRedoubleLegality:
    def test_illegal_on_empty(self, north):
        assert Auction().is_legal(RedoubleBid(north)) is False

    def test_illegal_without_prior_double(self, north, four_players):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        assert auction.is_legal(RedoubleBid(north)) is False

    def test_legal_for_contracting_team(self, north, south, east, four_players):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), DoubleBid(east)),
        )
        # Either member of the contracting team may redouble.
        assert auction.is_legal(RedoubleBid(north)) is True
        assert auction.is_legal(RedoubleBid(south)) is True

    def test_illegal_for_opposing_team(self, north, east, west, four_players):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), DoubleBid(east)),
        )
        # The doubling side cannot then redouble.
        assert auction.is_legal(RedoubleBid(west)) is False

    def test_illegal_if_already_redoubled(self, north, east, four_players):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.is_legal(RedoubleBid(north)) is False

    def test_legal_after_pass_since_double(
        self, north, east, south, four_players
    ):
        """Symmetric with the Double window: an intervening pass after
        the Coinche does not close the Surcoinche window. The
        contracting team may still come back and Redouble until the
        auction's 3-consecutive-passes terminator fires.
        See ``contree-domain.md §5.3``.
        """
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                PassBid(south),
            ),
        )
        assert auction.is_legal(RedoubleBid(north)) is True


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
        self, north, east, four_players
    ):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        actions = auction.legal_actions(east)
        assert any(isinstance(a, DoubleBid) for a in actions)
        # And opponents of the contractor *also* get the legal numeric raises.
        contract_raises = [
            a for a in actions
            if isinstance(a, ContractBid)
            and a.value not in ("Slam", "SoloSlam")
            and a.value > 100
        ]
        assert contract_raises  # at least one higher-value contract exists

    def test_includes_redouble_after_being_doubled(
        self, north, east, four_players
    ):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), DoubleBid(east)),
        )
        actions = auction.legal_actions(north)
        assert any(isinstance(a, RedoubleBid) for a in actions)
        # Partner of the contractor — same team — also gets a redouble option.
        south = north.team.players[1]
        partner_actions = auction.legal_actions(south)
        assert any(isinstance(a, RedoubleBid) for a in partner_actions)

    def test_only_pass_when_partner_doubled(
        self, north, east, south, four_players
    ):
        """E (opponent) contracts; N (S's partner) doubles. S's only
        legal action is Pass — drives the engine's auto-pass shortcut."""
        auction = Auction(
            (ContractBid(east, 100, Suit.HEARTS), DoubleBid(north)),
        )
        actions = auction.legal_actions(south)
        assert len(actions) == 1
        assert isinstance(actions[0], PassBid)

    def test_only_pass_when_partner_redoubled(
        self, north, south, west, four_players
    ):
        """N contracts, W doubles, S (N's partner) redoubles. N is up
        next and the only legal action is Pass."""
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(west),
                RedoubleBid(south),
            ),
        )
        actions = auction.legal_actions(north)
        assert len(actions) == 1
        assert isinstance(actions[0], PassBid)

    def test_only_pass_when_partner_doubled_even_after_pass(
        self, north, east, south, west, four_players
    ):
        """The partner of a doubler still has only Pass available
        regardless of how many passes follow the Double: they cannot
        re-Double (their team already did), the auction is frozen so
        no contract is legal, and they're on the wrong team to
        Redouble."""
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
        self, north, east, west, four_players
    ):
        """The contracting team may come back and Redouble even after
        passes intervene since the Double — only the auction's three-
        consecutive-passes terminator closes the window.
        See ``contree-domain.md §5.3``.
        """
        auction = Auction(
            (
                ContractBid(east, 100, Suit.HEARTS),
                DoubleBid(north),
                PassBid(west),
            ),
        )
        # East (contracting team) is up after this — Redouble must
        # remain in their legal set.
        actions = auction.legal_actions(east)
        assert any(isinstance(a, RedoubleBid) for a in actions)

    def test_double_still_available_after_intervening_passes(
        self, north, east, south, west, four_players,
    ):
        """Symmetric to the Redouble case: opposing players may come
        back and Coinche after intervening passes since the contract
        bid. See ``contree-domain.md §5.3``.
        """
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                PassBid(east),
                PassBid(south),
            ),
        )
        # West (opposing team) is up after two passes — Double must
        # still be on the table.
        actions = auction.legal_actions(west)
        assert any(isinstance(a, DoubleBid) for a in actions)

    def test_no_auto_pass_when_opponent_doubles_own_partner(
        self, north, east, south, four_players
    ):
        """Partner (N) contracted, opponent (E) doubled. S (N's partner,
        contracting team) has BOTH Pass and Redouble — must not auto-pass."""
        auction = Auction(
            (ContractBid(north, 100, Suit.HEARTS), DoubleBid(east)),
        )
        actions = auction.legal_actions(south)
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

    def test_apply_chains(self, north, east, four_players):
        auction = (
            Auction()
            .apply(PassBid(east))
            .apply(ContractBid(north, 80, Suit.SPADES))
        )
        assert len(auction.bids) == 2
        assert auction.last_contract_bid == ContractBid(north, 80, Suit.SPADES)

    def test_apply_illegal_raises(self, north, east, four_players):
        auction = Auction((ContractBid(east, 100, Suit.HEARTS),))
        illegal = ContractBid(north, 90, Suit.SPADES)  # lower than 100
        with pytest.raises(IllegalBidError) as excinfo:
            auction.apply(illegal)
        # The exception carries the offending bid and prior history.
        assert excinfo.value.bid is illegal
        assert excinfo.value.bids == auction.bids

    def test_apply_double_against_own_team_raises(
        self, north, south, four_players
    ):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        with pytest.raises(IllegalBidError):
            auction.apply(DoubleBid(south))


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------


class TestIsTerminal:
    def test_empty_not_terminal(self):
        assert Auction().is_terminal() is False

    def test_three_passes_after_contract_terminal(
        self, north, east, south, west, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(east),
                PassBid(south),
                PassBid(west),
            ),
        )
        assert auction.is_terminal() is True

    def test_two_passes_after_contract_not_terminal(
        self, north, east, south, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(east),
                PassBid(south),
            ),
        )
        assert auction.is_terminal() is False

    def test_four_passes_terminal_all_pass_wipe(
        self, north, east, south, west
    ):
        auction = Auction(
            (
                PassBid(north),
                PassBid(east),
                PassBid(south),
                PassBid(west),
            ),
        )
        assert auction.is_terminal() is True

    def test_three_passes_no_contract_not_terminal(
        self, north, east, south
    ):
        auction = Auction(
            (
                PassBid(north),
                PassBid(east),
                PassBid(south),
            ),
        )
        # Need the full all-pass wipe (4 passes) before annulling.
        assert auction.is_terminal() is False

    def test_three_passes_after_double_terminal(
        self, north, east, south, west, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                PassBid(south),
                PassBid(west),
                PassBid(north),
            ),
        )
        assert auction.is_terminal() is True


class TestContractMaterialisation:
    def test_no_contract_when_all_pass(self, north, east, south, west):
        auction = Auction(
            (
                PassBid(north),
                PassBid(east),
                PassBid(south),
                PassBid(west),
            ),
        )
        assert auction.contract() is None

    def test_simple_contract(self, north, east, south, west, four_players):
        auction = Auction(
            (
                ContractBid(north, 80, Suit.SPADES),
                PassBid(east),
                PassBid(south),
                PassBid(west),
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
        self, north, east, south, west, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                PassBid(south),
                PassBid(west),
                PassBid(north),
            ),
        )
        contract = auction.contract()
        assert contract.double is True
        assert contract.redouble is False

    def test_redoubled_contract(
        self, north, east, south, west, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                RedoubleBid(south),
                PassBid(west),
                PassBid(north),
                PassBid(east),
            ),
        )
        contract = auction.contract()
        assert contract.double is True
        assert contract.redouble is True


# ---------------------------------------------------------------------------
# State-query properties
# ---------------------------------------------------------------------------


class TestStateProperties:
    def test_last_contract_bid_none_when_empty(self):
        assert Auction().last_contract_bid is None

    def test_last_contract_bid_none_when_only_passes(self, north):
        auction = Auction((PassBid(north),))
        assert auction.last_contract_bid is None

    def test_last_contract_bid_returns_most_recent(self, north, east):
        first = ContractBid(north, 80, Suit.SPADES)
        second = ContractBid(east, 90, Suit.HEARTS)
        auction = Auction((first, PassBid(east), second))
        assert auction.last_contract_bid is second

    def test_has_double_true_after_double(
        self, north, east, four_players
    ):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), DoubleBid(east)),
        )
        assert auction.has_double is True

    def test_has_double_false_when_no_double(self, north, four_players):
        auction = Auction((ContractBid(north, 100, Suit.SPADES),))
        assert auction.has_double is False

    def test_has_redouble_true_after_redouble(
        self, north, east, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                RedoubleBid(north),
            ),
        )
        assert auction.has_redouble is True

    def test_has_redouble_false_when_only_double(
        self, north, east, four_players
    ):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), DoubleBid(east)),
        )
        assert auction.has_redouble is False

    def test_consecutive_passes_counts_from_tail(
        self, north, east, south, four_players
    ):
        cb = ContractBid(north, 100, Suit.SPADES)
        assert Auction().consecutive_passes == 0
        assert Auction((cb,)).consecutive_passes == 0
        assert Auction((cb, PassBid(east))).consecutive_passes == 1
        assert (
            Auction((cb, PassBid(east), PassBid(south))).consecutive_passes
            == 2
        )
        # Trailing non-pass resets.
        assert (
            Auction(
                (cb, PassBid(east), ContractBid(south, 110, Suit.HEARTS)),
            ).consecutive_passes
            == 0
        )

    def test_partner_bid_returns_partner_last_non_pass(
        self, north, east, south, four_players
    ):
        auction = Auction(
            (
                ContractBid(east, 80, Suit.SPADES),
                ContractBid(north, 90, Suit.HEARTS),
                PassBid(east),
                PassBid(south),
            ),
        )
        # South's partner is North; partner's last non-pass is 90 ♥.
        assert auction.partner_bid(south) == ContractBid(
            north, 90, Suit.HEARTS
        )

    def test_partner_bid_none_when_only_passes(
        self, north, east, south, four_players
    ):
        auction = Auction((PassBid(north),))
        assert auction.partner_bid(south) is None
