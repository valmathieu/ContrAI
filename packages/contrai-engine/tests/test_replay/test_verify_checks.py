"""Pins each of the verifier's checks called directly, fault branches included.

``test_verify.py`` proves the five classes end to end, by breaking a real
record and watching the verdict come back. That leaves one part of the
module thinly exercised, and it is the part that matters most: an illegal
action **ends a round's replay**, so a mutation that trips check 1 never
reaches checks 3 to 5, and the code that actually *produces* a finding is
the code the end-to-end tests reach least.

So these call each check on a hand-built ``_RoundCheck``, with stand-ins
for the engine round. The stand-ins are deliberately minimal — each check
reads two or three attributes, and naming exactly those is what makes it
obvious when one starts reading more.
"""

from __future__ import annotations

import pytest
from contrai_core import (
    Auction,
    Card,
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    Rank,
    RuleConfig,
    Suit,
    TeamSide,
)
from contrai_core.exceptions import (
    IllegalBidError,
    IllegalPlayError,
    PlayRuleViolation,
)
from contrai_data import BeloteHeld, SideMark, SlamOutcome

from contrai_engine.model.round.components import Mark
from contrai_engine.model.round.scoring import RoundScore
from contrai_engine.replay.exceptions import (
    ScriptExhaustedError,
    SeatMismatchError,
)
from contrai_data import MismatchKind
from contrai_engine.replay.verify import (
    _BIDDING,
    _PLAY,
    _RoundCheck,
    _check_auction,
    _check_belote,
    _check_score,
    _check_trick_winners,
    _classify,
    _same_bid,
    _taken_agrees,
)

TS = "2026-09-11T00:00:00Z"
SPADE_7 = Card(Suit.SPADES, Rank.SEVEN)
SPADE_K = Card(Suit.SPADES, Rank.KING)
SPADE_Q = Card(Suit.SPADES, Rank.QUEEN)
HEART_K = Card(Suit.HEARTS, Rank.KING)
HEART_Q = Card(Suit.HEARTS, Rank.QUEEN)


# ---------------------------------------------------------------------------
# Stand-ins
# ---------------------------------------------------------------------------


class _Record:
    """The ``RoundRecord`` attributes the checks read."""

    def __init__(
        self,
        number=1,
        auction=(),
        trick_winners=(),
        hands=None,
        belotes=(),
        score=None,
    ):
        self.number = number
        self.auction = auction
        self.trick_winners = trick_winners
        self.hands = hands or {}
        self.belotes = belotes
        self.score = score


class _Seat:
    """A live player as the checks see one: a seat, and maybe a team."""

    def __init__(self, position):
        self.position = position


class _State:
    """The ``PlayState`` attribute ``_check_trick_winners`` reads."""

    def __init__(self, winners):
        self.trick_winners = [_Seat(seat) for seat in winners]


class _Round:
    """The engine round the end-of-round checks read."""

    def __init__(self, auction=None, winners=None, score=None, contract=None,
                 rules=None):
        self.auction = auction
        self.play_state = None if winners is None else _State(winners)
        self.round_score = score
        self.contract = contract
        # A real round always has one; the catalogue defaults stand in here,
        # as they do for a round built without an explicit ruleset.
        self.rules = RuleConfig() if rules is None else rules


class _Contract:
    """The contract attributes the score check reads."""

    def __init__(self, position, value=80, suit=Suit.SPADES):
        self.player = _Seat(position)
        self.value = value
        self.suit = suit

    def is_slam(self):
        return False

    def is_solo_slam(self):
        return False


def _score(
    *,
    made=True,
    marks=None,
    card_points=None,
    belote=None,
    last_trick=TeamSide.NS,
    multiplier=1,
    slam=None,
):
    """A ``RoundScore`` with everything defaulted to an ordinary made round."""

    return RoundScore(
        scores={side: 0 for side in TeamSide},
        contract_made=made,
        unannounced_slam=slam,
        marks=marks or {TeamSide.NS: Mark(100, 80), TeamSide.EW: Mark(62, 0)},
        belote_points=belote or {side: 0 for side in TeamSide},
        card_points=card_points or {TeamSide.NS: 100, TeamSide.EW: 62},
        last_trick_side=last_trick,
        multiplier=multiplier,
    )


