"""Pins ``contrai-scrape corpus``: ``build`` (raw logs in, one record per game
out), then ``backup`` and ``check``."""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from contrai_data import (
    RecordCopy,
    RecordFormatError,
    build_catalog,
    catalog_path,
    game_path,
    load_game,
    read_events,
    verdicts_dir,
)

from contrai_scraper import RawFrame, RawLogWriter, raw_path
from contrai_scraper.cli import main


@pytest.fixture
def root(tmp_path) -> Path:
    """The corpus root — not ``tmp_path/corpus``, where the fixture logs sit."""

    return tmp_path / "the-corpus"


def _build(root: Path, profile_path: Path, *sources: str) -> int:
    argv = ["corpus", "build", "--profile", str(profile_path), "--corpus", str(root)]
    for source in sources:
        argv += ["--source", source]
    return main(argv)


def _report(root: Path) -> dict:
    return json.loads((root / "build.json").read_text(encoding="utf-8"))


def _write_log(path: Path, frames: list[tuple[str, int]]) -> Path:
    with RawLogWriter(path) as log:
        for index, (text, socket) in enumerate(frames):
            log.write_frame(
                RawFrame(socket=socket, direction="recv", at=index / 10, text=text)
            )
    return path


class TestBuild:
    def test_the_most_complete_copy_of_a_game_seen_twice_is_kept(
        self, root, profile_path, raw_log_path, partial_raw_log_path
    ):
        # The box saw the whole game, the laptop joined after the first deal.
        code = _build(root, profile_path, f"box={raw_log_path.parent}",
                      f"laptop={partial_raw_log_path.parent}")
        assert code == 0
        assert [path.name for path in (root / "games").iterdir()] == ["obs-g1.jsonl"]
        assert len(load_game(game_path(root, "obs-g1")).rounds) == 2
        duplicate = _report(root)["duplicates"][0]
        assert (duplicate["chosen"]["origin"],
                [(r["origin"], r["reason"]) for r in duplicate["rejected"]]) == (
            "raw/box/session-1.jsonl",
            [("raw/laptop/session-2.jsonl", "fewer scored rounds (1 against 2)")])

    def test_the_raw_logs_are_kept_under_their_source(
        self, root, profile_path, raw_log_path, partial_raw_log_path
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}",
               f"laptop={partial_raw_log_path.parent}")
        assert sorted(p.relative_to(root).as_posix()
                      for p in (root / "raw").rglob("*.jsonl")) == [
            "raw/box/session-1.jsonl", "raw/laptop/session-2.jsonl"]
        assert (root / "raw" / "box" / "session-1.jsonl").read_bytes() == (
            raw_log_path.read_bytes())

    def test_the_report_accounts_for_the_build(
        self, root, profile_path, raw_log_path, partial_raw_log_path
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}",
               f"laptop={partial_raw_log_path.parent}")
        report = _report(root)
        assert {key: report[key] for key in (
            "imported", "raw_logs", "visits", "not_tournament", "unusable",
            "copies", "games")} == {
            "imported": {
                "box": {"copied": 1, "grown": 0, "present": 0, "stale": 0},
                "laptop": {"copied": 1, "grown": 0, "present": 0, "stale": 0},
            },
            "raw_logs": {"box": 1, "laptop": 1},
            "visits": 2, "not_tournament": 0, "unusable": [],
            "copies": 2, "games": 1,
        }
        assert report["generator"].startswith("contrai-scraper")

    def test_it_prints_the_follow_up_commands(
        self, root, profile_path, raw_log_path, capsys
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}")
        printed = capsys.readouterr().out
        assert "1 copied, 0 grown, 0 already present" in printed
        assert "-> 1 copies of 1 games, 0 seen more than once" in printed
        assert "contrai verify" in printed and "contrai catalog" in printed

    def test_a_game_two_logs_of_one_source_saw_is_written_once(
        self, root, tmp_path, profile_path, source_game, synthesize
    ):
        # The append trap: ``parse`` over this directory would write both
        # logs' records of obs-g1 into one file, one after the other.
        directory = tmp_path / "box-raw"
        _write_log(raw_path(directory, "session-a"), synthesize(source_game))
        _write_log(raw_path(directory, "session-b"), synthesize(source_game))
        assert _build(root, profile_path, f"box={directory}") == 0
        events = read_events(game_path(root, "obs-g1")).events
        assert sum(1 for event in events if type(event).__name__ == "Header") == 1
        assert _report(root)["duplicates"][0]["rejected"][0]["reason"] == (
            "a tie, kept the first origin in sorted order")


