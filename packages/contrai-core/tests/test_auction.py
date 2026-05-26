"""Tests for the :class:`Auction` rule oracle and state machine.

The auction owns the chronological bid history and answers the
"what is legal now?" / "is bidding over?" / "what contract did this
produce?" questions previously scattered across ``Bid.is_valid_after``
and ``BidValidator``. These tests cover:

- :meth:`Auction.is_legal` for each :class:`Bid` subtype, including
  the Capot precedence rules from ``contree-domain.md §5.2`` and the
  Double-freezes-the-auction rule from §5.3.
- :meth:`Auction.legal_actions` enumeration shape and the "only Pass
  is legal" cases (partner just doubled / redoubled) that drive the
  engine's auto-pass shortcut.
- :meth:`Auction.apply` (happy path + ``IllegalBidError`` on illegal
  bids — *no* silent downgrade to Pass).
- :meth:`Auction.is_terminal` and :meth:`Auction.contract` covering
  both end conditions from §5.4.
- The state-query properties: ``last_contract``, ``has_double``,
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
        assert auction.last_contract is None

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

    def test_capot_over_any_numeric_legal(self, north, east):
        for value in (80, 90, 100, 130, 160):
            auction = Auction((ContractBid(east, value, Suit.HEARTS),))
            assert (
                auction.is_legal(ContractBid(north, "Capot", Suit.SPADES))
                is True
            )

    def test_numeric_over_capot_illegal(self, north, east):
        auction = Auction((ContractBid(east, "Capot", Suit.HEARTS),))
        assert auction.is_legal(ContractBid(north, 160, Suit.SPADES)) is False

    def test_capot_over_capot_illegal(self, north, east):
        auction = Auction((ContractBid(east, "Capot", Suit.HEARTS),))
        assert (
            auction.is_legal(ContractBid(north, "Capot", Suit.SPADES)) is False
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
        # Even Capot can't.
        assert (
            auction.is_legal(ContractBid(north, "Capot", Suit.SPADES))
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

    def test_illegal_after_pass_since_contract(
        self, north, east, four_players
    ):
        auction = Auction(
            (ContractBid(north, 100, Suit.SPADES), PassBid(east)),
        )
        assert auction.is_legal(DoubleBid(east)) is False


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

    def test_illegal_after_pass_since_double(
        self, north, east, south, four_players
    ):
        auction = Auction(
            (
                ContractBid(north, 100, Suit.SPADES),
                DoubleBid(east),
                PassBid(south),
            ),
        )
        assert auction.is_legal(RedoubleBid(north)) is False


# ---------------------------------------------------------------------------
# legal_actions enumeration
# ---------------------------------------------------------------------------


class TestLegalActions:
    """Shape of the enumerated legal-actions set."""

    def test_empty_auction_includes_pass_and_all_contracts(self, north):
        actions = Auction().legal_actions(north)
        # Always starts with the Pass action.
        assert isinstance(actions[0], PassBid)
        # 10 values × 6 suits = 60 ContractBids legal at start, plus the Pass.
        contracts = [a for a in actions if isinstance(a, ContractBid)]
        assert len(contracts) == 10 * 6
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
            if isinstance(a, ContractBid) and a.value != "Capot"
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

    def test_only_pass_after_passes_close_redouble_window(
        self, north, east, south, west, four_players
    ):
        """Once a pass appears after the Double, no one can act except
        passing — the contract is locked in at ×2."""
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
        assert auction.last_contract == ContractBid(north, 80, Suit.SPADES)

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
    def test_last_contract_none_when_empty(self):
        assert Auction().last_contract is None

    def test_last_contract_none_when_only_passes(self, north):
        auction = Auction((PassBid(north),))
        assert auction.last_contract is None

    def test_last_contract_returns_most_recent(self, north, east):
        first = ContractBid(north, 80, Suit.SPADES)
        second = ContractBid(east, 90, Suit.HEARTS)
        auction = Auction((first, PassBid(east), second))
        assert auction.last_contract is second

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

    def test_partner_bid_none_when_no_team(self, north):
        # No team assigned at all (deliberate fixture omission).
        assert Auction().partner_bid(north) is None