class _Scored:
    """The ``RoundScored`` fields the score check compares against."""

    def __init__(self, **overrides):
        from contrai_data import RoundOutcome

        self.round = 1
        self.outcome = RoundOutcome.MADE
        self.declarer = Position.NORTH
        self.contract = None
        self.taken = {TeamSide.NS: 100, TeamSide.EW: 62}
        self.belote = {side: 0 for side in TeamSide}
        self.marked = {
            TeamSide.NS: SideMark(made=100, announced=80),
            TeamSide.EW: SideMark(made=62, announced=0),
        }
        self.last_trick = TeamSide.NS
        self.slam = SlamOutcome.NONE
        for name, value in overrides.items():
            setattr(self, name, value)


def _illegal_bid() -> IllegalBidError:
    """The error ``Auction.apply`` raises, built as core builds it.

    Core's two rule errors carry diagnostics — the offending action and
    what was legal instead — so they cannot be raised from a bare
    message. Building them the real way is also what keeps these tests
    honest about what ``_classify`` is handed.
    """

    return IllegalBidError(DoubleBid(Position.WEST), ())


def _illegal_play() -> IllegalPlayError:
    """The error ``PlayState.apply`` raises, built as core builds it."""

    return IllegalPlayError(
        SPADE_7, PlayRuleViolation.MUST_FOLLOW_SUIT, (SPADE_K,)
    )


def _kinds(check: _RoundCheck) -> list[str]:
    """The classes a check reported, in the order it found them."""

    return [str(m.kind) for m in check.mismatches]


def _details(check: _RoundCheck) -> str:
    """Every detail the check reported, joined — for substring assertions."""

    return " | ".join(m.detail for m in check.mismatches)


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


class TestClassify:
    """Which class an exception out of the replay belongs to."""

    def test_an_illegal_bid_is_illegal_bid(self):
        kind, detail = _classify(_BIDDING, _illegal_bid())

        assert kind is MismatchKind.ILLEGAL_BID
        assert "refused a recorded bid" in detail

    def test_an_illegal_play_is_illegal_play(self):
        kind, detail = _classify(_PLAY, _illegal_play())

        assert kind is MismatchKind.ILLEGAL_PLAY
        assert "refused a recorded card" in detail

    def test_a_seat_mismatch_in_play_is_a_trick_winner_disagreement(self):
        # The one inference in the module: the record's next leader is
        # not the seat core says won, so the replay asked out of turn.
        kind, detail = _classify(_PLAY, SeatMismatchError("wrong seat"))

        assert kind is MismatchKind.TRICK_WINNER
        assert "not the seat core says leads" in detail

    def test_a_seat_mismatch_while_bidding_is_an_illegal_bid(self):
        kind, _ = _classify(_BIDDING, SeatMismatchError("wrong seat"))

        assert kind is MismatchKind.ILLEGAL_BID

    def test_running_past_the_record_takes_the_phase_s_class(self):
        exhausted = ScriptExhaustedError("out of actions")

        assert _classify(_PLAY, exhausted)[0] is MismatchKind.ILLEGAL_PLAY
        assert _classify(_BIDDING, exhausted)[0] is MismatchKind.ILLEGAL_BID

    def test_anything_else_still_gets_a_class(self):
        # A replay error the module does not name is still a finding, not
        # a traceback out of ``verify_game``.
        kind, detail = _classify(_PLAY, RuntimeError("something else"))

        assert kind is MismatchKind.ILLEGAL_PLAY
        assert "the replay stopped" in detail


# ---------------------------------------------------------------------------
# _same_bid
# ---------------------------------------------------------------------------