class TestRebuild:
    def test_a_rebuild_is_idempotent(self, root, profile_path, raw_log_path,
                                     partial_raw_log_path, capsys):
        sources = (f"box={raw_log_path.parent}", f"laptop={partial_raw_log_path.parent}")
        _build(root, profile_path, *sources)
        first = game_path(root, "obs-g1").read_bytes()
        capsys.readouterr()
        assert _build(root, profile_path, *sources) == 0
        assert game_path(root, "obs-g1").read_bytes() == first
        assert "0 copied, 0 grown, 1 already present" in capsys.readouterr().out

    def test_no_source_rebuilds_from_the_corpus_raw_logs(
        self, root, profile_path, raw_log_path
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}")
        first = game_path(root, "obs-g1").read_bytes()
        shutil.rmtree(root / "games")
        assert _build(root, profile_path) == 0
        assert game_path(root, "obs-g1").read_bytes() == first

    def test_a_rebuild_removes_the_stale_verdicts_and_catalog(
        self, root, profile_path, raw_log_path
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}")
        verdicts_dir(root).mkdir()
        (verdicts_dir(root) / "obs-g1.json").write_text("{}")
        catalog_path(root).write_text("stale")
        _build(root, profile_path)
        assert (verdicts_dir(root).exists(), catalog_path(root).exists()) == (False, False)


class TestRefusals:
    def test_a_log_whose_name_is_taken_refuses_the_build(
        self, root, tmp_path, profile_path, raw_log_path, capsys
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}")
        before = game_path(root, "obs-g1").read_bytes()
        other = tmp_path / "other" / raw_log_path.name
        other.parent.mkdir()
        other.write_text('{"different": true}\n', encoding="utf-8")
        assert _build(root, profile_path, f"box={other}") == 1
        assert "corpus build refused" in capsys.readouterr().err
        assert game_path(root, "obs-g1").read_bytes() == before

    def test_a_label_that_is_not_a_plain_segment_refuses_the_build(
        self, root, profile_path, raw_log_path, capsys
    ):
        assert _build(root, profile_path, f"../up={raw_log_path}") == 1
        assert "source label" in capsys.readouterr().err

    @pytest.mark.parametrize("value", ["box", "=dir", "box="])
    def test_a_source_that_is_not_label_equals_dir_is_a_usage_error(
        self, root, profile_path, value
    ):
        with pytest.raises(SystemExit) as exc:
            _build(root, profile_path, value)
        assert exc.value.code == 2

    def test_a_source_that_does_not_exist_is_a_usage_error(
        self, root, tmp_path, profile_path
    ):
        with pytest.raises(SystemExit) as exc:
            _build(root, profile_path, f"box={tmp_path / 'absent'}")
        assert exc.value.code == 2

    def test_corpus_needs_a_subcommand(self):
        with pytest.raises(SystemExit) as exc:
            main(["corpus"])
        assert exc.value.code == 2

    def test_no_game_leaves_the_previous_games_alone(
        self, root, tmp_path, profile_path, capsys
    ):
        kept = game_path(root, "obs-kept")
        kept.parent.mkdir(parents=True)
        kept.write_text("kept\n")
        empty = tmp_path / "empty" / "nothing.jsonl"
        empty.parent.mkdir()
        empty.write_text("", encoding="utf-8")
        assert _build(root, profile_path, f"box={empty}") == 1
        assert "held no game" in capsys.readouterr().out
        assert kept.read_text() == "kept\n"


