"""Pins the live events: who a double belongs to, which seat played, gapless seq."""

import json

import pytest
from contrai_core import (
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    RedoubleBid,
    SlamLevel,
    Suit,
)

from contrai_scraper import (
    ParseError,
    Translator,
    WireStream,
    bid_events,
    collect_rounds,
    compress_to_base64,
    load_profile,
    order_events,
    play_events,
)

SEATS = {
    "p1": Position.NORTH,
    "p2": Position.WEST,
    "p3": Position.SOUTH,
    "p4": Position.EAST,
}

TS = "2026-09-11T18:18:15Z"


def _rounds(profile, frames):
    """Runs raw frame texts through the stream and collects the rounds.

    The whole module works this way: build frames with the shared builders,
    push them through the real ``WireStream``, assert on ``contrai-data``
    events. No test constructs a ``WireEvent`` by hand — that would let the
    two halves drift apart and still pass.
    """

    stream = WireStream(profile.wire)
    events = [
        event
        for text in frames
        if (event := stream.ingest(text, socket=0)) is not None
    ]
    return collect_rounds(order_events(events), Translator(profile))


def _bids(profile, frames, seat_of_player=None, number=1):
    return bid_events(
        _rounds(profile, frames)[number],
        Translator(profile),
        seat_of_player or SEATS,
        ts=TS,
    )


def _plays(profile, frames, seat_of_player=None, number=1):
    return play_events(
        _rounds(profile, frames)[number],
        Translator(profile),
        seat_of_player or SEATS,
        ts=TS,
    )


class TestBids:
    def test_a_pass_is_a_null_payload(self, profile, builders):
        bids = _bids(profile, [builders.bid_frame(seq=1, actor="p1", payload=None)])
        assert isinstance(bids[0].bid, PassBid)
        assert bids[0].position is Position.NORTH

    def test_a_contract_bid_carries_value_and_suit(self, profile, builders):
        frames = [builders.bid_frame(
            seq=1, actor="p1", payload={"who": "p1", "colour": "wood", "level": 80})]
        bid = _bids(profile, frames)[0].bid
        assert isinstance(bid, ContractBid)
        assert (bid.value, bid.suit) == (80, Suit.SPADES)

    def test_a_slam_token_becomes_a_slam_bid(self, profile, builders):
        frames = [builders.bid_frame(
            seq=1, actor="p1", payload={"who": "p1", "colour": "wood", "level": "BIG"})]
        assert _bids(profile, frames)[0].bid.value is SlamLevel.SLAM

    def test_a_double_is_attributed_to_the_keys_actor(self, profile, builders):
        # The payload's owner field names who made the *underlying* bid, which
        # is the declarer, not the doubler. Taking the owner would credit the
        # double to the side it was aimed at — and the round still looks legal.
        frames = [
            builders.bid_frame(seq=1, actor="p1",
                               payload={"who": "p1", "colour": "wood", "level": 80}),
            builders.bid_frame(seq=2, actor="p2",
                               payload={"who": "p1", "colour": "wood", "level": 80,
                                        "twice": "p2"}),
        ]
        bids = _bids(profile, frames)
        assert isinstance(bids[1].bid, DoubleBid)
        assert bids[1].position is Position.WEST      # not NORTH

    def test_a_redouble_is_attributed_to_its_own_actor(self, profile, builders):
        frames = [
            builders.bid_frame(seq=1, actor="p1",
                               payload={"who": "p1", "colour": "wood", "level": 80}),
            builders.bid_frame(seq=2, actor="p2",
                               payload={"who": "p1", "colour": "wood", "level": 80,
                                        "twice": "p2"}),
            builders.bid_frame(seq=3, actor="p1",
                               payload={"who": "p1", "colour": "wood", "level": 80,
                                        "twice": "p2", "fourfold": "p1"}),
        ]
        bids = _bids(profile, frames)
        assert isinstance(bids[2].bid, RedoubleBid)
        assert bids[2].position is Position.NORTH

    def test_a_double_is_only_read_once(self, profile, builders):
        # Every bid after a double repeats the doubler in its payload, so a
        # reader that looks only at the field emits a double per bid.
        frames = [
            builders.bid_frame(seq=1, actor="p1",
                               payload={"who": "p1", "colour": "wood", "level": 80}),
            builders.bid_frame(seq=2, actor="p2",
                               payload={"who": "p1", "colour": "wood", "level": 80,
                                        "twice": "p2"}),
            builders.bid_frame(seq=3, actor="p3",
                               payload={"who": "p1", "colour": "wood", "level": 80,
                                        "twice": "p2"}),
        ]
        bids = _bids(profile, frames)
        assert [type(bid.bid) for bid in bids] == [ContractBid, DoubleBid, ContractBid]

    def test_the_record_sequence_is_renumbered_gaplessly(self, profile, builders):
        # The wire's own numbering skips values (two rounds of the real corpus
        # each skip one); contrai-data's projection refuses anything but 1..n.
        frames = [builders.bid_frame(seq=n, actor="p1", payload=None)
                  for n in (1, 2, 4, 5)]
        assert [bid.seq for bid in _bids(profile, frames)] == [1, 2, 3, 4]

    def test_a_bid_by_an_unseated_player_is_skipped(self, profile, builders):
        frames = [
            builders.bid_frame(seq=1, actor="p1", payload=None),
            builders.bid_frame(seq=2, actor="stranger", payload=None),
        ]
        assert len(_bids(profile, frames)) == 1

    def test_think_time_comes_from_the_metadata(self, profile, builders):
        frames = [builders.bid_frame(seq=1, actor="p1", payload=None, at=100)]
        assert _bids(profile, frames)[0].think_ms is None