class TestSameBid:
    def test_the_same_bid_by_the_same_seat_matches(self):
        recorded = ContractBid(Position.NORTH, 80, Suit.SPADES)
        replayed = ContractBid(_Seat(Position.NORTH), 80, Suit.SPADES)

        assert _same_bid(replayed, recorded) is True

    def test_a_different_kind_does_not_match(self):
        assert (
            _same_bid(
                PassBid(_Seat(Position.NORTH)),
                ContractBid(Position.NORTH, 80, Suit.SPADES),
            )
            is False
        )

    def test_a_different_seat_does_not_match(self):
        assert (
            _same_bid(
                ContractBid(_Seat(Position.WEST), 80, Suit.SPADES),
                ContractBid(Position.NORTH, 80, Suit.SPADES),
            )
            is False
        )

    def test_a_different_value_does_not_match(self):
        assert (
            _same_bid(
                ContractBid(_Seat(Position.NORTH), 90, Suit.SPADES),
                ContractBid(Position.NORTH, 80, Suit.SPADES),
            )
            is False
        )

    def test_a_different_trump_does_not_match(self):
        assert (
            _same_bid(
                ContractBid(_Seat(Position.NORTH), 80, Suit.HEARTS),
                ContractBid(Position.NORTH, 80, Suit.SPADES),
            )
            is False
        )


# ---------------------------------------------------------------------------
# _check_auction
# ---------------------------------------------------------------------------


class TestCheckAuction:
    """The record must be a subsequence of the replayed auction."""

    @staticmethod
    def _run(recorded, replayed):
        check = _RoundCheck(_Record(auction=tuple(recorded)))
        _check_auction(check, _Round(auction=Auction(bids=tuple(replayed))))
        return check

    def test_an_identical_auction_reports_nothing(self):
        recorded = [
            ContractBid(Position.NORTH, 80, Suit.SPADES),
            PassBid(Position.WEST),
        ]
        replayed = [
            ContractBid(_Seat(Position.NORTH), 80, Suit.SPADES),
            PassBid(_Seat(Position.WEST)),
        ]

        assert self._run(recorded, replayed).mismatches == []

    def test_a_forced_pass_the_record_omits_is_not_a_fault(self):
        # The whole point of comparing as a subsequence: the engine
        # writes passes an observed table never transmits.
        recorded = [
            ContractBid(Position.NORTH, 80, Suit.SPADES),
            PassBid(Position.SOUTH),
        ]
        replayed = [
            ContractBid(_Seat(Position.NORTH), 80, Suit.SPADES),
            PassBid(_Seat(Position.EAST)),  # forced, engine-inserted
            PassBid(_Seat(Position.SOUTH)),
        ]

        assert self._run(recorded, replayed).mismatches == []

    def test_an_extra_non_pass_bid_is_a_fault(self):
        recorded = [ContractBid(Position.NORTH, 80, Suit.SPADES)]
        replayed = [
            DoubleBid(_Seat(Position.WEST)),
            ContractBid(_Seat(Position.NORTH), 80, Suit.SPADES),
        ]

        check = self._run(recorded, replayed)

        assert _kinds(check) == ["illegal_bid"]
        assert "holds a bid the record does not" in _details(check)
        assert check.mismatches[0].position == "West"

    def test_a_recorded_bid_that_never_landed_is_a_fault(self):
        recorded = [
            ContractBid(Position.NORTH, 80, Suit.SPADES),
            ContractBid(Position.SOUTH, 90, Suit.HEARTS),
        ]
        replayed = [ContractBid(_Seat(Position.NORTH), 80, Suit.SPADES)]

        check = self._run(recorded, replayed)

        assert _kinds(check) == ["illegal_bid"]
        assert "never reached the replayed auction" in _details(check)
        assert check.mismatches[0].seq == 2
        assert check.mismatches[0].expected == "90 Hearts"

    def test_it_reports_the_first_disagreement_and_stops(self):
        recorded = [
            ContractBid(Position.NORTH, 80, Suit.SPADES),
            ContractBid(Position.SOUTH, 90, Suit.HEARTS),
            ContractBid(Position.NORTH, 100, Suit.CLUBS),
        ]

        check = self._run(recorded, [])

        assert len(check.mismatches) == 1

    def test_a_round_with_no_auction_at_all_reports_the_first_bid(self):
        # ``Round.auction`` is None until bidding closes, which is the
        # state a round that failed mid-auction is left in.
        check = _RoundCheck(
            _Record(auction=(ContractBid(Position.NORTH, 80, Suit.SPADES),))
        )

        _check_auction(check, _Round(auction=None))

        assert _kinds(check) == ["illegal_bid"]

    def test_an_empty_record_auction_reports_nothing(self):
        assert self._run([], [PassBid(_Seat(Position.NORTH))]).mismatches == []