class TestUnusableCopies:
    def test_a_copy_that_cannot_be_folded_is_reported_and_left_out(
        self, root, profile_path, two_table_raw_log_path, monkeypatch, capsys
    ):
        # A parser bug on one game must not cost the corpus the others.
        class Brittle(RecordCopy):
            @classmethod
            def of(cls, source, origin, events):
                if events[0].game_id == "obs-g2":
                    raise RecordFormatError("Round 3 is dealt twice")
                return RecordCopy.of(source, origin, events)

        monkeypatch.setattr("contrai_scraper.cli.RecordCopy", Brittle)
        assert _build(root, profile_path, f"box={two_table_raw_log_path}") == 0
        assert [p.name for p in (root / "games").iterdir()] == ["obs-g1.jsonl"]
        assert _report(root)["unusable"] == [{
            "origin": "raw/box/session-2.jsonl", "game_id": "obs-g2",
            "reason": "Round 3 is dealt twice"}]
        assert "obs-g2 could not be folded" in capsys.readouterr().out


class TestOffline:
    def test_the_account_variables_are_not_needed(
        self, root, tmp_path, profile_text, raw_log_path, monkeypatch
    ):
        for name in ("CONTRAI_SCRAPER_EMAIL", "CONTRAI_HOME_IP"):
            monkeypatch.delenv(name, raising=False)
        indirected = tmp_path / "indirected-profile.toml"
        indirected.write_text(
            profile_text
            .replace('email = "watcher@example.invalid"',
                     'email = "env:CONTRAI_SCRAPER_EMAIL"')
            .replace('home_ip = "198.51.100.1"', 'home_ip = "env:CONTRAI_HOME_IP"'),
            encoding="utf-8")
        assert _build(root, indirected, f"box={raw_log_path}") == 0


class TestBackupCommands:
    @pytest.fixture
    def built(self, root, profile_path, raw_log_path) -> Path:
        _build(root, profile_path, f"box={raw_log_path.parent}")
        return root

    def _archive(self, out: Path) -> Path:
        (archive,) = out.glob("contrai-corpus-*.zip")
        return archive

    def test_a_backup_is_written_and_named(self, built, tmp_path, capsys):
        assert main(["corpus", "backup", str(built), "--to", str(tmp_path / "out")]) == 0
        archive = self._archive(tmp_path / "out")
        printed = capsys.readouterr().out
        assert f"{archive}: 3 files (1 raw logs, 1 games)" in printed
        assert "corpus check" in printed

    def test_a_fresh_backup_checks_clean(self, built, tmp_path, capsys):
        main(["corpus", "backup", str(built), "--to", str(tmp_path / "out")])
        capsys.readouterr()
        assert main(["corpus", "check", str(self._archive(tmp_path / "out"))]) == 0
        assert "ok: 3 files match the manifest" in capsys.readouterr().out

    def test_a_damaged_backup_fails_its_check(self, built, tmp_path, capsys):
        main(["corpus", "backup", str(built), "--to", str(tmp_path / "out")])
        archive = self._archive(tmp_path / "out")
        with zipfile.ZipFile(archive, "a") as zipped:
            zipped.writestr("games/obs-smuggled.jsonl", "not in the manifest\n")
        capsys.readouterr()
        assert main(["corpus", "check", str(archive)]) == 1
        printed = capsys.readouterr().out
        assert "unlisted: games/obs-smuggled.jsonl" in printed and "FAIL" in printed

    def test_a_restore_rebuilds_the_same_catalog(self, built, tmp_path):
        # A restore is an unzip, then verify and catalog: the games that come
        # back index exactly as the ones that went in.
        main(["corpus", "backup", str(built), "--to", str(tmp_path / "out")])
        restored = tmp_path / "restored"
        with zipfile.ZipFile(self._archive(tmp_path / "out")) as zipped:
            zipped.extractall(restored)
        assert build_catalog(restored).game_count == build_catalog(built).game_count == 1
        assert game_path(restored, "obs-g1").read_bytes() == (
            game_path(built, "obs-g1").read_bytes())

    def test_an_empty_corpus_refuses_a_backup(self, tmp_path, capsys):
        (tmp_path / "empty").mkdir()
        assert main(["corpus", "backup", str(tmp_path / "empty"),
                     "--to", str(tmp_path / "out")]) == 1
        assert "corpus backup refused" in capsys.readouterr().err

    def test_a_file_that_is_not_a_backup_fails_its_check(self, tmp_path, capsys):
        path = tmp_path / "not.zip"
        path.write_text("plain text")
        assert main(["corpus", "check", str(path)]) == 1
        assert "corpus check failed" in capsys.readouterr().err


