"""Pins the on-disk line: one event in, one JSON object out, and back."""

import json

import pytest
from contrai_core import (
    Card,
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    Rank,
    RedoubleBid,
    RuleConfig,
    SlamLevel,
    Suit,
    TeamSide,
    TrumpVariant,
)

from contrai_data import (
    FORMAT,
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
    decode,
    encode,
)

TS = "2026-09-10T18:18:15Z"

HEADER = Header(
    format=FORMAT,
    source=RecordSource.ENGINE,
    generator="contrai-engine 0.4.0",
    game_id="engine-20260910T181815Z-a1b2c3",
    created_at=TS,
)

STARTED = GameStarted(
    ruleset=Ruleset(preset="classic", config=RuleConfig()),
    seats={
        position: Seat(
            id=None, name="ai:expert", account=None, kind=SeatKind.AI, level=None
        )
        for position in Position
    },
    observed_from=None,
    ts=TS,
)

OBSERVED_START = GameStarted(
    ruleset=Ruleset(preset="tournament", config=RuleConfig()),
    seats={
        position: Seat(
            id="881204",
            name="Babette",
            account="acct-1",
            kind=SeatKind.OBSERVED,
            level="1",
        )
        for position in Position
    },
    observed_from=ObservedFrom(
        round=2, phase=JoinPhase.PLAY, totals={TeamSide.NS: 320, TeamSide.EW: 0}
    ),
    ts=TS,
)

DEALT = RoundDealt(
    round=1,
    dealer=Position.EAST,
    hands={
        position: tuple(Card(suit, rank) for rank in Rank)
        for position, suit in zip(Position, Suit, strict=True)
    },
    hands_derivation=HandsDerivation.SELF_PLAY,
    ts=TS,
)

SCORED = RoundScored(
    round=1,
    outcome=RoundOutcome.MADE,
    declarer=Position.NORTH,
    contract=ContractTerms(value=80, suit=Suit.SPADES, multiplier=1),
    taken={TeamSide.NS: 162, TeamSide.EW: 0},
    belote={TeamSide.NS: 20, TeamSide.EW: 0},
    announcements={TeamSide.NS: 0, TeamSide.EW: 0},
    carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
    marked={TeamSide.NS: SideMark(162, 80), TeamSide.EW: SideMark(0, 0)},
    totals={TeamSide.NS: 262, TeamSide.EW: 0},
    last_trick=TeamSide.NS,
    slam=SlamOutcome.UNANNOUNCED,
    source=ScoreSource.ENGINE,
    ts=TS,
)

ALL_PASS = RoundScored(
    round=2,
    outcome=RoundOutcome.ALL_PASS,
    declarer=None,
    contract=None,
    taken={TeamSide.NS: 0, TeamSide.EW: 0},
    belote={TeamSide.NS: 0, TeamSide.EW: 0},
    announcements={TeamSide.NS: 0, TeamSide.EW: 0},
    carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
    marked={TeamSide.NS: SideMark(0, 0), TeamSide.EW: SideMark(0, 0)},
    totals=None,
    last_trick=None,
    slam=SlamOutcome.NONE,
    source=ScoreSource.SNAPSHOT,
    ts=TS,
)

SLAM_SCORED = RoundScored(
    round=3,
    outcome=RoundOutcome.FAILED,
    declarer=Position.EAST,
    contract=ContractTerms(
        value=SlamLevel.SOLO_SLAM, suit=TrumpVariant.NO_TRUMP, multiplier=4
    ),
    taken={TeamSide.NS: 82, TeamSide.EW: 80},
    belote={TeamSide.NS: 0, TeamSide.EW: 0},
    announcements={TeamSide.NS: 0, TeamSide.EW: 0},
    carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
    marked={TeamSide.NS: SideMark(0, 500), TeamSide.EW: SideMark(0, 0)},
    totals={TeamSide.NS: 762, TeamSide.EW: 0},
    last_trick=TeamSide.EW,
    slam=SlamOutcome.SOLO_SLAM,
    source=ScoreSource.PANEL,
    ts=TS,
)

HELD_SCORED = RoundScored(
    round=4,
    outcome=RoundOutcome.HELD,
    declarer=Position.WEST,
    contract=ContractTerms(value=80, suit=Suit.DIAMONDS, multiplier=1),
    taken={TeamSide.NS: 81, TeamSide.EW: 81},
    belote={TeamSide.NS: 0, TeamSide.EW: 0},
    announcements={TeamSide.NS: 0, TeamSide.EW: 0},
    carried_over=None,
    marked={TeamSide.NS: SideMark(81, 0), TeamSide.EW: SideMark(0, 0)},
    totals={TeamSide.NS: 129, TeamSide.EW: 496},
    last_trick=None,
    slam=SlamOutcome.NONE,
    source=ScoreSource.SNAPSHOT,
    ts=TS,
)

