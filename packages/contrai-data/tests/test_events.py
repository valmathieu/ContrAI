"""Pins the record vocabulary: the closed enums and each event's invariants."""

import dataclasses

import pytest
from contrai_core import (
    Card,
    ContraiError,
    ContractBid,
    PassBid,
    Position,
    Rank,
    RuleConfig,
    Suit,
    TeamSide,
)

from contrai_data import (
    BeloteHeld,
    BidMade,
    CardPlayed,
    ContractTerms,
    EndReason,
    GameEnded,
    GameStarted,
    HandsDerivation,
    Header,
    JoinPhase,
    ObservedFrom,
    RecordError,
    RecordFormatError,
    RecordSource,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    Ruleset,
    ScoreSource,
    Seat,
    SeatKind,
    SideMark,
    SlamOutcome,
    UnsupportedFormatError,
)

TS = "2026-09-10T18:18:15Z"


def seat(kind: SeatKind = SeatKind.AI) -> Seat:
    return Seat(id=None, name="ai:expert", account=None, kind=kind, level=None)


def four_seats() -> dict[Position, Seat]:
    return {position: seat() for position in Position}


def deal() -> dict[Position, tuple[Card, ...]]:
    """The 32-card deck split eight per seat, one suit each."""
    return {
        position: tuple(Card(suit, rank) for rank in Rank)
        for position, suit in zip(Position, Suit, strict=True)
    }


class TestErrors:
    @pytest.mark.parametrize(
        "error", [RecordError, RecordFormatError, UnsupportedFormatError]
    )
    def test_is_both_a_contrai_error_and_a_value_error(self, error):
        # Same dual-base invariant contrai-core asserts for its own family:
        # one ``except ContraiError`` catches everything, and a plain
        # ``except ValueError`` still catches each member.
        assert issubclass(error, ContraiError)
        assert issubclass(error, ValueError)


class TestVocabularies:
    @pytest.mark.parametrize(
        "enum_cls, expected",
        [
            (RecordSource, {"ENGINE": "engine", "OBSERVED": "observed"}),
            (SeatKind, {"HUMAN": "human", "AI": "ai", "OBSERVED": "observed"}),
            (JoinPhase, {"BIDDING": "bidding", "PLAY": "play"}),
            (
                HandsDerivation,
                {
                    "SELF_PLAY": "self_play",
                    "OBSERVED": "observed",
                    "DEALT_FROM_DECK": "dealt_from_deck",
                },
            ),
            (
                RoundOutcome,
                {
                    "MADE": "made",
                    "FAILED": "failed",
                    "ALL_PASS": "all_pass",
                    "DISPUTED": "disputed",
                    "HELD": "held",
                },
            ),
            (
                SlamOutcome,
                {
                    "NONE": "none",
                    "SLAM": "slam",
                    "SOLO_SLAM": "solo_slam",
                    "UNANNOUNCED": "unannounced",
                },
            ),
            (ScoreSource, {"ENGINE": "engine", "SNAPSHOT": "snapshot", "PANEL": "panel"}),
            (
                EndReason,
                {
                    "TARGET_REACHED": "target_reached",
                    "ABANDONED": "abandoned",
                    "OBSERVER_LEFT": "observer_left",
                    "INTERRUPTED": "interrupted",
                },
            ),
        ],
    )
    def test_members_and_values(self, enum_cls, expected):
        assert {m.name: m.value for m in enum_cls} == expected

    @pytest.mark.parametrize(
        "member",
        [
            *RecordSource,
            *SeatKind,
            *JoinPhase,
            *HandsDerivation,
            *RoundOutcome,
            *SlamOutcome,
            *ScoreSource,
            *EndReason,
        ],
    )
    def test_str_is_the_record_token(self, member):
        # House style (contrai_core.types): an f-string renders the token.
        assert f"{member}" == member.value


class TestImmutability:
    @pytest.mark.parametrize(
        "event, field",
        [
            (
                Header(
                    format="contrai-record/1",
                    source=RecordSource.ENGINE,
                    generator="contrai-engine 0.4.0",
                    game_id="engine-20260910T181815Z-a1b2c3",
                    created_at=TS,
                ),
                "created_at",
            ),
            (
                GameEnded(
                    totals=None, winner=None, reason=EndReason.ABANDONED, ts=None
                ),
                "ts",
            ),
        ],
    )
    def test_events_are_frozen(self, event, field):
        # The field has to be one the event actually declares: a frozen
        # dataclass with ``slots=True`` answers an *unknown* attribute with
        # a ``TypeError`` from its regenerated ``__setattr__``, not with
        # ``FrozenInstanceError``, so naming a stray field would pass this
        # test for entirely the wrong reason.
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(event, field, TS)


