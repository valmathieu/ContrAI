"""Pins the CLI: subcommand dispatch, the default, and what parse writes."""

import pytest
from contrai_data import load_game

from contrai_scraper.cli import main


class TestDispatch:
    def test_no_subcommand_still_runs_the_v1_flow(self, monkeypatch):
        # Bare ``contrai-scrape`` predates subcommands and must keep working.
        called = []
        monkeypatch.setattr(
            "contrai_scraper.cli._run_browser", lambda args: called.append(args)
        )
        main([])
        assert len(called) == 1

    def test_parse_is_dispatched(self, monkeypatch, tmp_path, profile_path):
        called = []
        monkeypatch.setattr(
            "contrai_scraper.cli._run_parse", lambda args: called.append(args) or 0
        )
        main(["parse", str(tmp_path), "--profile", str(profile_path)])
        assert len(called) == 1

    def test_the_browser_flow_is_started_on_the_right_loop(self, monkeypatch):
        # The v1 flow needs Playwright's proactor loop on Windows. What is
        # checked here is only that the default subcommand reaches it — the
        # browser itself is never launched, and CI has none installed.
        ran = []
        monkeypatch.setattr("contrai_scraper.cli.scrape", lambda: "session")
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", ran.append)
        assert main([]) == 0
        assert ran == ["session"]

    def test_an_unknown_subcommand_exits_two(self):
        with pytest.raises(SystemExit) as exc:
            main(["nonsense"])
        assert exc.value.code == 2

    def test_parse_needs_a_profile(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            main(["parse", str(tmp_path)])
        assert exc.value.code == 2


class TestParse:
    def test_a_raw_log_becomes_a_record(self, tmp_path, profile_path, raw_log_path):
        code = main(["parse", str(raw_log_path), "--profile", str(profile_path),
                     "--out", str(tmp_path)])
        written = list((tmp_path / "games").glob("*.jsonl"))
        assert (code, len(written)) == (0, 1)

    def test_the_written_record_loads_and_projects(
        self, tmp_path, profile_path, raw_log_path
    ):
        main(["parse", str(raw_log_path), "--profile", str(profile_path),
              "--out", str(tmp_path)])
        record = load_game(next((tmp_path / "games").glob("*.jsonl")))
        assert record.complete is True

    def test_dry_run_writes_nothing(self, tmp_path, profile_path, raw_log_path):
        main(["parse", str(raw_log_path), "--profile", str(profile_path),
              "--out", str(tmp_path), "--dry-run"])
        assert not (tmp_path / "games").exists()

    def test_the_output_root_defaults_to_the_profile(
        self, profile, profile_path, raw_log_path
    ):
        main(["parse", str(raw_log_path), "--profile", str(profile_path)])
        assert list((profile.output.root / "games").glob("*.jsonl"))

    def test_a_directory_is_searched_for_raw_logs(
        self, tmp_path, profile_path, raw_log_path
    ):
        code = main(["parse", str(raw_log_path.parent.parent), "--profile",
                     str(profile_path), "--out", str(tmp_path)])
        assert (code, len(list((tmp_path / "games").glob("*.jsonl")))) == (0, 1)

    def test_a_log_that_parses_to_nothing_exits_one(
        self, tmp_path, profile_path, capsys
    ):
        empty = tmp_path / "raw" / "nothing.jsonl"
        empty.parent.mkdir()
        empty.write_text("", encoding="utf-8")
        assert main(["parse", str(empty), "--profile", str(profile_path),
                     "--out", str(tmp_path)]) == 1

    def test_a_path_that_does_not_exist_exits_two(self, tmp_path, profile_path):
        with pytest.raises(SystemExit) as exc:
            main(["parse", str(tmp_path / "absent.jsonl"), "--profile",
                  str(profile_path)])
        assert exc.value.code == 2

    def test_a_bad_profile_exits_two(self, tmp_path, raw_log_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("[site\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            main(["parse", str(raw_log_path), "--profile", str(bad)])
        assert exc.value.code == 2


class TestReporting:
    def test_each_log_reports_its_game_and_round_count(
        self, tmp_path, profile_path, raw_log_path, capsys
    ):
        main(["parse", str(raw_log_path), "--profile", str(profile_path),
              "--out", str(tmp_path)])
        printed = capsys.readouterr().out
        assert "obs-g1" in printed and "2 rounds" in printed

    def test_a_skipped_round_is_reported(
        self, tmp_path, profile_path, partial_raw_log_path, capsys
    ):
        main(["parse", str(partial_raw_log_path), "--profile", str(profile_path),
              "--out", str(tmp_path)])
        assert "skipped" in capsys.readouterr().out