EVERY_EVENT = [
    HEADER,
    STARTED,
    OBSERVED_START,
    DEALT,
    BidMade(
        round=1, seq=1, position=Position.NORTH,
        bid=PassBid(player=Position.NORTH), think_ms=None, ts=TS,
    ),
    BidMade(
        round=1, seq=2, position=Position.WEST,
        bid=ContractBid(player=Position.WEST, value=120, suit=Suit.HEARTS),
        think_ms=1400, ts=TS,
    ),
    BidMade(
        round=1, seq=3, position=Position.SOUTH,
        bid=ContractBid(
            player=Position.SOUTH, value=SlamLevel.SOLO_SLAM, suit=TrumpVariant.ALL_TRUMP
        ),
        think_ms=0, ts=TS,
    ),
    BidMade(
        round=1, seq=4, position=Position.EAST,
        bid=DoubleBid(player=Position.EAST), think_ms=None, ts=TS,
    ),
    BidMade(
        round=1, seq=5, position=Position.SOUTH,
        bid=RedoubleBid(player=Position.SOUTH), think_ms=None, ts=TS,
    ),
    CardPlayed(
        round=1, trick=1, position=Position.NORTH,
        card=Card(Suit.SPADES, Rank.TEN), derived=False, think_ms=900, ts=TS,
    ),
    CardPlayed(
        round=1, trick=8, position=Position.NORTH,
        card=Card(Suit.SPADES, Rank.SEVEN), derived=True, think_ms=None, ts=TS,
    ),
    BeloteHeld(
        round=1, position=Position.NORTH,
        cards=(Card(Suit.SPADES, Rank.KING), Card(Suit.SPADES, Rank.QUEEN)),
        announced=True, ts=TS,
    ),
    BeloteHeld(
        round=1, position=Position.SOUTH,
        cards=(Card(Suit.DIAMONDS, Rank.KING), Card(Suit.DIAMONDS, Rank.QUEEN)),
        announced=None, ts=TS,
    ),
    SCORED,
    ALL_PASS,
    SLAM_SCORED,
    HELD_SCORED,
    GameEnded(
        totals={TeamSide.NS: 1386, TeamSide.EW: 1742},
        winner=TeamSide.EW, reason=EndReason.TARGET_REACHED, ts=TS,
    ),
    GameEnded(totals=None, winner=None, reason=EndReason.OBSERVER_LEFT, ts=None),
]


class TestRoundTrip:
    @pytest.mark.parametrize("event", EVERY_EVENT, ids=lambda e: type(e).__name__)
    def test_every_event_survives_a_round_trip(self, event):
        assert decode(encode(event)) == event

    @pytest.mark.parametrize("event", EVERY_EVENT, ids=lambda e: type(e).__name__)
    def test_a_line_is_one_json_object_on_one_line(self, event):
        line = encode(event)
        assert "\n" not in line
        assert isinstance(json.loads(line), dict)

    def test_the_event_name_is_the_first_key(self):
        # Reading a record with ``head`` should say what each line is
        # without scrolling.
        assert encode(DEALT).startswith('{"event": "round_dealt"')

    def test_non_ascii_names_are_written_as_themselves(self):
        seat = Seat(
            id="1", name="Zoé", account=None, kind=SeatKind.OBSERVED, level=None
        )
        started = GameStarted(
            ruleset=Ruleset(preset="tournament", config=RuleConfig()),
            seats={position: seat for position in Position},
            observed_from=None,
            ts=TS,
        )
        assert "Zoé" in encode(started)

    def test_refuses_to_encode_something_that_is_not_an_event(self):
        with pytest.raises(RecordFormatError, match="event type"):
            encode({"event": "round_dealt"})


