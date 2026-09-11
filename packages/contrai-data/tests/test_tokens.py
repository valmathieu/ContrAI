"""Pins the record's ASCII spelling and the strictness of reading it back."""

import dataclasses

import pytest
from contrai_core import (
    Bid,
    Card,
    ContractBid,
    DoubleBid,
    InvalidRuleConfigError,
    PassBid,
    Position,
    Rank,
    RedoubleBid,
    Rounding,
    RuleConfig,
    SlamLevel,
    Suit,
    TeamSide,
    TrumpVariant,
)

from contrai_data import RecordFormatError
from contrai_data.tokens import (
    bid_payload,
    card_token,
    contract_suit_token,
    contract_value_token,
    parse_bid,
    parse_card,
    parse_contract_suit,
    parse_contract_value,
    parse_position,
    parse_ruleset,
    parse_side,
    parse_timestamp,
    position_token,
    ruleset_payload,
    side_token,
)

ALL_CARDS = [Card(suit, rank) for suit in Suit for rank in Rank]


class TestPositions:
    @pytest.mark.parametrize(
        "position, token",
        [(Position.NORTH, "N"), (Position.WEST, "W"),
         (Position.SOUTH, "S"), (Position.EAST, "E")],
    )
    def test_token(self, position, token):
        assert position_token(position) == token
        assert parse_position(token) is position

    @pytest.mark.parametrize("token", ["n", "North", "X", "", "NS", 1, None])
    def test_refuses_anything_else(self, token):
        with pytest.raises(RecordFormatError, match="seat token"):
            parse_position(token)


class TestSides:
    @pytest.mark.parametrize("side", list(TeamSide))
    def test_round_trip(self, side):
        assert parse_side(side_token(side)) is side

    @pytest.mark.parametrize("token", ["ns", "N", "North-South", "", 1, None])
    def test_refuses_anything_else(self, token):
        with pytest.raises(RecordFormatError, match="side token"):
            parse_side(token)


class TestCards:
    @pytest.mark.parametrize("card", ALL_CARDS)
    def test_round_trip(self, card):
        assert parse_card(card_token(card)) == card

    @pytest.mark.parametrize(
        "card, token",
        [
            (Card(Suit.SPADES, Rank.TEN), "10S"),
            (Card(Suit.HEARTS, Rank.JACK), "JH"),
            (Card(Suit.DIAMONDS, Rank.ACE), "AD"),
            (Card(Suit.CLUBS, Rank.SEVEN), "7C"),
        ],
    )
    def test_spelling(self, card, token):
        assert card_token(card) == token

    def test_every_token_is_distinct(self):
        # 10 is the one two-character rank; a fixed-offset parser would
        # read its suit off the wrong character.
        assert len({card_token(card) for card in ALL_CARDS}) == 32

    @pytest.mark.parametrize(
        "token", ["", "S", "1S", "JX", "10", "JHH", "jh", "10s", 10, None]
    )
    def test_refuses_anything_else(self, token):
        with pytest.raises(RecordFormatError, match="card token"):
            parse_card(token)


class TestContractSuits:
    @pytest.mark.parametrize(
        "suit, token",
        [
            (Suit.SPADES, "S"),
            (Suit.HEARTS, "H"),
            (Suit.DIAMONDS, "D"),
            (Suit.CLUBS, "C"),
            (TrumpVariant.NO_TRUMP, "NT"),
            (TrumpVariant.ALL_TRUMP, "AT"),
        ],
    )
    def test_round_trip(self, suit, token):
        assert contract_suit_token(suit) == token
        assert parse_contract_suit(token) is suit

    @pytest.mark.parametrize("token", ["nt", "Spades", "X", "", 1, None])
    def test_refuses_anything_else(self, token):
        with pytest.raises(RecordFormatError, match="contract suit"):
            parse_contract_suit(token)


class TestContractValues:
    @pytest.mark.parametrize("value", [*range(80, 250, 10)])
    def test_numeric_values_ride_as_numbers(self, value):
        assert contract_value_token(value) == value
        assert parse_contract_value(value) == value

    @pytest.mark.parametrize(
        "level, token", [(SlamLevel.SLAM, "slam"), (SlamLevel.SOLO_SLAM, "solo_slam")]
    )
    def test_slam_values_ride_as_words(self, level, token):
        assert contract_value_token(level) == token
        assert parse_contract_value(token) is level

    @pytest.mark.parametrize("token", ["capot", 85, 70, 250, True, None])
    def test_refuses_anything_else(self, token):
        with pytest.raises(RecordFormatError, match="contract value"):
            parse_contract_value(token)


