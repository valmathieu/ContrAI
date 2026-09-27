"""Pins the corpus: which copy of a game is kept, how raw logs come in, and
how a new ``games/`` replaces the old one."""

from __future__ import annotations

import dataclasses
import itertools
from pathlib import Path

import pytest

from contrai_data import (
    CopyChoice,
    CorpusError,
    GameEnded,
    GameEvent,
    ImportStatus,
    RawImport,
    RecordCopy,
    RoundScored,
    catalog_path,
    choose_copy,
    game_path,
    import_raw,
    load_game,
    raw_logs,
    verdicts_dir,
    write_games,
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


class TestReport:
    def test_a_choice_reports_every_copy_with_its_counts(self, three_round_game):
        full = _copy(three_round_game, origin="raw/box/a.jsonl")
        short = _copy(_without_rounds(three_round_game, 3), source="laptop",
                      origin="raw/laptop/b.jsonl")
        assert choose_copy([short, full]).as_report() == {
            "game_id": "engine-20260910T181815Z-a1b2c3",
            "chosen": {"source": "box", "origin": "raw/box/a.jsonl",
                       "scored_rounds": 3, "rounds": 3, "ended_with_totals": True,
                       "first_round": 1},
            "rejected": [{"source": "laptop", "origin": "raw/laptop/b.jsonl",
                          "scored_rounds": 2, "rounds": 2, "ended_with_totals": True,
                          "first_round": 1,
                          "reason": "fewer scored rounds (2 against 3)"}],
        }


# ----------------------------------------------------------------------
# Raw logs
# ----------------------------------------------------------------------


def _log(directory: Path, name: str, text: str) -> Path:
    """A raw log with the given content, somewhere outside the corpus."""

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def corpus(tmp_path) -> Path:
    return tmp_path / "corpus"


class TestImportRaw:
    def test_a_new_log_is_copied_under_its_source(self, tmp_path, corpus):
        log = _log(tmp_path / "box", "s1.jsonl", "a\nb\n")
        result = import_raw(corpus, {"box": [log]})
        assert result.entries == (("box", "s1.jsonl", ImportStatus.COPIED),)
        assert (corpus / "raw" / "box" / "s1.jsonl").read_text() == "a\nb\n"

    def test_the_same_log_again_is_present(self, tmp_path, corpus):
        log = _log(tmp_path / "box", "s1.jsonl", "a\n")
        import_raw(corpus, {"box": [log]})
        assert import_raw(corpus, {"box": [log]}).entries == (
            ("box", "s1.jsonl", ImportStatus.PRESENT),)

    def test_a_log_that_grew_replaces_its_snapshot(self, tmp_path, corpus):
        # The box's current session log is fetched while it is still being
        # written: next week's fetch is the same log, further on.
        import_raw(corpus, {"box": [_log(tmp_path / "w1", "s1.jsonl", "a\nb")]})
        grown = _log(tmp_path / "w2", "s1.jsonl", "a\nbc\nd\n")
        assert import_raw(corpus, {"box": [grown]}).entries == (
            ("box", "s1.jsonl", ImportStatus.GROWN),)
        assert (corpus / "raw" / "box" / "s1.jsonl").read_text() == "a\nbc\nd\n"

    def test_an_older_snapshot_leaves_the_longer_log(self, tmp_path, corpus):
        import_raw(corpus, {"box": [_log(tmp_path / "w2", "s1.jsonl", "a\nb\n")]})
        older = _log(tmp_path / "w1", "s1.jsonl", "a\n")
        assert import_raw(corpus, {"box": [older]}).entries == (
            ("box", "s1.jsonl", ImportStatus.STALE),)
        assert (corpus / "raw" / "box" / "s1.jsonl").read_text() == "a\nb\n"

    def test_a_different_log_under_a_taken_name_refuses_everything(self, tmp_path, corpus):
        import_raw(corpus, {"box": [_log(tmp_path / "w1", "s1.jsonl", "a\nb\n")]})
        new = _log(tmp_path / "w2", "s2.jsonl", "fresh\n")
        clash = _log(tmp_path / "w2", "s1.jsonl", "a\nX\n")
        with pytest.raises(CorpusError, match=r"box/s1\.jsonl"):
            import_raw(corpus, {"box": [new, clash]})
        # Judged before anything is copied: the good log did not come in.
        assert not (corpus / "raw" / "box" / "s2.jsonl").exists()

    def test_every_conflict_is_named(self, tmp_path, corpus):
        import_raw(corpus, {"box": [_log(tmp_path / "a", "s1.jsonl", "1\n"),
                                    _log(tmp_path / "a", "s2.jsonl", "2\n")]})
        with pytest.raises(CorpusError, match=r"s1\.jsonl.*s2\.jsonl"):
            import_raw(corpus, {"box": [_log(tmp_path / "b", "s1.jsonl", "x\n"),
                                        _log(tmp_path / "b", "s2.jsonl", "y\n")]})

    def test_two_logs_handed_in_under_one_name_are_compared(self, tmp_path, corpus):
        short = _log(tmp_path / "a", "s1.jsonl", "a\n")
        long = _log(tmp_path / "b", "s1.jsonl", "a\nb\n")
        result = import_raw(corpus, {"box": [short, long, short]})
        assert [status for *_, status in result.entries] == [
            ImportStatus.COPIED, ImportStatus.GROWN, ImportStatus.STALE]
        assert (corpus / "raw" / "box" / "s1.jsonl").read_text() == "a\nb\n"

    def test_one_name_in_two_sources_is_two_logs(self, tmp_path, corpus):
        result = import_raw(corpus, {"box": [_log(tmp_path / "a", "s1.jsonl", "a\n")],
                                     "laptop": [_log(tmp_path / "b", "s1.jsonl", "b\n")]})
        assert result.counts() == {
            "box": {"copied": 1, "grown": 0, "present": 0, "stale": 0},
            "laptop": {"copied": 1, "grown": 0, "present": 0, "stale": 0},
        }

    def test_a_long_log_is_compared_past_the_first_chunk(self, tmp_path, corpus,
                                                         monkeypatch):
        monkeypatch.setattr("contrai_data.corpus._CHUNK", 4)
        import_raw(corpus, {"box": [_log(tmp_path / "a", "s1.jsonl", "abcdefgh1")]})
        with pytest.raises(CorpusError):
            import_raw(corpus, {"box": [_log(tmp_path / "b", "s1.jsonl", "abcdefgh2")]})

    @pytest.mark.parametrize("label", ["Box", "", "../up", "a/b", "-x", "box.1"])
    def test_a_label_that_is_not_a_plain_segment_is_refused(self, tmp_path, corpus,
                                                             label):
        with pytest.raises(CorpusError, match="source label"):
            import_raw(corpus, {label: [_log(tmp_path, "s1.jsonl", "a\n")]})
        assert not corpus.exists()

    def test_a_failed_copy_leaves_no_partial_file(self, tmp_path, corpus, monkeypatch):
        def broken(source, destination):
            Path(destination).write_text("half")
            raise OSError("disk full")

        monkeypatch.setattr("contrai_data.corpus.shutil.copyfile", broken)
        with pytest.raises(OSError, match="disk full"):
            import_raw(corpus, {"box": [_log(tmp_path / "a", "s1.jsonl", "a\n")]})
        assert list((corpus / "raw" / "box").iterdir()) == []

    def test_an_empty_import_reports_nothing(self, corpus):
        assert import_raw(corpus, {}) == RawImport(())


class TestRawLogs:
    def test_a_corpus_without_raw_holds_none(self, corpus):
        assert raw_logs(corpus) == ()

    def test_logs_come_by_source_then_name(self, tmp_path, corpus):
        import_raw(corpus, {
            "laptop": [_log(tmp_path / "l", "a.jsonl", "1\n")],
            "box": [_log(tmp_path / "b", "b.jsonl", "2\n"),
                    _log(tmp_path / "b", "a.jsonl", "3\n")],
        })
        (corpus / "raw" / "README.txt").write_text("not a source")
        (corpus / "raw" / "box" / "notes.txt").write_text("not a log")
        assert [(source, path.name) for source, path in raw_logs(corpus)] == [
            ("box", "a.jsonl"), ("box", "b.jsonl"), ("laptop", "a.jsonl")]


# ----------------------------------------------------------------------
# Games
# ----------------------------------------------------------------------


class TestWriteGames:
    def test_each_copy_becomes_one_loadable_record(self, corpus, three_round_game):
        assert write_games(corpus, [_copy(three_round_game)]) == 1
        record = load_game(game_path(corpus, "engine-20260910T181815Z-a1b2c3"))
        assert len(record.rounds) == 3

    def test_the_old_games_are_replaced_whole(self, corpus, three_round_game):
        stale = game_path(corpus, "obs-gone")
        stale.parent.mkdir(parents=True)
        stale.write_text("an old record\n")
        write_games(corpus, [_copy(three_round_game)])
        assert [path.name for path in (corpus / "games").iterdir()] == [
            "engine-20260910T181815Z-a1b2c3.jsonl"]
        # Nothing is left behind beside games/: no staging, no retired copy.
        assert sorted(path.name for path in corpus.iterdir()) == ["games"]

    def test_the_verdicts_and_catalog_go_with_the_old_games(self, corpus,
                                                            three_round_game):
        verdicts_dir(corpus).mkdir(parents=True)
        (verdicts_dir(corpus) / "old.json").write_text("{}")
        catalog_path(corpus).write_text("stale")
        write_games(corpus, [_copy(three_round_game)])
        assert (verdicts_dir(corpus).exists(), catalog_path(corpus).exists()) == (
            False, False)

    def test_two_copies_of_one_game_are_refused_and_change_nothing(
        self, corpus, three_round_game
    ):
        kept = game_path(corpus, "obs-kept")
        kept.parent.mkdir(parents=True)
        kept.write_text("kept\n")
        with pytest.raises(ValueError, match="Two copies"):
            write_games(corpus, [_copy(three_round_game), _copy(three_round_game)])
        assert kept.read_text() == "kept\n"
        assert sorted(path.name for path in corpus.iterdir()) == ["games"]

    def test_a_catalog_held_open_changes_nothing(self, corpus, three_round_game,
                                                 monkeypatch):
        # On Windows another program holding the catalog makes the unlink
        # fail: the previous games must survive it.
        kept = game_path(corpus, "obs-kept")
        kept.parent.mkdir(parents=True)
        kept.write_text("kept\n")
        catalog_path(corpus).write_text("open elsewhere")
        real_unlink = Path.unlink

        def held(self, missing_ok=False):
            if self.name == catalog_path(corpus).name:
                raise PermissionError("in use")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", held)
        with pytest.raises(PermissionError):
            write_games(corpus, [_copy(three_round_game)])
        assert kept.read_text() == "kept\n"
        assert sorted(path.name for path in corpus.iterdir()) == [
            "catalog.sqlite", "games"]

    def test_no_copy_leaves_an_empty_games(self, corpus):
        assert write_games(corpus, []) == 0
        assert list((corpus / "games").iterdir()) == []

    def test_a_rebuild_writes_the_same_bytes(self, corpus, three_round_game):
        # A second build over the same copies replaces the first rather than
        # appending to it: the append trap the offline parse falls into.
        path = game_path(corpus, "engine-20260910T181815Z-a1b2c3")
        write_games(corpus, [_copy(three_round_game)])
        first = path.read_bytes()
        write_games(corpus, [_copy(three_round_game)])
        assert path.read_bytes() == first
        assert sorted(entry.name for entry in corpus.iterdir()) == ["games"]