# ---------------------------------------------------------------------------
# _check_trick_winners
# ---------------------------------------------------------------------------


class TestCheckTrickWinners:
    """The cross-check behind the seat-mismatch detector."""

    @staticmethod
    def _run(replayed, recorded):
        check = _RoundCheck(_Record(trick_winners=tuple(recorded)))
        _check_trick_winners(check, _Round(winners=replayed))
        return check

    def test_matching_winners_report_nothing(self):
        winners = [Position.NORTH, Position.WEST]

        assert self._run(winners, winners).mismatches == []

    def test_a_passed_out_round_reports_nothing(self):
        check = _RoundCheck(_Record(trick_winners=()))

        _check_trick_winners(check, _Round(winners=None))

        assert check.mismatches == []

    def test_a_different_winner_names_the_trick(self):
        check = self._run(
            [Position.NORTH, Position.EAST], [Position.NORTH, Position.WEST]
        )

        assert _kinds(check) == ["trick_winner"]
        assert check.mismatches[0].trick == 2
        assert check.mismatches[0].expected == "East"
        assert check.mismatches[0].observed == "West"

    def test_it_reports_the_first_differing_trick_only(self):
        check = self._run(
            [Position.EAST, Position.EAST], [Position.WEST, Position.WEST]
        )

        assert len(check.mismatches) == 1
        assert check.mismatches[0].trick == 1

    def test_a_short_replay_reports_the_trick_counts(self):
        check = self._run([Position.NORTH], [Position.NORTH, Position.WEST])

        assert _kinds(check) == ["trick_winner"]
        assert "the replay completed 1 trick(s)" in _details(check)
        assert (check.mismatches[0].expected, check.mismatches[0].observed) == (
            "1",
            "2",
        )

    def test_a_long_replay_reports_the_trick_counts_too(self):
        check = self._run([Position.NORTH, Position.WEST], [Position.NORTH])

        assert "the record holds 1" in _details(check)


# ---------------------------------------------------------------------------
# _check_belote
# ---------------------------------------------------------------------------


class TestCheckBelote:
    @staticmethod
    def _run(hands, belotes, announced=()):
        check = _RoundCheck(_Record(hands=hands, belotes=belotes))
        check.announced = set(announced)
        _check_belote(check, _Round())
        return check

    @staticmethod
    def _claim(position=Position.NORTH, suit=Suit.SPADES, announced=False):
        return BeloteHeld(
            round=1,
            position=position,
            cards=(Card(suit, Rank.KING), Card(suit, Rank.QUEEN)),
            announced=announced,
            ts=TS,
        )

    def test_a_supported_unannounced_pair_reports_nothing(self):
        check = self._run(
            {Position.NORTH: (SPADE_K, SPADE_Q)}, (self._claim(),)
        )

        assert check.mismatches == []

    def test_a_pair_the_deal_does_not_hold_is_a_fault(self):
        check = self._run({Position.NORTH: (SPADE_K,)}, (self._claim(),))

        assert _kinds(check) == ["belote"]
        assert "the dealt hand does not hold" in _details(check)
        assert check.mismatches[0].position == "North"

    def test_a_seat_the_record_never_dealt_to_is_a_fault(self):
        check = self._run({}, (self._claim(),))

        assert _kinds(check) == ["belote"]

    def test_an_announced_pair_the_replay_announced_reports_nothing(self):
        check = self._run(
            {Position.NORTH: (SPADE_K, SPADE_Q)},
            (self._claim(announced=True),),
            announced=[(Position.NORTH, Suit.SPADES)],
        )

        assert check.mismatches == []

    def test_an_announced_pair_the_replay_never_announced_is_a_fault(self):
        check = self._run(
            {Position.NORTH: (SPADE_K, SPADE_Q)},
            (self._claim(announced=True),),
        )

        assert _kinds(check) == ["belote"]
        assert "never announced" in _details(check)

    def test_an_announcement_in_another_suit_does_not_satisfy_the_claim(self):
        check = self._run(
            {Position.NORTH: (SPADE_K, SPADE_Q)},
            (self._claim(announced=True),),
            announced=[(Position.NORTH, Suit.HEARTS)],
        )

        assert _kinds(check) == ["belote"]

    def test_an_unsupported_pair_is_reported_once_not_twice(self):
        # The deal check ``continue``s, so an unsupported pair marked
        # announced does not also trip the announcement check.
        check = self._run({}, (self._claim(announced=True),))

        assert len(check.mismatches) == 1
        assert "the dealt hand does not hold" in _details(check)

    def test_every_claimed_pair_is_checked(self):
        check = self._run(
            {Position.NORTH: (SPADE_K, SPADE_Q)},
            (
                self._claim(),
                self._claim(position=Position.WEST, suit=Suit.HEARTS),
            ),
        )

        assert len(check.mismatches) == 1
        assert check.mismatches[0].position == "West"


