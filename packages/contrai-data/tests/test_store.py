"""Pins the file layer: append-and-flush, truncation tolerance, paths, ids."""

import datetime as dt
import re

import pytest

from contrai_data import (
    RecordFormatError,
    RecordWriter,
    games_dir,
    game_path,
    new_game_id,
    read_events,
    records_root,
)


class TestRecordWriter:
    def test_writes_one_line_per_event(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            for event in three_round_game:
                writer.write(event)
        assert path.read_text(encoding="utf-8").count("\n") == len(three_round_game)

    def test_round_trips_the_whole_game(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            for event in three_round_game:
                writer.write(event)
        assert read_events(path).events == tuple(three_round_game)

    def test_each_line_is_readable_before_the_file_is_closed(
        self, tmp_path, three_round_game
    ):
        # The whole point of flushing per line: a crash mid-game must lose
        # at most the event being written.
        path = tmp_path / "game.jsonl"
        writer = RecordWriter(path)
        writer.write(three_round_game[0])
        writer.write(three_round_game[1])
        assert len(read_events(path).events) == 2
        writer.close()

    def test_appends_to_an_existing_file(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            writer.write(three_round_game[0])
        with RecordWriter(path) as writer:
            writer.write(three_round_game[1])
        assert len(read_events(path).events) == 2

    def test_creates_missing_parent_directories(self, tmp_path, three_round_game):
        path = tmp_path / "root" / "games" / "engine-x.jsonl"
        with RecordWriter(path) as writer:
            writer.write(three_round_game[0])
        assert path.exists()

    def test_accepts_a_path_as_a_string(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(str(path)) as writer:
            writer.write(three_round_game[0])
        assert read_events(str(path)).events == (three_round_game[0],)

    def test_line_endings_are_lf_on_every_platform(self, tmp_path, three_round_game):
        # Records travel between the Windows dev machine and the Debian box
        # that runs the scraper; a CR would land inside the last token of
        # every line for whichever of the two did not write it.
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            writer.write(three_round_game[0])
        assert b"\r" not in path.read_bytes()

    def test_writing_after_close_is_refused(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        writer = RecordWriter(path)
        writer.close()
        assert writer.closed is True
        with pytest.raises(ValueError):
            writer.write(three_round_game[0])

    def test_closing_twice_is_harmless(self, tmp_path):
        writer = RecordWriter(tmp_path / "game.jsonl")
        writer.close()
        writer.close()
        assert writer.closed is True

    def test_exposes_its_path(self, tmp_path):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            assert writer.path == path

    def test_is_open_until_closed(self, tmp_path):
        writer = RecordWriter(tmp_path / "game.jsonl")
        assert writer.closed is False
        writer.close()

    def test_the_context_manager_closes_on_an_exception(
        self, tmp_path, three_round_game
    ):
        path = tmp_path / "game.jsonl"
        writer = RecordWriter(path)
        with pytest.raises(RuntimeError):
            with writer:
                writer.write(three_round_game[0])
                raise RuntimeError("boom")
        assert writer.closed is True
        assert len(read_events(path).events) == 1


class TestReadEvents:
    def test_an_absent_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_events(tmp_path / "nope.jsonl")

    def test_an_empty_file_reads_as_no_events(self, tmp_path):
        path = tmp_path / "game.jsonl"
        path.write_text("", encoding="utf-8")
        result = read_events(path)
        assert (result.events, result.truncated) == ((), False)

    def test_blank_lines_are_skipped(self, tmp_path, three_round_game):
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            writer.write(three_round_game[0])
        path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
        assert len(read_events(path).events) == 1

    def test_a_truncated_last_line_is_dropped_and_reported(
        self, tmp_path, three_round_game
    ):
        # What a crash mid-write leaves behind: a final line that is not
        # JSON yet. Everything before it is intact and must still load.
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            for event in three_round_game[:4]:
                writer.write(event)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"event": "card_pla')
        result = read_events(path)
        assert result.truncated is True
        assert result.events == tuple(three_round_game[:4])

    def test_a_single_truncated_line_leaves_no_events(self, tmp_path):
        path = tmp_path / "game.jsonl"
        path.write_text('{"event": "hea', encoding="utf-8")
        result = read_events(path)
        assert (result.events, result.truncated) == ((), True)

    def test_a_malformed_line_in_the_middle_is_an_error(
        self, tmp_path, three_round_game
    ):
        # Only the *last* line can be a crash artefact. Anywhere else it is
        # corruption, and swallowing it would silently drop an event.
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            for event in three_round_game[:4]:
                writer.write(event)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines.insert(2, '{"event": "card_pla')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(RecordFormatError, match="JSON"):
            read_events(path)

    def test_a_well_formed_last_line_with_a_bad_token_is_an_error(
        self, tmp_path, three_round_game
    ):
        # A crash cannot produce valid JSON with an invalid token — that is
        # a producer bug, so tolerance must not extend to it.
        path = tmp_path / "game.jsonl"
        with RecordWriter(path) as writer:
            writer.write(three_round_game[0])
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"event": "belote", "round": 1, "position": "X"}\n')
        with pytest.raises(RecordFormatError):
            read_events(path)


class TestLayout:
    def test_records_root_follows_contrai_home(self, contrai_home):
        assert records_root() == contrai_home / "records"

    def test_records_root_falls_back_to_the_user_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CONTRAI_HOME", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        assert records_root() == tmp_path / ".contrai" / "records"

    def test_games_live_under_games(self, tmp_path):
        assert games_dir(tmp_path) == tmp_path / "games"

    def test_a_game_path_is_its_id_dot_jsonl(self, tmp_path):
        assert game_path(tmp_path, "obs-56630b35") == (
            tmp_path / "games" / "obs-56630b35.jsonl"
        )

    @pytest.mark.parametrize("game_id", ["", "a/b", "..", ".", "a\\b", "a.jsonl/../x"])
    def test_a_game_id_that_is_not_one_path_segment_is_refused(self, tmp_path, game_id):
        # The id becomes a file name, and both producers take it from
        # outside: the scraper from the wire, the engine from a flag.
        with pytest.raises(RecordFormatError, match="game id"):
            game_path(tmp_path, game_id)


class TestGameIds:
    def test_shape(self):
        game_id = new_game_id(
            now=dt.datetime(2026, 9, 10, 18, 18, 15, tzinfo=dt.UTC), entropy="a1b2c3"
        )
        assert game_id == "engine-20260910T181815Z-a1b2c3"

    def test_prefix_is_configurable(self):
        assert new_game_id("obs").startswith("obs-")

    def test_generated_ids_are_well_formed_and_distinct(self):
        ids = {new_game_id() for _ in range(50)}
        assert len(ids) == 50
        for game_id in ids:
            assert re.fullmatch(r"engine-\d{8}T\d{6}Z-[0-9a-f]{6}", game_id)

    def test_a_generated_id_is_a_usable_file_name(self, tmp_path):
        assert game_path(tmp_path, new_game_id()).suffix == ".jsonl"
