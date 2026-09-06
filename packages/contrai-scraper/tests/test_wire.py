"""Pins the envelope: keepalives, double wrap, dedup, key parsing, ordering."""

import json

import pytest

from contrai_scraper import (
    WireStream,
    dig,
    duplicate_key,
    load_profile,
    order_events,
    parse_key,
    unwrap,
)


def envelope(kind: str, event: str, data, *, frame_id: str = "1", metadata=None) -> str:
    """Builds a frame the way the invented vocabulary spells it."""

    inner = {"event": event, "data": data}
    if metadata is not None:
        inner["metadata"] = metadata
    return json.dumps({"id": frame_id, "event": kind, "data": json.dumps(inner)})


class TestUnwrap:
    def test_a_keepalive_is_not_an_event(self, profile):
        assert unwrap("tick", socket=0, wire=profile.wire) is None

    def test_a_non_json_frame_is_not_an_event(self, profile):
        # The keepalive is raw text, not JSON, and a parser that assumes
        # otherwise throws roughly once a second on a busy socket.
        assert unwrap("not json at all", socket=0, wire=profile.wire) is None

    def test_a_frame_of_another_kind_is_not_an_event(self, profile):
        frame = json.dumps({"id": "1", "event": "hello", "data": "abcd"})
        assert unwrap(frame, socket=0, wire=profile.wire) is None

    def test_the_inner_payload_is_parsed_twice(self, profile):
        frame = envelope("payload", "g1,1,0,0", "blob")
        event = unwrap(frame, socket=0, wire=profile.wire)
        assert (event.kind, event.data) == ("g1,1,0,0", "blob")

    def test_metadata_travels_with_the_event(self, profile):
        frame = envelope("payload", "g1,1,1,0,card,p1", "2w",
                         metadata={"ms": {"v": 2561, "m": 8000}, "at": 1788855235490})
        event = unwrap(frame, socket=0, wire=profile.wire)
        assert (event.received_ms, event.metadata["ms"]["v"]) == (1788855235490, 2561)

    def test_an_event_without_metadata_still_unwraps(self, profile):
        # The table-lifecycle event carries no metadata block at all.
        frame = envelope("payload", "updateTable", {"over": 1})
        event = unwrap(frame, socket=0, wire=profile.wire)
        assert (event.metadata, event.received_ms) == ({}, None)


class TestKeys:
    def test_a_six_field_key_parses(self, profile):
        key = parse_key("g1,9,3,2,card,p7", profile.wire)
        assert (key.game, key.round, key.trick, key.position, key.verb, key.player) == (
            "g1", 9, 3, 2, "card", "p7")

    def test_the_deal_key_has_four_fields_and_no_player(self, profile):
        key = parse_key("g1,9,0,0", profile.wire)
        assert (key.round, key.verb, key.player) == (9, "deal", None)

    def test_a_bid_verb_keeps_its_sequence(self, profile):
        assert parse_key("g1,9,0,1,bid:3,p7", profile.wire).verb == "bid:3"

    @pytest.mark.parametrize("event", ["updateTable", "g1,nine,0,0", "g1,9", ""])
    def test_anything_that_is_not_a_game_key_reads_as_none(self, profile, event):
        assert parse_key(event, profile.wire) is None


    def test_a_short_key_whose_padding_is_not_zero_is_not_a_deal(self, profile):
        # Four parts alone do not make a deal: the slots a deal leaves empty
        # are spelled as zeros, and a key that fills them means something else.
        assert parse_key("g1,9,1,0", profile.wire) is None

    def test_the_deal_key_length_comes_from_the_profile(self, tmp_path, profile_text):
        # A site that addresses a deal by its round alone is describable
        # without touching this module — which is the point of the key being
        # profile-driven rather than a constant.
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("deal_key_arity = 4", "deal_key_arity = 2"),
                        encoding="utf-8")
        key = parse_key("g1,9", load_profile(path).wire)
        assert (key.round, key.trick, key.position, key.verb) == (9, None, None, "deal")