class TestGameStarted:
    def test_accepts_four_seats(self):
        started = GameStarted(
            ruleset=Ruleset(preset="classic", config=RuleConfig()),
            seats=four_seats(),
            observed_from=None,
            ts=TS,
        )
        assert set(started.seats) == set(Position)

    def test_refuses_a_missing_seat(self):
        seats = four_seats()
        del seats[Position.EAST]
        with pytest.raises(RecordFormatError, match="four seats"):
            GameStarted(
                ruleset=Ruleset(preset="classic", config=RuleConfig()),
                seats=seats,
                observed_from=None,
                ts=TS,
            )

    def test_carries_where_we_joined(self):
        started = GameStarted(
            ruleset=Ruleset(preset="tournament", config=RuleConfig()),
            seats=four_seats(),
            observed_from=ObservedFrom(
                round=2, phase=JoinPhase.PLAY, totals={TeamSide.NS: 320, TeamSide.EW: 0}
            ),
            ts=TS,
        )
        assert started.observed_from.phase is JoinPhase.PLAY


class TestRoundDealt:
    def test_accepts_a_full_deal(self):
        dealt = RoundDealt(
            round=1,
            dealer=Position.EAST,
            hands=deal(),
            hands_derivation=HandsDerivation.SELF_PLAY,
            ts=TS,
        )
        assert len(dealt.hands[Position.NORTH]) == 8

    def test_refuses_a_short_hand(self):
        hands = deal()
        hands[Position.NORTH] = hands[Position.NORTH][:7]
        with pytest.raises(RecordFormatError, match="eight cards"):
            RoundDealt(
                round=1,
                dealer=Position.EAST,
                hands=hands,
                hands_derivation=HandsDerivation.OBSERVED,
                ts=TS,
            )

    def test_refuses_a_duplicated_card(self):
        # The check that catches a mis-decoded deal: 32 slots, 32 distinct
        # cards. A parser bug that repeats a packet lands here, not three
        # steps later in a legality check.
        hands = deal()
        hands[Position.NORTH] = hands[Position.WEST]
        with pytest.raises(RecordFormatError, match="32 distinct"):
            RoundDealt(
                round=1,
                dealer=Position.EAST,
                hands=hands,
                hands_derivation=HandsDerivation.OBSERVED,
                ts=TS,
            )

    def test_refuses_a_missing_seat(self):
        hands = deal()
        del hands[Position.SOUTH]
        with pytest.raises(RecordFormatError, match="four seats"):
            RoundDealt(
                round=1,
                dealer=Position.EAST,
                hands=hands,
                hands_derivation=HandsDerivation.OBSERVED,
                ts=TS,
            )


class TestBidMade:
    def test_files_a_bid_under_its_own_seat(self):
        made = BidMade(
            round=1,
            seq=1,
            position=Position.NORTH,
            bid=PassBid(player=Position.NORTH),
            think_ms=None,
            ts=TS,
        )
        assert made.bid.player is Position.NORTH

    def test_refuses_a_bid_filed_under_another_seat(self):
        with pytest.raises(RecordFormatError, match="seat"):
            BidMade(
                round=1,
                seq=1,
                position=Position.NORTH,
                bid=PassBid(player=Position.SOUTH),
                think_ms=None,
                ts=TS,
            )

    def test_refuses_a_non_positive_sequence(self):
        with pytest.raises(RecordFormatError, match="1-based"):
            BidMade(
                round=1,
                seq=0,
                position=Position.NORTH,
                bid=PassBid(player=Position.NORTH),
                think_ms=None,
                ts=TS,
            )

    def test_carries_a_contract_bid(self):
        bid = ContractBid(player=Position.WEST, value=120, suit=Suit.HEARTS)
        made = BidMade(
            round=1, seq=3, position=Position.WEST, bid=bid, think_ms=1400, ts=TS
        )
        assert made.think_ms == 1400


class TestCardPlayed:
    def test_accepts_a_trick_on_the_ladder(self):
        played = CardPlayed(
            round=1,
            trick=8,
            position=Position.NORTH,
            card=Card(Suit.SPADES, Rank.ACE),
            derived=True,
            think_ms=None,
            ts=TS,
        )
        assert played.derived is True

    @pytest.mark.parametrize("trick", [0, 9])
    def test_refuses_a_trick_off_the_ladder(self, trick):
        with pytest.raises(RecordFormatError, match="1 to 8"):
            CardPlayed(
                round=1,
                trick=trick,
                position=Position.NORTH,
                card=Card(Suit.SPADES, Rank.ACE),
                derived=False,
                think_ms=None,
                ts=TS,
            )


class TestBeloteHeld:
    def test_accepts_a_king_and_queen_of_one_suit(self):
        held = BeloteHeld(
            round=4,
            position=Position.SOUTH,
            cards=(Card(Suit.HEARTS, Rank.KING), Card(Suit.HEARTS, Rank.QUEEN)),
            announced=True,
            ts=TS,
        )
        assert held.announced is True

    def test_possession_without_a_declaration_is_null(self):
        held = BeloteHeld(
            round=4,
            position=Position.SOUTH,
            cards=(Card(Suit.HEARTS, Rank.KING), Card(Suit.HEARTS, Rank.QUEEN)),
            announced=None,
            ts=TS,
        )
        assert held.announced is None

    @pytest.mark.parametrize(
        "cards",
        [
            (Card(Suit.HEARTS, Rank.KING),),
            (Card(Suit.HEARTS, Rank.KING), Card(Suit.SPADES, Rank.QUEEN)),
            (Card(Suit.HEARTS, Rank.KING), Card(Suit.HEARTS, Rank.JACK)),
        ],
    )
    def test_refuses_anything_that_is_not_a_belote(self, cards):
        with pytest.raises(RecordFormatError, match="King and the Queen"):
            BeloteHeld(
                round=4, position=Position.SOUTH, cards=cards, announced=True, ts=TS
            )