class TestBids:
    @pytest.mark.parametrize(
        "bid, payload",
        [
            (PassBid(player=Position.NORTH), {"kind": "pass"}),
            (DoubleBid(player=Position.NORTH), {"kind": "double"}),
            (RedoubleBid(player=Position.NORTH), {"kind": "redouble"}),
            (
                ContractBid(player=Position.NORTH, value=120, suit=Suit.HEARTS),
                {"kind": "contract", "value": 120, "suit": "H"},
            ),
            (
                ContractBid(
                    player=Position.NORTH, value=SlamLevel.SLAM, suit=TrumpVariant.ALL_TRUMP
                ),
                {"kind": "contract", "value": "slam", "suit": "AT"},
            ),
        ],
    )
    def test_round_trip(self, bid, payload):
        assert bid_payload(bid) == payload
        parsed = parse_bid(payload, Position.NORTH)
        assert parsed == bid
        assert type(parsed) is type(bid)
        assert parsed.player is Position.NORTH

    def test_parsing_seats_the_bid_at_the_given_position(self):
        parsed = parse_bid({"kind": "pass"}, Position.EAST)
        assert parsed.player is Position.EAST

    def test_refuses_a_bid_variant_it_cannot_spell(self):
        # The ``case _`` on the way out: a fifth ``Bid`` subclass added to
        # core without a token is a format gap, not a silent pass.
        @dataclasses.dataclass(frozen=True, slots=True)
        class SurcoincheBid(Bid):
            pass

        with pytest.raises(RecordFormatError, match="bid variant"):
            bid_payload(SurcoincheBid(player=Position.NORTH))

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"kind": "coinche"},
            {"kind": "contract"},
            {"kind": "contract", "value": 120},
            {"kind": "contract", "suit": "H"},
            "pass",
        ],
    )
    def test_refuses_a_malformed_payload(self, payload):
        with pytest.raises(RecordFormatError):
            parse_bid(payload, Position.NORTH)


class TestRulesets:
    def test_round_trips_the_default_table(self):
        rules = RuleConfig()
        assert parse_ruleset(ruleset_payload(rules)) == rules

    def test_round_trips_a_non_default_table(self):
        # The three deltas the observed tables play (spec §5.5), pinned here
        # so a knob renamed in core breaks this rather than a live record.
        rules = dataclasses.replace(
            RuleConfig.classic(),
            any_failure_marks_160=True,
            only_announced_points_multiplied=False,
            solo_slam_gives_the_lead=True,
        )
        assert parse_ruleset(ruleset_payload(rules)) == rules

    def test_payload_is_flat_and_token_shaped(self):
        payload = ruleset_payload(RuleConfig())
        assert len(payload) == len(dataclasses.fields(RuleConfig))
        assert payload["rounding"] == "exact"
        assert payload["turn_direction"] == "anticlockwise"
        assert payload["target_score"] == 2000

    def test_a_missing_knob_takes_its_default(self):
        # A record written before a knob existed was, by construction,
        # produced by a table playing that knob's default.
        payload = ruleset_payload(RuleConfig())
        del payload["rounding"]
        assert parse_ruleset(payload).rounding is Rounding.EXACT

    def test_an_unknown_knob_is_refused(self):
        # The opposite case: a record from a newer producer names a rule
        # this build cannot honour. Dropping it silently would replay the
        # game under rules it was never played under.
        payload = ruleset_payload(RuleConfig()) | {"double_belote_counts": True}
        with pytest.raises(RecordFormatError, match="double_belote_counts"):
            parse_ruleset(payload)

    def test_an_unknown_enum_token_is_refused(self):
        payload = ruleset_payload(RuleConfig()) | {"rounding": "nearest_20"}
        with pytest.raises(RecordFormatError, match="rounding"):
            parse_ruleset(payload)

    def test_an_enum_knob_given_a_non_token_is_refused(self):
        payload = ruleset_payload(RuleConfig()) | {"rounding": 20}
        with pytest.raises(RecordFormatError, match="rounding"):
            parse_ruleset(payload)

    @pytest.mark.parametrize(
        "override", [{"target_score": True}, {"target_score": "2000"}, {"reshuffle_every_round": 1}]
    )
    def test_a_wrongly_typed_knob_is_refused(self, override):
        # ``True`` is an ``int`` to ``isinstance`` — the check is on the
        # exact type, so a bool cannot slip into a numeric knob.
        payload = ruleset_payload(RuleConfig()) | override
        with pytest.raises(RecordFormatError):
            parse_ruleset(payload)

    def test_an_impossible_table_raises_core_s_own_error(self):
        payload = ruleset_payload(RuleConfig()) | {
            "mark_made_points": False,
            "mark_announced_points": False,
        }
        with pytest.raises(InvalidRuleConfigError):
            parse_ruleset(payload)

    def test_refuses_a_payload_that_is_not_a_mapping(self):
        with pytest.raises(RecordFormatError, match="ruleset"):
            parse_ruleset(["target_score", 2000])


class TestTimestamps:
    @pytest.mark.parametrize(
        "value",
        ["2026-09-10T18:18:15Z", "2026-09-10T18:18:15.250Z", "2026-09-10T18:18:15+00:00"],
    )
    def test_accepts_an_utc_instant_unchanged(self, value):
        assert parse_timestamp(value) == value

    @pytest.mark.parametrize(
        "value",
        ["2026-09-10T18:18:15", "2026-09-10T20:18:15+02:00", "10/09/2026", "", 1757528295, None],
    )
    def test_refuses_anything_that_is_not_an_utc_instant(self, value):
        # A naive or locally-offset timestamp in a record is a producer bug
        # that silently mis-orders events across machines. Case-insensitive:
        # three different refusals answer here and they do not all start the
        # sentence with the same word.
        with pytest.raises(RecordFormatError, match="(?i)timestamp"):
            parse_timestamp(value)