class TestMalformedFrames:
    def test_an_inner_payload_that_is_not_an_object_is_not_an_event(self, profile):
        frame = json.dumps({"id": "1", "event": "payload", "data": json.dumps("bare")})
        assert unwrap(frame, socket=0, wire=profile.wire) is None

    def test_an_event_without_a_name_is_not_an_event(self, profile):
        frame = json.dumps({"id": "1", "event": "payload",
                            "data": json.dumps({"event": 7, "data": None})})
        assert unwrap(frame, socket=0, wire=profile.wire) is None

    def test_a_metadata_block_that_is_not_an_object_is_ignored(self, profile):
        frame = envelope("payload", "updateTable", {"over": 1}, metadata="nonsense")
        event = unwrap(frame, socket=0, wire=profile.wire)
        assert (event.metadata, event.received_ms) == ({}, None)

    def test_an_inner_payload_already_parsed_is_taken_as_is(self, profile):
        # Not every capture re-encodes the inner document; one that hands it
        # over as an object must read the same way as one that stringifies it.
        frame = json.dumps({"id": "1", "event": "payload",
                            "data": {"event": "updateTable", "data": {"over": 1}}})
        assert unwrap(frame, socket=0, wire=profile.wire).kind == "updateTable"


class TestDuplicateKeys:
    def test_a_frame_that_is_not_json_has_no_key(self):
        assert duplicate_key("tick") is None

    def test_a_frame_with_no_id_and_no_inner_object_has_no_key(self):
        assert duplicate_key(json.dumps({"event": "payload", "data": "bare"})) is None


class TestDig:
    def test_a_path_through_a_non_mapping_reads_as_none(self):
        assert dig({"ms": 7}, "ms.v") is None


class TestDedup:
    def test_the_mirrored_socket_is_dropped(self, profile):
        stream = WireStream(profile.wire)
        frame = envelope("payload", "g1,1,1,0,card,2w", "2w", frame_id="7")
        assert stream.ingest(frame, socket=0) is not None
        assert stream.ingest(frame, socket=1) is None
        assert (stream.received, stream.deduped) == (2, 1)

    def test_frames_without_an_id_dedup_on_their_content(self, profile):
        # The table-lifecycle event carries no metadata and, in some captures,
        # no usable frame id either.
        stream = WireStream(profile.wire)
        frame = json.dumps({"event": "payload",
                            "data": json.dumps({"event": "updateTable",
                                                "data": {"over": 1}})})
        assert stream.ingest(frame, socket=0) is not None
        assert stream.ingest(frame, socket=1) is None

    def test_two_different_events_both_survive(self, profile):
        stream = WireStream(profile.wire)
        first = envelope("payload", "g1,1,1,0,card,p1", "2w", frame_id="1")
        second = envelope("payload", "g1,1,1,1,card,p2", "3x", frame_id="2")
        assert stream.ingest(first, 0) and stream.ingest(second, 0)
        assert stream.deduped == 0

    def test_keepalives_are_counted_as_skipped_not_deduped(self, profile):
        stream = WireStream(profile.wire)
        assert stream.ingest("tick", 0) is None
        assert (stream.skipped, stream.deduped) == (1, 0)


class TestOrdering:
    def test_events_sort_by_the_server_clock(self, profile):
        stream = WireStream(profile.wire)
        late = stream.ingest(envelope("payload", "g1,1,1,1,card,p2", "3x",
                                      frame_id="2", metadata={"at": 200}), 0)
        early = stream.ingest(envelope("payload", "g1,1,1,0,card,p1", "2w",
                                       frame_id="1", metadata={"at": 100}), 0)
        assert order_events([late, early]) == (early, late)

    def test_events_without_a_clock_keep_their_arrival_order(self, profile):
        stream = WireStream(profile.wire)
        first = stream.ingest(envelope("payload", "g1,1,0,0", "a", frame_id="1"), 0)
        second = stream.ingest(envelope("payload", "g1,2,0,0", "b", frame_id="2"), 0)
        assert order_events([first, second]) == (first, second)