class TestPlays:
    def test_a_play_carries_its_trick_and_the_seat_that_made_it(
        self, profile, builders
    ):
        # The key's fourth field is the index within the trick, not the seat —
        # the seat comes from the player handle.
        frames = [builders.play_frame(trick=3, index=2, actor="p4", card="9z")]
        play = _plays(profile, frames)[0]
        assert (play.trick, play.position) == (3, Position.EAST)
        assert (play.card.suit, play.card.rank.value) == (Suit.CLUBS, "Ace")

    def test_think_time_comes_from_the_metadata(self, profile, builders):
        frames = [builders.play_frame(actor="p1", card="2w", think=2561)]
        assert _plays(profile, frames)[0].think_ms == 2561

    def test_a_play_without_metadata_has_no_think_time(self, profile, builders):
        frames = [builders.play_frame(actor="p1", card="2w")]
        assert _plays(profile, frames)[0].think_ms is None

    def test_plays_group_into_tricks_in_order(self, profile, builders):
        frames = [
            builders.play_frame(trick=trick, index=index, actor=actor, card=card)
            for trick, cards in ((1, ("2w", "3w", "4w", "5w")),
                                 (2, ("2x", "3x", "4x", "5x")))
            for index, (actor, card) in enumerate(
                zip(("p1", "p2", "p3", "p4"), cards, strict=True))
        ]
        plays = _plays(profile, frames)
        assert [(p.trick, p.position) for p in plays] == [
            (1, Position.NORTH), (1, Position.WEST), (1, Position.SOUTH),
            (1, Position.EAST),
            (2, Position.NORTH), (2, Position.WEST), (2, Position.SOUTH),
            (2, Position.EAST),
        ]

    def test_no_observed_play_is_marked_derived(self, profile, builders):
        # The eighth trick is the derived one, and it is added later, by the
        # session assembler — nothing that arrived on the wire is derived.
        frames = [builders.play_frame(actor="p1", card="2w")]
        assert _plays(profile, frames)[0].derived is False

    def test_a_play_by_an_unseated_player_is_skipped(self, profile, builders):
        frames = [
            builders.play_frame(actor="p1", card="2w"),
            builders.play_frame(index=1, actor="stranger", card="3w"),
        ]
        assert len(_plays(profile, frames)) == 1