# ---------------------------------------------------------------------------
# _check_score
# ---------------------------------------------------------------------------


class TestCheckScore:
    @staticmethod
    def _run(recorded, *, score=None, contract=None, rules=None):
        check = _RoundCheck(_Record(score=recorded))
        _check_score(
            check,
            _Round(
                score=_score() if score is None else score,
                contract=contract,
                rules=rules,
            ),
        )
        return check

    def test_an_agreeing_score_reports_nothing(self):
        contract = _Contract(Position.NORTH)
        recorded = _Scored(
            contract=type(
                "Terms", (), {"value": 80, "suit": Suit.SPADES, "multiplier": 1}
            )()
        )

        check = self._run(recorded, contract=contract)

        assert check.mismatches == []
        assert check.unchecked == []

    def test_no_score_line_leaves_the_check_unrun(self):
        check = self._run(None)

        assert check.mismatches == []
        assert check.unchecked == ["score"]

    def test_a_record_scoring_a_round_the_replay_did_not_is_a_fault(self):
        check = _RoundCheck(_Record(score=_Scored()))

        _check_score(check, _Round(score=None))

        assert _kinds(check) == ["score"]
        assert "a round the replay did not" in _details(check)

    def test_a_different_outcome_is_a_fault(self):
        from contrai_data import RoundOutcome

        check = self._run(
            _Scored(outcome=RoundOutcome.FAILED, declarer=None),
            score=_score(made=True),
        )

        assert "the round resolved differently" in _details(check)

    def test_a_different_slam_classification_is_a_fault(self):
        check = self._run(
            _Scored(slam=SlamOutcome.UNANNOUNCED, declarer=None)
        )

        assert "slam classification differs" in _details(check)

    def test_a_different_declarer_is_a_fault(self):
        check = self._run(_Scored(declarer=Position.WEST))

        assert "the declaring seat differs" in _details(check)
        assert check.mismatches[0].observed == "West"

    def test_a_contract_on_one_side_only_is_a_fault(self):
        terms = type(
            "Terms", (), {"value": 80, "suit": Suit.SPADES, "multiplier": 1}
        )()

        check = self._run(_Scored(contract=terms))

        assert "one of the two has a contract" in _details(check)

    def test_a_replay_contract_the_record_lacks_is_a_fault_too(self):
        check = self._run(
            _Scored(declarer=Position.NORTH, contract=None),
            contract=_Contract(Position.NORTH),
        )

        assert "one of the two has a contract" in _details(check)

    def test_different_contract_terms_are_a_fault(self):
        terms = type(
            "Terms", (), {"value": 90, "suit": Suit.SPADES, "multiplier": 2}
        )()

        check = self._run(_Scored(contract=terms), contract=_Contract(Position.NORTH))

        assert "contract's terms differ" in _details(check)
        assert check.mismatches[0].expected == "80 Spades x1"
        assert check.mismatches[0].observed == "90 Spades x2"

    def test_different_marks_are_a_fault(self):
        check = self._run(
            _Scored(
                declarer=None,
                marked={
                    TeamSide.NS: SideMark(made=100, announced=90),
                    TeamSide.EW: SideMark(made=62, announced=0),
                },
            )
        )

        assert "marked points differ" in _details(check)

    def test_the_two_components_are_compared_apart(self):
        # The same total out of a different made / announced split is a
        # scoring-rule regression, not a match.
        check = self._run(
            _Scored(
                declarer=None,
                marked={
                    TeamSide.NS: SideMark(made=80, announced=100),
                    TeamSide.EW: SideMark(made=62, announced=0),
                },
            )
        )

        assert "marked points differ" in _details(check)

    def test_marks_are_compared_after_the_double_multiplier(self):
        # A record holds what the sheet says, which is already doubled; the
        # engine's Mark carries the components before the multiplier. Compared
        # raw, every doubled round in an observed record came back suspect.
        check = self._run(
            _Scored(
                declarer=None,
                marked={
                    TeamSide.NS: SideMark(made=100, announced=160),
                    TeamSide.EW: SideMark(made=62, announced=0),
                },
            ),
            score=_score(multiplier=2),
        )

        assert check.mismatches == []

    def test_a_table_doubling_the_whole_mark_multiplies_both_components(self):
        # Where the multiplier bites is the table's own convention: the
        # tournament ruleset doubles the sum, not the announced part alone.
        check = self._run(
            _Scored(
                declarer=None,
                marked={
                    TeamSide.NS: SideMark(made=200, announced=160),
                    TeamSide.EW: SideMark(made=124, announced=0),
                },
            ),
            score=_score(multiplier=2),
            rules=RuleConfig.tournament(),
        )

        assert check.mismatches == []

    def test_marks_that_still_disagree_once_multiplied_are_a_fault(self):
        check = self._run(
            _Scored(
                declarer=None,
                marked={
                    TeamSide.NS: SideMark(made=100, announced=80),
                    TeamSide.EW: SideMark(made=62, announced=0),
                },
            ),
            score=_score(multiplier=2),
        )

        assert "marked points differ" in _details(check)

    def test_different_card_points_are_a_fault(self):
        check = self._run(
            _Scored(declarer=None, taken={TeamSide.NS: 99, TeamSide.EW: 63})
        )

        assert "captured card points differ" in _details(check)

    def test_different_belote_points_are_a_fault(self):
        check = self._run(
            _Scored(declarer=None, belote={TeamSide.NS: 20, TeamSide.EW: 0})
        )

        assert "belote points differ" in _details(check)

    def test_a_different_last_trick_side_is_a_fault(self):
        check = self._run(_Scored(declarer=None, last_trick=TeamSide.EW))

        assert "last trick went to another side" in _details(check)

    def test_a_last_trick_the_record_never_states_is_unchecked(self):
        # An observed source may name no side at all: a table that folds the
        # ten-point bonus into the row's card points states the points and
        # nothing else. Those points are compared above, so the bonus is still
        # checked — but a claim never made cannot disagree with the replay.
        check = self._run(_Scored(declarer=None, last_trick=None))

        assert check.mismatches == []
        assert check.unchecked == ["score"]

    def test_two_silences_about_the_last_trick_agree(self):
        # A round whose tricks were never played has no last trick on either
        # side, which is agreement rather than something left unchecked.
        check = self._run(
            _Scored(declarer=None, last_trick=None), score=_score(last_trick=None)
        )

        assert (check.mismatches, check.unchecked) == ([], [])

    def test_every_disagreeing_field_is_reported(self):
        # The score check does not stop at the first fault: a reader
        # wants the whole picture of a divergent score line.
        check = self._run(
            _Scored(
                declarer=Position.WEST,
                taken={TeamSide.NS: 0, TeamSide.EW: 162},
                last_trick=TeamSide.EW,
            )
        )

        assert len(check.mismatches) >= 3
        assert set(_kinds(check)) == {"score"}


