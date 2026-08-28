"""Unit tests for the AI explainability channel.

Two things are pinned here. First the value types themselves —
``RuleCitation`` / ``Rationale`` / ``BidDecision`` / ``CardDecision``
are frozen, slotted, value-equal records with the documented defaults.
Second, and the point of the whole exercise: **every** return path of
both rule-based strategies comes back explained. The parametrized
sweeps below drive each strategy through the branches of its ladder and
assert a non-empty ``rule`` and ``detail`` on the way out, so a new
branch added without a rationale fails here rather than reaching a
debug panel as a blank line.
"""

from __future__ import annotations

import dataclasses

import pytest

from contrai_core import (
    Auction,
    Card,
    Contract,
    ContractBid,
    DoubleBid,
    Hand,
    ObservedPlay,
    PassBid,
    PlayObservation,
    Rank,
    RedoubleBid,
    Suit,
)
from contrai_core.play import Play

from contrai_engine.model.player import (
    BidDecision,
    CardDecision,
    Rationale,
    RuleCitation,
)


def _c(suit, rank):
    return Card(suit, rank)


def _contract(player, value, suit):
    return Contract(ContractBid(player, value, suit))


def _obs(observer, hand, contract, *, current_trick=(), completed_tricks=(),
         legal_cards=None):
    """Assemble a sealed :class:`PlayObservation`, as ``observe`` does."""
    hand = tuple(hand)

    def seal(plays):
        return tuple(
            ObservedPlay(play.player.position, play.card) for play in plays
        )

    return PlayObservation(
        position=observer.position,
        hand=hand,
        contract=contract.observed() if contract is not None else None,
        bids=(),
        completed_tricks=tuple(seal(trick) for trick in completed_tricks),
        current_trick=seal(current_trick),
        legal_cards=tuple(hand if legal_cards is None else legal_cards),
    )


# ---------------------------------------------------------------------------
# The value types
# ---------------------------------------------------------------------------


class TestValueTypes:
    """Frozen, slotted, value-equal records with the documented defaults."""

    def test_rationale_defaults_to_no_alternatives_and_no_citations(self):
        rationale = Rationale("cash the master", "led the master.")
        assert rationale.considered == ()
        assert rationale.citations == ()

    def test_rationale_is_value_equal(self):
        one = Rationale("r", "d", ("a",), (RuleCitation("k", "v", "e"),))
        two = Rationale("r", "d", ("a",), (RuleCitation("k", "v", "e"),))
        assert one == two

    @pytest.mark.parametrize(
        "value",
        [
            RuleCitation("k", "v", "e"),
            Rationale("r", "d"),
            BidDecision(PassBid(None), Rationale("r", "d")),
            CardDecision(_c(Suit.SPADES, Rank.SEVEN), Rationale("r", "d")),
        ],
    )
    def test_every_type_is_frozen(self, value):
        field = next(iter(dataclasses.fields(value))).name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, "nope")

    @pytest.mark.parametrize(
        "cls", [RuleCitation, Rationale, BidDecision, CardDecision]
    )
    def test_every_type_is_slotted(self, cls):
        """No ``__dict__`` — a stray attribute cannot be smuggled on."""
        assert hasattr(cls, "__slots__")

    def test_decisions_carry_what_was_decided(self):
        card = _c(Suit.HEARTS, Rank.ACE)
        decision = CardDecision(card, Rationale("cash an ace", "led it."))
        assert decision.card is card
        assert decision.rationale.rule == "cash an ace"

    def test_a_citation_names_knob_value_and_local_effect(self):
        citation = RuleCitation(
            "under_trump_exemption", "True", "discarded instead of "
            "under-trumping"
        )
        assert citation.knob == "under_trump_exemption"
        assert citation.value == "True"
        assert "under-trumping" in citation.effect


# ---------------------------------------------------------------------------
# Every card-play branch is explained
# ---------------------------------------------------------------------------


