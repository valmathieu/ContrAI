"""Pins the lobby reader: whole seat maps, the start flag, raw accounts."""

import dataclasses

import pytest

from contrai_scraper import (
    LobbyRoster,
    LobbyWatcher,
    ProfileError,
    Translator,
    WireStream,
    lobby_seats,
)

#: The tournament row's hash, and another row's.
CUP = "cfg-42"
OTHER = "cfg-7"

#: Four accounts as the site spells them: six digits, zero-padded.
FOUR = {"top": "095024", "right": "100001", "bottom": "100002", "left": "100003"}


def slot(builders, seats, *, row=CUP, full=None, frame_id="l1", kind="slot"):
    """One lobby event in the fixture vocabulary: a row's whole seat map."""

    data = {"key": row, "chairs": {
        placement: {"acct": account, "grade": "3"}
        for placement, account in seats.items()
    }}
    if full is not None:
        data["ready"] = full
    return builders.envelope("payload", kind, data, frame_id=frame_id)


def watch(profile, texts, *, row=CUP):
    """Every roster the watcher announces over a run of frame texts."""

    stream = WireStream(profile.wire)
    watcher = LobbyWatcher(profile, row)
    announced = []
    for at, text in enumerate(texts):
        event = stream.ingest(text, socket=0)
        if event is not None:
            roster = watcher.read(event, at=float(at))
            if roster is not None:
                announced.append(roster)
    return announced, watcher


class TestAnnouncing:
    def test_a_full_row_announces_its_four_players(self, profile, builders):
        announced, _ = watch(profile, [
            slot(builders, dict(list(FOUR.items())[:3]), frame_id="l1"),
            slot(builders, FOUR, frame_id="l2"),
            slot(builders, FOUR, full=True, frame_id="l3"),
        ])
        assert [(dict(roster.seats), roster.at) for roster in announced] == [(FOUR, 2.0)]

    def test_four_seats_without_the_flag_are_not_a_start(self, profile, builders):
        # The row fills and can still lose a player before its game starts;
        # only the flag says it did.
        announced, _ = watch(profile, [slot(builders, FOUR)])
        assert announced == []

    def test_a_seat_map_is_the_whole_map_never_a_change(self, profile, builders):
        # Added up, four one-seat events make a roster that never sat
        # together: the reading that looked as though it was working.
        texts = [
            slot(builders, {placement: account}, frame_id=f"l{index}",
                 full=index == 3)
            for index, (placement, account) in enumerate(FOUR.items())
        ]
        announced, watcher = watch(profile, texts)
        assert (announced, watcher.states) == ([], 4)

    def test_another_rows_start_is_not_the_tournaments(self, profile, builders):
        announced, watcher = watch(profile, [slot(builders, FOUR, row=OTHER, full=True)])
        assert (announced, watcher.states) == ([], 0)

    def test_an_event_of_another_kind_is_ignored(self, profile, builders):
        announced, watcher = watch(profile, [slot(builders, FOUR, full=True,
                                                   kind="joinTable")])
        assert (announced, watcher.states) == ([], 0)

    def test_a_start_is_announced_once(self, profile, builders):
        announced, _ = watch(profile, [
            slot(builders, FOUR, full=True, frame_id="l1"),
            slot(builders, FOUR, full=True, frame_id="l2"),
        ])
        assert len(announced) == 1

    def test_the_same_four_are_announced_again_after_the_row_recycled(
        self, profile, builders
    ):
        # A rematch: the row empties to its next first player and the same
        # four sit down again. It is a new game.
        announced, _ = watch(profile, [
            slot(builders, FOUR, full=True, frame_id="l1"),
            slot(builders, {"top": "095024"}, frame_id="l2"),
            slot(builders, FOUR, full=True, frame_id="l3"),
        ])
        assert len(announced) == 2

    def test_four_seats_naming_one_account_twice_are_not_a_roster(
        self, profile, builders
    ):
        doubled = {**FOUR, "left": FOUR["top"]}
        announced, _ = watch(profile, [slot(builders, doubled, full=True)])
        assert announced == []

    def test_accounts_keep_their_leading_zeros(self, profile, builders):
        announced, _ = watch(profile, [slot(builders, FOUR, full=True)])
        assert "095024" in announced[0].accounts


class TestSeats:
    def test_an_account_that_is_not_a_string_is_not_counted(self, profile, builders):
        # A number there has already lost its leading zero.
        data = {"key": CUP, "chairs": {"top": {"acct": 95024}, "right": {"acct": ""},
                                       "bottom": {"acct": "100002"}}}
        assert lobby_seats(data, Translator(profile)) == {"bottom": "100002"}

    def test_a_payload_with_no_seat_map_has_none(self, profile):
        assert lobby_seats({"key": CUP, "chairs": ["top"]}, Translator(profile)) is None

    def test_a_row_with_no_seat_map_announces_nothing(self, profile, builders):
        text = builders.envelope("payload", "slot", {"key": CUP, "ready": True})
        announced, watcher = watch(profile, [text])
        assert (announced, watcher.states) == ([], 1)


class TestRoster:
    def test_the_digest_names_the_four_and_gives_none_away(self):
        first = LobbyRoster(seats=FOUR, at=0.0, received_ms=None)
        swapped = LobbyRoster(
            seats=dict(zip(FOUR, reversed(list(FOUR.values())), strict=True)),
            at=5.0, received_ms=1,
        )
        other = LobbyRoster(seats={**FOUR, "left": "100009"}, at=0.0, received_ms=None)
        assert (first.digest == swapped.digest, first.digest == other.digest,
                any(account in first.digest for account in FOUR.values())) == (
            True, False, False)


class TestProfile:
    def test_a_profile_that_cannot_read_the_lobby_is_refused(self, profile):
        bare = dataclasses.replace(
            profile,
            wire=dataclasses.replace(
                profile.wire,
                events=dataclasses.replace(profile.wire.events, lobby_table=None),
            ),
        )
        with pytest.raises(ProfileError, match="lobby's socket"):
            LobbyWatcher(bare, CUP)