class TestParseCache:
    def test_a_second_build_reads_every_unchanged_log_from_the_cache(
        self, root, profile_path, raw_log_path, partial_raw_log_path, capsys
    ):
        sources = (f"box={raw_log_path.parent}", f"laptop={partial_raw_log_path.parent}")
        _build(root, profile_path, *sources)
        first = game_path(root, "obs-g1").read_bytes()
        capsys.readouterr()
        assert _build(root, profile_path) == 0
        assert "0 raw logs parsed, 2 read from the cache" in capsys.readouterr().out
        assert game_path(root, "obs-g1").read_bytes() == first
        assert _report(root)["cached_logs"] == 2

    def test_a_cached_log_is_not_parsed_at_all(self, root, profile_path, raw_log_path,
                                               monkeypatch):
        _build(root, profile_path, f"box={raw_log_path.parent}")

        def refuse(log, profile):
            raise AssertionError(f"{log} was parsed again")

        monkeypatch.setattr("contrai_scraper.cli._parse_log", refuse)
        assert _build(root, profile_path) == 0

    def test_only_the_log_that_grew_is_parsed_again(
        self, root, tmp_path, profile_path, source_game, synthesize, capsys
    ):
        # The box's live session log, fetched twice: the second fetch holds
        # the whole game where the first held its opening only.
        frames = synthesize(source_game)
        cut = len(frames) // 2
        early = _write_log(raw_path(tmp_path / "week1", "live"), frames[:cut])
        _write_log(raw_path(tmp_path / "week1", "done"), synthesize(
            source_game, game="g2", table="t2", frame_prefix="other-"))
        _build(root, profile_path, f"box={early.parent}")
        # Grown from the same writer: rewrite the whole session under the
        # same name, whose first half is byte for byte the early fetch.
        grown = raw_path(tmp_path / "week2", "live")
        grown.parent.mkdir(parents=True)
        grown.write_bytes(early.read_bytes())
        with grown.open("a", encoding="utf-8", newline="\n") as handle:
            for index, (text, socket) in enumerate(frames[cut:], start=cut):
                handle.write(json.dumps({"kind": "frame", "socket": socket,
                                         "dir": "recv", "at": index / 10,
                                         "text": text}) + "\n")
        capsys.readouterr()
        assert _build(root, profile_path, f"box={grown}") == 0
        printed = capsys.readouterr().out
        assert "1 grown" in printed and "1 raw logs parsed, 1 read from the cache" in printed
        assert len(load_game(game_path(root, "obs-g1")).rounds) == 2

    def test_full_parses_every_log_again(self, root, profile_path, raw_log_path, capsys):
        _build(root, profile_path, f"box={raw_log_path.parent}")
        capsys.readouterr()
        main(["corpus", "build", "--profile", str(profile_path), "--corpus", str(root),
              "--full"])
        assert "1 raw logs parsed, 0 read from the cache" in capsys.readouterr().out

    def test_the_entry_of_a_log_that_left_raw_is_pruned(
        self, root, profile_path, raw_log_path, partial_raw_log_path
    ):
        _build(root, profile_path, f"box={raw_log_path.parent}",
               f"laptop={partial_raw_log_path.parent}")
        (root / "raw" / "laptop" / partial_raw_log_path.name).unlink()
        _build(root, profile_path)
        assert sorted(p.relative_to(root / "cache").as_posix()
                      for p in (root / "cache").rglob("*.json")) == [
            f"box/{raw_log_path.name}.json"]