class TestRounds:
    def test_the_deal_payload_is_decompressed(self, profile, builders):
        frames = [builders.deal_frame(round_=1, cards=("2w", "3x"))]
        assert _rounds(profile, frames)[1].deal_stock == ("2w", "3x")

    def test_rounds_are_kept_apart(self, profile, builders):
        frames = [
            builders.play_frame(round_=1, actor="p1", card="2w"),
            builders.play_frame(round_=2, actor="p2", card="3w"),
        ]
        rounds = _rounds(profile, frames)
        assert sorted(rounds) == [1, 2]

    def test_a_round_remembers_when_it_started(self, profile, builders):
        frames = [
            builders.play_frame(round_=1, index=1, actor="p2", card="3w", at=200),
            builders.play_frame(round_=1, index=0, actor="p1", card="2w", at=100),
        ]
        assert _rounds(profile, frames)[1].first_ms == 100

    def test_events_of_no_round_are_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "updateTable", {"over": 1})]
        assert _rounds(profile, frames) == {}

    def test_an_undecodable_deal_payload_is_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "g1,1,0,0", "!!!not base64!!!")]
        assert _rounds(profile, frames)[1].deal_stock == ()


class TestRefusals:
    def test_a_bid_payload_of_another_shape_is_refused(self, profile, builders):
        # Defaulting an unknown shape to a pass keeps the auction legal and
        # makes the round wrong, which is the failure the record cannot catch.
        frames = [builders.bid_frame(seq=1, actor="p1", payload="nonsense")]
        with pytest.raises(ParseError):
            _bids(profile, frames)

    def test_a_null_bid_needs_the_profile_to_call_it_a_pass(
        self, tmp_path, profile_text, builders
    ):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("pass_is_null = true",
                                             "pass_is_null = false"),
                        encoding="utf-8")
        other = load_profile(path)
        frames = [builders.bid_frame(seq=1, actor="p1", payload=None)]
        with pytest.raises(ParseError):
            _bids(other, frames)

    def test_a_contract_bid_without_a_trump_is_refused(self, profile, builders):
        frames = [builders.bid_frame(seq=1, actor="p1",
                                     payload={"who": "p1", "level": 80})]
        with pytest.raises(ParseError, match="trump"):
            _bids(profile, frames)


class TestOddKeys:
    def test_a_play_with_no_actor_is_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "g1,1,1,0,card,", "2w")]
        assert _rounds(profile, frames)[1].plays == {}

    def test_an_event_with_an_unknown_verb_is_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "g1,1,0,0,chat,p1", "hello")]
        round_ = _rounds(profile, frames)[1]
        assert (round_.plays, round_.bids) == ({}, {})

    def test_a_bid_verb_without_a_number_falls_back_to_the_key(
        self, profile, builders
    ):
        # The verb normally carries the wire's own sequence number; the key's
        # ordering slot repeats it, and is what is left if the verb stops.
        frames = [builders.envelope("payload", "g1,1,0,3,bid:x,p1", None)]
        assert list(_rounds(profile, frames)[1].bids) == [3]


class TestDealPayloads:
    def test_a_deal_that_is_not_a_string_is_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "g1,1,0,0", {"beforeDeal": ["2w"]})]
        assert _rounds(profile, frames)[1].deal_stock == ()

    def test_a_deal_that_decodes_to_non_json_is_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "g1,1,0,0",
                                    compress_to_base64("not a document"))]
        assert _rounds(profile, frames)[1].deal_stock == ()

    def test_a_deal_whose_order_is_not_a_list_is_ignored(self, profile, builders):
        frames = [builders.envelope("payload", "g1,1,0,0",
                                    compress_to_base64(json.dumps({"beforeDeal": 7})))]
        assert _rounds(profile, frames)[1].deal_stock == ()