def _card_scenarios(players):
    """One observation per branch of the card-play ladder.

    Each entry is ``(label, seat, observation)``; the branch each one
    lands on is named in the label so a failure says which return path
    came back unexplained.
    """
    north, east, south, west = (players[s] for s in "NESW")
    hearts_trick = (
        Play(west, _c(Suit.HEARTS, Rank.ACE)),
        Play(north, _c(Suit.HEARTS, Rank.SEVEN)),
        Play(east, _c(Suit.HEARTS, Rank.TEN)),
        Play(south, _c(Suit.HEARTS, Rank.EIGHT)),
    )
    both_void = (
        Play(north, _c(Suit.HEARTS, Rank.ACE)),
        Play(east, _c(Suit.CLUBS, Rank.SEVEN)),
        Play(south, _c(Suit.HEARTS, Rank.KING)),
        Play(west, _c(Suit.DIAMONDS, Rank.SEVEN)),
    )
    spades = _contract(north, 100, Suit.SPADES)
    opponents = _contract(east, 100, Suit.SPADES)

    return [
        (
            "open on trump",
            north,
            _obs(north, [_c(Suit.SPADES, Rank.JACK),
                         _c(Suit.HEARTS, Rank.EIGHT)], spades),
        ),
        (
            "open on trump avoiding the 9",
            north,
            _obs(north, [_c(Suit.SPADES, Rank.NINE),
                         _c(Suit.SPADES, Rank.KING)], spades),
        ),
        (
            "opening defender cashes an ace",
            north,
            _obs(north, [_c(Suit.DIAMONDS, Rank.ACE),
                         _c(Suit.CLUBS, Rank.EIGHT)], opponents),
        ),
        (
            "opening concedes cheaply",
            north,
            _obs(north, [_c(Suit.DIAMONDS, Rank.EIGHT),
                         _c(Suit.CLUBS, Rank.SEVEN)], opponents),
        ),
        (
            "lead pulls trump",
            north,
            _obs(north, [_c(Suit.SPADES, Rank.JACK),
                         _c(Suit.DIAMONDS, Rank.EIGHT)], spades,
                 completed_tricks=[hearts_trick]),
        ),
        (
            "lead cashes an ace",
            north,
            _obs(north, [_c(Suit.DIAMONDS, Rank.ACE),
                         _c(Suit.CLUBS, Rank.EIGHT)], spades,
                 completed_tricks=[both_void]),
        ),
        (
            "lead cashes a master",
            north,
            _obs(north, [_c(Suit.HEARTS, Rank.KING),
                         _c(Suit.CLUBS, Rank.EIGHT)], spades,
                 completed_tricks=[hearts_trick]),
        ),
        (
            "lead concedes cheaply",
            north,
            _obs(north, [_c(Suit.DIAMONDS, Rank.EIGHT),
                         _c(Suit.CLUBS, Rank.SEVEN)], spades,
                 completed_tricks=[both_void]),
        ),
        (
            "team winning: pile onto partner",
            north,
            _obs(north, [_c(Suit.HEARTS, Rank.KING),
                         _c(Suit.HEARTS, Rank.SEVEN)], spades,
                 current_trick=(Play(south, _c(Suit.HEARTS, Rank.ACE)),)),
        ),
        (
            "team winning: discard onto partner",
            north,
            _obs(north, [_c(Suit.DIAMONDS, Rank.KING),
                         _c(Suit.CLUBS, Rank.SEVEN)], spades,
                 current_trick=(Play(south, _c(Suit.HEARTS, Rank.ACE)),)),
        ),
        (
            "team winning: nothing but trump",
            north,
            _obs(north, [_c(Suit.SPADES, Rank.SEVEN),
                         _c(Suit.SPADES, Rank.EIGHT)], spades,
                 current_trick=(Play(south, _c(Suit.HEARTS, Rank.ACE)),)),
        ),
        (
            "team losing: beat with the fattest winner",
            north,
            _obs(north, [_c(Suit.HEARTS, Rank.ACE),
                         _c(Suit.HEARTS, Rank.EIGHT)], spades,
                 current_trick=(Play(west, _c(Suit.HEARTS, Rank.KING)),),
                 legal_cards=[_c(Suit.HEARTS, Rank.ACE),
                              _c(Suit.HEARTS, Rank.EIGHT)]),
        ),
        (
            "team losing: concede the trick",
            north,
            _obs(north, [_c(Suit.HEARTS, Rank.JACK),
                         _c(Suit.HEARTS, Rank.EIGHT)], spades,
                 current_trick=(Play(west, _c(Suit.HEARTS, Rank.ACE)),),
                 legal_cards=[_c(Suit.HEARTS, Rank.JACK),
                              _c(Suit.HEARTS, Rank.EIGHT)]),
        ),
        (
            "team losing: ruff to win",
            north,
            _obs(north, [_c(Suit.SPADES, Rank.NINE),
                         _c(Suit.DIAMONDS, Rank.EIGHT)], spades,
                 current_trick=(Play(west, _c(Suit.HEARTS, Rank.KING)),)),
        ),
        (
            "team losing: concede cheaply under the exemption",
            south,
            _obs(south,
                 [_c(Suit.SPADES, Rank.SEVEN), _c(Suit.CLUBS, Rank.SEVEN)],
                 opponents,
                 current_trick=(
                     Play(west, _c(Suit.HEARTS, Rank.ACE)),
                     Play(north, _c(Suit.HEARTS, Rank.SEVEN)),
                     Play(east, _c(Suit.SPADES, Rank.JACK)),
                 ),
                 legal_cards=[_c(Suit.SPADES, Rank.SEVEN),
                              _c(Suit.CLUBS, Rank.SEVEN)]),
        ),
    ]