def scored(**overrides) -> RoundScored:
    base = dict(
        round=1,
        outcome=RoundOutcome.MADE,
        declarer=Position.NORTH,
        contract=ContractTerms(value=80, suit=Suit.SPADES, multiplier=1),
        taken={TeamSide.NS: 162, TeamSide.EW: 0},
        belote={TeamSide.NS: 0, TeamSide.EW: 0},
        announcements={TeamSide.NS: 0, TeamSide.EW: 0},
        carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
        marked={
            TeamSide.NS: SideMark(made=162, announced=80),
            TeamSide.EW: SideMark(made=0, announced=0),
        },
        totals={TeamSide.NS: 242, TeamSide.EW: 0},
        last_trick=TeamSide.NS,
        slam=SlamOutcome.UNANNOUNCED,
        source=ScoreSource.ENGINE,
        ts=TS,
    )
    return RoundScored(**(base | overrides))


class TestRoundScored:
    def test_accepts_a_held_round(self):
        assert scored(outcome=RoundOutcome.HELD).outcome is RoundOutcome.HELD

    def test_accepts_an_unknown_carry(self):
        assert scored(carried_over=None).carried_over is None

    def test_a_stated_carry_still_names_both_sides(self):
        with pytest.raises(RecordFormatError, match="carried_over"):
            scored(carried_over={TeamSide.NS: 0})

    def test_accepts_a_made_contract(self):
        assert scored().marked[TeamSide.NS].announced == 80

    def test_accepts_an_all_pass_round(self):
        event = scored(
            outcome=RoundOutcome.ALL_PASS,
            declarer=None,
            contract=None,
            taken={TeamSide.NS: 0, TeamSide.EW: 0},
            marked={
                TeamSide.NS: SideMark(0, 0),
                TeamSide.EW: SideMark(0, 0),
            },
            totals=None,
            last_trick=None,
            slam=SlamOutcome.NONE,
            source=ScoreSource.SNAPSHOT,
        )
        assert event.contract is None

    def test_refuses_a_contract_without_a_declarer(self):
        with pytest.raises(RecordFormatError, match="declarer"):
            scored(declarer=None)

    def test_refuses_a_declarer_without_a_contract(self):
        with pytest.raises(RecordFormatError, match="declarer"):
            scored(contract=None)

    def test_refuses_an_all_pass_round_that_names_a_contract(self):
        with pytest.raises(RecordFormatError, match="all_pass"):
            scored(outcome=RoundOutcome.ALL_PASS)

    def test_refuses_a_contracted_round_marked_all_pass(self):
        with pytest.raises(RecordFormatError, match="all_pass"):
            scored(outcome=RoundOutcome.MADE, declarer=None, contract=None)

    def test_refuses_a_one_sided_component(self):
        with pytest.raises(RecordFormatError, match="both sides"):
            scored(taken={TeamSide.NS: 162})

    def test_refuses_a_one_sided_total(self):
        with pytest.raises(RecordFormatError, match="both sides"):
            scored(totals={TeamSide.NS: 242})


class TestObservedFrom:
    def test_refuses_a_one_sided_total(self):
        with pytest.raises(RecordFormatError, match="both sides"):
            ObservedFrom(
                round=2, phase=JoinPhase.PLAY, totals={TeamSide.NS: 320}
            )


class TestGameEnded:
    def test_a_game_we_watched_to_the_end(self):
        ended = GameEnded(
            totals={TeamSide.NS: 1386, TeamSide.EW: 1742},
            winner=TeamSide.EW,
            reason=EndReason.TARGET_REACHED,
            ts=TS,
        )
        assert ended.winner is TeamSide.EW

    def test_a_game_we_left_carries_neither_totals_nor_a_timestamp(self):
        # The wire's end-of-game event carries no scores, and a game we walked
        # away from has no end timestamp at all (spec §4.2.1 D3).
        ended = GameEnded(
            totals=None, winner=None, reason=EndReason.OBSERVER_LEFT, ts=None
        )
        assert (ended.totals, ended.winner, ended.ts) == (None, None, None)

    def test_refuses_a_one_sided_total(self):
        with pytest.raises(RecordFormatError, match="both sides"):
            GameEnded(
                totals={TeamSide.NS: 1386},
                winner=TeamSide.NS,
                reason=EndReason.TARGET_REACHED,
                ts=TS,
            )