# ---------------------------------------------------------------------------
# _taken_agrees
# ---------------------------------------------------------------------------


class TestTakenAgrees:
    """§4.2.1: a sweep may be recorded as 250 rather than the real pile."""

    def test_identical_piles_agree(self):
        assert (
            _taken_agrees(
                _score(card_points={TeamSide.NS: 100, TeamSide.EW: 62}),
                _Scored(taken={TeamSide.NS: 100, TeamSide.EW: 62}),
                SlamOutcome.NONE,
            )
            is True
        )

    def test_different_piles_on_an_ordinary_round_disagree(self):
        assert (
            _taken_agrees(
                _score(card_points={TeamSide.NS: 100, TeamSide.EW: 62}),
                _Scored(taken={TeamSide.NS: 99, TeamSide.EW: 63}),
                SlamOutcome.NONE,
            )
            is False
        )

    def test_a_sweep_may_state_the_substitute_instead_of_the_pile(self):
        assert (
            _taken_agrees(
                _score(card_points={TeamSide.NS: 0, TeamSide.EW: 162}),
                _Scored(taken={TeamSide.NS: 0, TeamSide.EW: 250}),
                SlamOutcome.SLAM,
            )
            is True
        )

    def test_the_substitute_must_sit_on_the_sweeping_side(self):
        assert (
            _taken_agrees(
                _score(card_points={TeamSide.NS: 0, TeamSide.EW: 162}),
                _Scored(taken={TeamSide.NS: 250, TeamSide.EW: 0}),
                SlamOutcome.SLAM,
            )
            is False
        )

    def test_a_sweep_with_some_other_number_still_disagrees(self):
        assert (
            _taken_agrees(
                _score(card_points={TeamSide.NS: 0, TeamSide.EW: 162}),
                _Scored(taken={TeamSide.NS: 0, TeamSide.EW: 500}),
                SlamOutcome.SLAM,
            )
            is False
        )

    def test_a_sweep_nobody_took_points_in_disagrees(self):
        # Unreachable from a played round and therefore exactly the kind
        # of malformed input that must not crash the sweeper lookup.
        assert (
            _taken_agrees(
                _score(card_points={TeamSide.NS: 0, TeamSide.EW: 0}),
                _Scored(taken={TeamSide.NS: 0, TeamSide.EW: 250}),
                SlamOutcome.SLAM,
            )
            is False
        )