#: How many card-play scenarios ``_card_scenarios`` builds. Parametrizing
#: by index keeps each branch its own test case (and its own failure line)
#: without calling the builder at collection time, when the ``players``
#: fixture does not exist yet.
_CARD_SCENARIO_COUNT = 15


class TestEveryCardPlayBranchIsExplained:
    """No return path of the card-play ladder comes back unexplained."""

    def test_the_sweep_reaches_every_named_rule(self, players):
        """The scenarios really do fan out, rather than all landing on one
        branch and passing the sweep below vacuously."""
        rules = {
            seat.cardplay.choose_card(obs).rationale.rule
            for _, seat, obs in _card_scenarios(players)
        }
        assert len(rules) >= 8

    @pytest.mark.parametrize("index", range(_CARD_SCENARIO_COUNT))
    def test_each_branch_names_a_rule_and_a_detail(self, players, index):
        label, seat, obs = _card_scenarios(players)[index]
        decision = seat.cardplay.choose_card(obs)
        assert isinstance(decision, CardDecision), label
        assert decision.card in obs.legal_cards, label
        assert decision.rationale.rule, label
        assert decision.rationale.detail, label

    def test_the_exemption_branch_cites_the_knob(self, players):
        """The §9.5 knob is named where it actually decided something."""
        scenarios = dict(
            (label, (seat, obs))
            for label, seat, obs in _card_scenarios(players)
        )
        seat, obs = scenarios[
            "team losing: concede cheaply under the exemption"
        ]
        citations = seat.cardplay.choose_card(obs).rationale.citations
        assert [(c.knob, c.effect) for c in citations] == [
            ("under_trump_exemption", "discarded instead of under-trumping")
        ]

    def test_a_plain_discard_does_not_cite_the_exemption(self, players):
        """A seat holding no trump was never excused from anything."""
        north, west = players["N"], players["W"]
        obs = _obs(
            north,
            [_c(Suit.DIAMONDS, Rank.EIGHT), _c(Suit.CLUBS, Rank.SEVEN)],
            _contract(west, 100, Suit.SPADES),
            current_trick=(Play(west, _c(Suit.HEARTS, Rank.ACE)),),
        )
        decision = north.cardplay.choose_card(obs)
        assert decision.rationale.citations == ()


