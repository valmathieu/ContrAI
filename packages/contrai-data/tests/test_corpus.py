"""Pins the corpus: which copy of a game is kept, and why the others are not."""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from contrai_data import (
    CopyChoice,
    GameEnded,
    GameEvent,
    RecordCopy,
    RoundScored,
    choose_copy,
)


def _round_of(event: GameEvent) -> int | None:
    """The round an event belongs to, or ``None`` for the game-level ones."""

    return getattr(event, "round", None)


def _without_rounds(events: list[GameEvent], *numbers: int) -> list[GameEvent]:
    """The game with whole rounds cut out, deal to score."""

    return [event for event in events if _round_of(event) not in numbers]


def _unscored(events: list[GameEvent], number: int) -> list[GameEvent]:
    """The game with one round's score line dropped."""

    return [
        event
        for event in events
        if not (isinstance(event, RoundScored) and event.round == number)
    ]


def _copy(events: list[GameEvent], source: str = "box", origin: str = "a.jsonl") -> RecordCopy:
    return RecordCopy.of(source, origin, events)


class TestRecordCopy:
    def test_it_counts_what_it_holds(self, three_round_game):
        copy = _copy(_unscored(three_round_game, 3))
        assert (copy.game_id, copy.rounds, copy.scored_rounds, copy.ended_with_totals,
                copy.first_round) == ("engine-20260910T181815Z-a1b2c3", 3, 2, True, 1)

    def test_a_copy_with_no_round_starts_nowhere(self, three_round_game):
        copy = _copy(three_round_game[:2])
        assert (copy.rounds, copy.first_round, copy.ended_with_totals) == (0, None, False)

    def test_an_end_without_totals_does_not_count(self, three_round_game):
        events = [
            dataclasses.replace(event, totals=None) if isinstance(event, GameEnded)
            else event
            for event in three_round_game
        ]
        assert _copy(events).ended_with_totals is False


class TestRanking:
    def test_a_single_candidate_is_kept_with_nothing_rejected(self, three_round_game):
        copy = _copy(three_round_game)
        assert choose_copy([copy]) == CopyChoice(chosen=copy, rejected=())

    def test_more_scored_rounds_win_before_more_rounds(self, three_round_game):
        # The loser holds one more round, but its extra rounds carry no
        # score: a scored round is what verification and training consume.
        dealt = _copy(_unscored(_unscored(three_round_game, 3), 2), origin="dealt")
        scored = _copy(_without_rounds(three_round_game, 3), origin="scored")
        choice = choose_copy([dealt, scored])
        assert choice.chosen is scored
        assert choice.rejected[0].reason == "fewer scored rounds (1 against 2)"

    def test_more_rounds_win_on_equal_scores(self, three_round_game):
        longer = _copy(_unscored(three_round_game, 3), origin="longer")
        shorter = _copy(_without_rounds(three_round_game, 3), origin="shorter")
        choice = choose_copy([shorter, longer])
        assert (choice.chosen, choice.rejected[0].reason) == (
            longer, "fewer rounds (2 against 3)")

    def test_a_closing_total_wins_on_equal_rounds(self, three_round_game):
        closed = _copy(three_round_game, origin="z-closed")
        open_ = _copy(three_round_game[:-1], origin="a-open")
        choice = choose_copy([open_, closed])
        assert (choice.chosen, choice.rejected[0].reason) == (
            closed, "no game_ended with totals")

    def test_the_earlier_join_wins_on_equal_counts(self, three_round_game):
        early = _copy(_without_rounds(three_round_game, 3), origin="z-early")
        late = _copy(_without_rounds(three_round_game, 1), origin="a-late")
        choice = choose_copy([late, early])
        assert (choice.chosen, choice.rejected[0].reason) == (
            early, "joined later (round 2 against round 1)")

    def test_a_copy_with_rounds_beats_one_with_none(self, three_round_game):
        # Both hold no scored round and the same round count only when both
        # are empty, so this pins the "joins at infinity" end of the key.
        empty = _copy(three_round_game[:2], origin="a-empty")
        choice = choose_copy([empty, _copy(three_round_game, origin="b-full")])
        assert choice.chosen.origin == "b-full"

    def test_a_tie_keeps_the_first_source(self, three_round_game):
        box = _copy(three_round_game, source="box", origin="z.jsonl")
        laptop = _copy(three_round_game, source="laptop", origin="a.jsonl")
        choice = choose_copy([laptop, box])
        assert (choice.chosen, choice.rejected[0].reason) == (
            box, "a tie, kept the first source (box before laptop)")

    def test_a_tie_within_one_source_keeps_the_first_origin(self, three_round_game):
        first = _copy(three_round_game, origin="raw/box/a.jsonl")
        second = _copy(three_round_game, origin="raw/box/b.jsonl")
        choice = choose_copy([second, first])
        assert (choice.chosen, choice.rejected[0].reason) == (
            first, "a tie, kept the first origin in sorted order")

    def test_the_order_of_the_candidates_does_not_matter(self, three_round_game):
        copies = [
            _copy(three_round_game, origin="full"),
            _copy(_unscored(three_round_game, 3), origin="unscored"),
            _copy(_without_rounds(three_round_game, 3), origin="short"),
            _copy(three_round_game[:-1], origin="open"),
        ]
        choices = {
            (choice.chosen.origin, tuple(r.copy.origin for r in choice.rejected))
            for choice in map(choose_copy, itertools.permutations(copies))
        }
        assert choices == {("full", ("open", "unscored", "short"))}

    def test_the_choice_names_its_game(self, three_round_game):
        assert choose_copy([_copy(three_round_game)]).game_id == (
            "engine-20260910T181815Z-a1b2c3")


class TestRefusals:
    def test_no_candidate_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            choose_copy([])

    def test_copies_of_different_games_are_refused(self, three_round_game):
        header = dataclasses.replace(three_round_game[0], game_id="engine-other")
        other = _copy([header, *three_round_game[1:]])
        with pytest.raises(ValueError, match="several games"):
            choose_copy([_copy(three_round_game), other])