# ---------------------------------------------------------------------------
# The observer's own guards
# ---------------------------------------------------------------------------


class TestObserverGuards:
    """Hooks that fire outside a scripted round must be no-ops."""

    @staticmethod
    def _observer():
        from contrai_core import RuleConfig

        from contrai_engine.replay.verify import VerifyingObserver

        record = type(
            "Rec",
            (),
            {
                "rounds": (),
                "preset": "classic",
                "ruleset": RuleConfig(),
                "header": type("H", (), {"game_id": "x", "source": "engine"})(),
            },
        )()
        return VerifyingObserver(record)

    def test_round_complete_outside_a_round_records_nothing(self):
        observer = self._observer()

        observer.on_round_complete(_Round(), {})

        assert observer.rounds == []

    def test_contract_established_outside_a_round_is_a_no_op(self):
        observer = self._observer()

        observer.on_contract_established(_Round())

        assert observer.rounds == []

    def test_belote_announced_outside_a_round_is_a_no_op(self):
        observer = self._observer()

        observer.on_belote_announced(
            _Seat(Position.NORTH), "belote", Suit.SPADES, _Round()
        )

        assert observer.rounds == []

    def test_a_failure_before_any_round_still_produces_a_verdict(self):
        observer = self._observer()

        observer.round_failed(_Record(number=4), _illegal_bid())

        assert [r.number for r in observer.rounds] == [4]
        assert str(observer.rounds[0].verdict) == "suspect"