# ---------------------------------------------------------------------------
# Every bidding branch is explained
# ---------------------------------------------------------------------------


def _bidding_scenarios(players):
    """One ``(label, seat, hand, auction)`` per branch of the bid ladder."""
    north, east, south, west = (players[s] for s in "NESW")
    weak = Hand([
        _c(Suit.SPADES, Rank.SEVEN), _c(Suit.SPADES, Rank.EIGHT),
        _c(Suit.HEARTS, Rank.SEVEN), _c(Suit.HEARTS, Rank.EIGHT),
        _c(Suit.DIAMONDS, Rank.SEVEN), _c(Suit.DIAMONDS, Rank.EIGHT),
        _c(Suit.CLUBS, Rank.SEVEN), _c(Suit.CLUBS, Rank.EIGHT),
    ])
    strong = Hand([
        _c(Suit.SPADES, Rank.JACK), _c(Suit.SPADES, Rank.NINE),
        _c(Suit.SPADES, Rank.ACE), _c(Suit.SPADES, Rank.KING),
        _c(Suit.HEARTS, Rank.ACE), _c(Suit.DIAMONDS, Rank.ACE),
        _c(Suit.CLUBS, Rank.ACE), _c(Suit.CLUBS, Rank.JACK),
    ])
    support = Hand([
        _c(Suit.SPADES, Rank.JACK), _c(Suit.SPADES, Rank.SEVEN),
        _c(Suit.HEARTS, Rank.ACE), _c(Suit.HEARTS, Rank.EIGHT),
        _c(Suit.DIAMONDS, Rank.ACE), _c(Suit.DIAMONDS, Rank.EIGHT),
        _c(Suit.CLUBS, Rank.SEVEN), _c(Suit.CLUBS, Rank.EIGHT),
    ])

    return [
        ("open on the table", north, strong, Auction()),
        ("pass on a weak hand", north, weak, Auction()),
        (
            "pass under an out-of-reach bid",
            north,
            Hand(list(strong)[:8]),
            Auction((ContractBid(east, 160, Suit.HEARTS),)),
        ),
        (
            "support partner",
            north,
            support,
            Auction((ContractBid(south, 80, Suit.SPADES), PassBid(west))),
        ),
        (
            "our cards are already priced in",
            north,
            support,
            Auction((ContractBid(north, 80, Suit.SPADES), PassBid(east))),
        ),
        (
            "frozen by a Double",
            north,
            weak,
            Auction((ContractBid(north, 80, Suit.SPADES), DoubleBid(east))),
        ),
        (
            "frozen by a Redouble",
            north,
            weak,
            Auction((
                ContractBid(north, 80, Suit.SPADES),
                DoubleBid(east),
                RedoubleBid(south),
            )),
        ),
        (
            "double the opponents",
            north,
            strong,
            Auction((ContractBid(east, 160, Suit.HEARTS),)),
        ),
    ]


#: How many bidding scenarios ``_bidding_scenarios`` builds. Same reason
#: as :data:`_CARD_SCENARIO_COUNT`.
_BIDDING_SCENARIO_COUNT = 8


class TestEveryBiddingBranchIsExplained:
    """No return path of the bidding ladder comes back unexplained."""

    def test_the_sweep_reaches_several_distinct_rules(self, players):
        rules = set()
        for _, seat, hand, auction in _bidding_scenarios(players):
            seat.hand = hand
            rules.add(seat.choose_bid(auction).rationale.rule)
        assert len(rules) >= 5

    @pytest.mark.parametrize("index", range(_BIDDING_SCENARIO_COUNT))
    def test_each_branch_names_a_rule_and_a_detail(self, players, index):
        label, seat, hand, auction = _bidding_scenarios(players)[index]
        seat.hand = hand
        decision = seat.choose_bid(auction)
        assert isinstance(decision, BidDecision), label
        assert auction.is_legal(decision.bid), label
        assert decision.rationale.rule, label
        assert decision.rationale.detail, label