class TestFormatGate:
    def test_the_current_format_is_accepted(self):
        assert decode(encode(HEADER)).format == FORMAT

    @pytest.mark.parametrize("value", ["contrai-record/3", "contrai-record/0"])
    def test_an_unknown_major_is_refused(self, value):
        line = json.dumps({**json.loads(encode(HEADER)), "format": value})
        with pytest.raises(UnsupportedFormatError, match="major"):
            decode(line)

    def test_a_first_major_record_still_reads(self):
        line = json.dumps({**json.loads(encode(HEADER)), "format": "contrai-record/1"})
        assert decode(line).format == "contrai-record/1"

    def test_this_build_writes_the_second_major(self):
        assert FORMAT == "contrai-record/2"

    @pytest.mark.parametrize("value", ["some-other-format/1"])
    def test_an_unknown_family_is_refused(self, value):
        line = json.dumps({**json.loads(encode(HEADER)), "format": value})
        with pytest.raises(UnsupportedFormatError, match="format"):
            decode(line)

    @pytest.mark.parametrize(
        "value", ["contrai-record", "contrai-record/one", 1, "a/b/1"]
    )
    def test_a_malformed_format_is_refused(self, value):
        line = json.dumps({**json.loads(encode(HEADER)), "format": value})
        with pytest.raises(RecordFormatError, match="format"):
            decode(line)


class TestRefusals:
    @pytest.mark.parametrize(
        "line",
        ['{"event": "round_dea', "not json at all", "", "   "],
    )
    def test_a_line_that_is_not_json_is_refused(self, line):
        with pytest.raises(RecordFormatError, match="JSON"):
            decode(line)

    @pytest.mark.parametrize("line", ["[]", '"round_dealt"', "42"])
    def test_a_line_that_is_not_an_object_is_refused(self, line):
        with pytest.raises(RecordFormatError, match="object"):
            decode(line)

    def test_a_line_without_an_event_name_is_refused(self):
        with pytest.raises(RecordFormatError, match="event"):
            decode('{"round": 1}')

    def test_an_unknown_event_name_is_refused(self):
        with pytest.raises(RecordFormatError, match="chelem"):
            decode('{"event": "chelem", "round": 1}')

    def test_a_missing_field_is_refused(self):
        payload = json.loads(encode(DEALT))
        del payload["dealer"]
        with pytest.raises(RecordFormatError, match="dealer"):
            decode(json.dumps(payload))

    def test_an_unknown_field_is_refused(self):
        # A producer writing a field this build ignores is a producer this
        # build cannot fully read.
        payload = json.loads(encode(DEALT)) | {"shuffled": True}
        with pytest.raises(RecordFormatError, match="shuffled"):
            decode(json.dumps(payload))

    def test_a_bad_token_inside_a_field_is_refused(self):
        payload = json.loads(encode(DEALT))
        payload["dealer"] = "X"
        with pytest.raises(RecordFormatError, match="seat token"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("value", [True, "1", 1.5, None])
    def test_a_round_number_that_is_not_an_int_is_refused(self, value):
        # ``True`` is an int to ``isinstance``, so the guard is on the exact
        # type — otherwise a JSON ``true`` becomes round 1.
        payload = json.loads(encode(DEALT)) | {"round": value}
        with pytest.raises(RecordFormatError, match="round"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("value", [1, "true"])
    def test_a_derived_flag_that_is_not_a_bool_is_refused(self, value):
        played = json.loads(encode(EVERY_EVENT[9])) | {"derived": value}
        with pytest.raises(RecordFormatError, match="derived"):
            decode(json.dumps(played))

    def test_a_think_ms_that_is_not_an_int_is_refused(self):
        payload = json.loads(encode(EVERY_EVENT[4])) | {"think_ms": "fast"}
        with pytest.raises(RecordFormatError, match="think_ms"):
            decode(json.dumps(payload))

    def test_an_optional_string_that_is_not_a_string_is_refused(self):
        payload = json.loads(encode(OBSERVED_START))
        payload["seats"]["N"]["level"] = 1
        with pytest.raises(RecordFormatError, match="level"):
            decode(json.dumps(payload))

    def test_a_seats_map_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(STARTED)) | {"seats": ["N", "W", "S", "E"]}
        with pytest.raises(RecordFormatError, match="seats"):
            decode(json.dumps(payload))

    def test_a_seat_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(STARTED))
        payload["seats"]["N"] = "ai:expert"
        with pytest.raises(RecordFormatError, match="seat"):
            decode(json.dumps(payload))

    def test_a_hand_that_is_not_a_list_is_refused(self):
        payload = json.loads(encode(DEALT))
        payload["hands"]["N"] = "10S JH"
        with pytest.raises(RecordFormatError, match="hand"):
            decode(json.dumps(payload))

    def test_an_unknown_enum_token_is_refused(self):
        payload = json.loads(encode(DEALT)) | {"hands_derivation": "guessed"}
        with pytest.raises(RecordFormatError, match="hands_derivation"):
            decode(json.dumps(payload))

    def test_a_side_keyed_field_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(SCORED)) | {"taken": [162, 0]}
        with pytest.raises(RecordFormatError, match="taken"):
            decode(json.dumps(payload))

    def test_a_mark_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(SCORED))
        payload["marked"]["NS"] = 162
        with pytest.raises(RecordFormatError, match="marked"):
            decode(json.dumps(payload))

    def test_a_contract_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(SCORED)) | {"contract": "80S"}
        with pytest.raises(RecordFormatError, match="contract"):
            decode(json.dumps(payload))

    def test_an_observed_from_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(OBSERVED_START)) | {"observed_from": 2}
        with pytest.raises(RecordFormatError, match="observed_from"):
            decode(json.dumps(payload))

    def test_a_ruleset_that_is_not_a_mapping_is_refused(self):
        payload = json.loads(encode(STARTED)) | {"ruleset": "classic"}
        with pytest.raises(RecordFormatError, match="ruleset"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("drop", ["preset", "config"])
    def test_a_ruleset_missing_half_of_itself_is_refused(self, drop):
        payload = json.loads(encode(STARTED))
        del payload["ruleset"][drop]
        with pytest.raises(RecordFormatError, match="ruleset"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("drop", ["id", "name", "account", "kind", "level"])
    def test_a_seat_missing_a_field_is_refused(self, drop):
        payload = json.loads(encode(STARTED))
        del payload["seats"]["N"][drop]
        with pytest.raises(RecordFormatError, match="seat"):
            decode(json.dumps(payload))

    def test_a_required_string_that_is_not_a_string_is_refused(self):
        payload = json.loads(encode(HEADER)) | {"generator": 4}
        with pytest.raises(RecordFormatError, match="generator"):
            decode(json.dumps(payload))

    def test_an_enum_token_that_is_not_a_string_is_refused(self):
        payload = json.loads(encode(DEALT)) | {"hands_derivation": 1}
        with pytest.raises(RecordFormatError, match="hands_derivation"):
            decode(json.dumps(payload))

    def test_a_side_keyed_value_that_is_not_a_number_is_refused(self):
        payload = json.loads(encode(SCORED))
        payload["taken"]["NS"] = "162"
        with pytest.raises(RecordFormatError, match="taken"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("drop", ["round", "phase", "totals"])
    def test_an_observed_from_missing_a_field_is_refused(self, drop):
        payload = json.loads(encode(OBSERVED_START))
        del payload["observed_from"][drop]
        with pytest.raises(RecordFormatError, match="observed_from"):
            decode(json.dumps(payload))

    def test_an_observed_from_round_that_is_not_an_int_is_refused(self):
        payload = json.loads(encode(OBSERVED_START))
        payload["observed_from"]["round"] = "2"
        with pytest.raises(RecordFormatError, match="observed_from"):
            decode(json.dumps(payload))

    def test_belote_cards_that_are_not_a_list_is_refused(self):
        payload = json.loads(encode(EVERY_EVENT[11])) | {"cards": "KSQS"}
        with pytest.raises(RecordFormatError, match="cards"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("drop", ["value", "suit", "multiplier"])
    def test_a_contract_missing_a_term_is_refused(self, drop):
        payload = json.loads(encode(SCORED))
        del payload["contract"][drop]
        with pytest.raises(RecordFormatError, match="contract"):
            decode(json.dumps(payload))

    def test_a_contract_multiplier_that_is_not_an_int_is_refused(self):
        payload = json.loads(encode(SCORED))
        payload["contract"]["multiplier"] = "1"
        with pytest.raises(RecordFormatError, match="multiplier"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("drop", ["made", "announced"])
    def test_a_mark_missing_a_half_is_refused(self, drop):
        payload = json.loads(encode(SCORED))
        del payload["marked"]["NS"][drop]
        with pytest.raises(RecordFormatError, match="marked"):
            decode(json.dumps(payload))

    @pytest.mark.parametrize("part", ["made", "announced"])
    def test_a_mark_half_that_is_not_an_int_is_refused(self, part):
        payload = json.loads(encode(SCORED))
        payload["marked"]["NS"][part] = "162"
        with pytest.raises(RecordFormatError, match=part):
            decode(json.dumps(payload))

    def test_an_announced_flag_that_is_not_a_bool_is_refused(self):
        payload = json.loads(encode(EVERY_EVENT[11])) | {"announced": "yes"}
        with pytest.raises(RecordFormatError, match="announced"):
            decode(json.dumps(payload))
