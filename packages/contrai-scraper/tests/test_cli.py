"""Pins the CLI: dispatch, the limits, the profile check, what parse writes."""

import asyncio
import signal
import sys

import pytest
from contrai_data import load_game

from contrai_scraper import ScoreboardReading, Translator, read_snapshot
from contrai_scraper.cli import (
    _orientation_result,
    _reconfigure_streams,
    _treat_sigterm_as_interrupt,
    main,
)


class TestDispatch:
    def test_run_is_still_the_default_subcommand(self, monkeypatch, profile_path):
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._run_recorder",
                            lambda args: seen.append(args.profile) or 0)
        assert main(["--profile", str(profile_path)]) == 0
        assert seen == [profile_path]

    def test_a_bare_invocation_now_asks_for_a_profile(self):
        # The v1 flow was the last one that ran without one.
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2

    def test_check_profile_is_not_swallowed_by_the_default(self, monkeypatch,
                                                            profile_path):
        # It has to be in SUBCOMMANDS or it normalises to
        # "run check-profile ..." and argparse rejects the positional.
        monkeypatch.setattr("contrai_scraper.cli._run_check", lambda args: 0)
        assert main(["check-profile", str(profile_path)]) == 0

    def test_parse_is_dispatched(self, monkeypatch, tmp_path, profile_path):
        called = []
        monkeypatch.setattr(
            "contrai_scraper.cli._run_parse", lambda args: called.append(args) or 0
        )
        main(["parse", str(tmp_path), "--profile", str(profile_path)])
        assert len(called) == 1

    def test_an_unknown_subcommand_exits_two(self):
        with pytest.raises(SystemExit) as exc:
            main(["nonsense"])
        assert exc.value.code == 2

    def test_parse_needs_a_profile(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            main(["parse", str(tmp_path)])
        assert exc.value.code == 2

    def test_run_refuses_a_profile_it_cannot_read(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("[site\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            main(["run", "--profile", str(bad)])
        assert exc.value.code == 2


class TestLimits:
    def test_minutes_becomes_a_second_limit(self, monkeypatch, profile_path):
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._watch",
                            lambda p, limits, headless: seen.append(limits))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path), "--minutes", "20"])
        assert seen[0].max_seconds == 1200.0

    def test_max_games_reaches_the_recorder(self, monkeypatch, profile_path):
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._watch",
                            lambda p, limits, headless: seen.append(limits))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path), "--max-games", "2"])
        assert (seen[0].max_games, seen[0].max_seconds) == (2, None)

    def test_headless_overrides_the_profile(self, monkeypatch, profile_path):
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._watch",
                            lambda p, limits, headless: seen.append(headless))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path), "--headless"])
        main(["run", "--profile", str(profile_path), "--headed"])
        assert seen == [True, False]

    def test_the_profile_decides_by_default(self, monkeypatch, profile_path):
        # ``None`` is what tells the launcher to read [browser].headless;
        # defaulting to False here would silently ignore the profile.
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._watch",
                            lambda p, limits, headless: seen.append(headless))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path)])
        assert seen == [None]


class TestSigterm:
    def test_sigterm_cancels_the_run_the_way_ctrl_c_does(self):
        # A service stop is SIGTERM, and Python's default for it is to die
        # without raising — the recorder's interrupt handler would never run.
        previous = signal.getsignal(signal.SIGTERM)
        seen: list[str] = []

        async def scenario():
            _treat_sigterm_as_interrupt()
            handler = signal.getsignal(signal.SIGTERM)
            asyncio.get_running_loop().call_soon(handler, signal.SIGTERM, None)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                seen.append("cancelled")
                raise

        try:
            with pytest.raises(KeyboardInterrupt):
                asyncio.run(scenario())
        finally:
            signal.signal(signal.SIGTERM, previous)
        assert seen == ["cancelled"]

    def test_without_a_callable_sigint_handler_sigterm_is_left_alone(self, monkeypatch):
        # Outside asyncio's own installation — an embedded interpreter, a
        # thread that is not the main one — there is nothing to copy across,
        # and overwriting SIGTERM with a sentinel would be worse than leaving
        # the default in place.
        installed: list[object] = []
        monkeypatch.setattr(signal, "getsignal", lambda number: signal.SIG_IGN)
        monkeypatch.setattr(
            signal, "signal", lambda number, handler: installed.append(handler)
        )
        _treat_sigterm_as_interrupt()
        assert installed == []

    def test_an_interrupted_run_exits_130(self, monkeypatch, profile_path):
        # 130 is what a shell reports for a process an interrupt stopped, and
        # it is what tells a supervisor this was a stop rather than a fault.
        def interrupted(coroutine):
            coroutine.close()
            raise KeyboardInterrupt

        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", interrupted)
        assert main(["run", "--profile", str(profile_path)]) == 130


class TestStreams:
    def test_the_streams_are_utf8_before_dispatch(self, monkeypatch, profile_path):
        # A parse note can carry a card, a card carries a suit glyph, and a
        # legacy Windows console raises on one. The engine hit this exact
        # fault in b144b5d; the fix has to land before anything prints.
        seen: list[str] = []
        monkeypatch.setattr(
            "contrai_scraper.cli._reconfigure_streams",
            lambda: seen.append("utf-8"),
        )
        monkeypatch.setattr(
            "contrai_scraper.cli._run_check",
            lambda args: seen.append("dispatched") or 0,
        )
        assert main(["check-profile", str(profile_path)]) == 0
        assert seen == ["utf-8", "dispatched"]

    def test_a_stream_that_refuses_is_left_alone(self, monkeypatch, capsys):
        # Some streams are pipes, some are already UTF-8, and a run is not
        # worth failing over either.
        monkeypatch.setattr(sys.stdout, "reconfigure", _refuse, raising=False)
        _reconfigure_streams()
        assert capsys.readouterr().err == ""

    def test_a_stream_with_no_encoding_to_set_is_skipped(self, monkeypatch):
        # A redirected stream may be any file-like object at all, and one
        # without the method is not a reason to refuse to run.
        monkeypatch.setattr(sys, "stdout", _Bare())
        _reconfigure_streams()
        assert isinstance(sys.stdout, _Bare)


class TestCheckProfile:
    def test_a_profile_that_does_not_load_fails_the_check(self, tmp_path, capsys):
        bad = tmp_path / "bad.toml"
        bad.write_text("[site\n", encoding="utf-8")
        code = main(["check-profile", str(bad)])
        assert (code, "profile loads" in capsys.readouterr().out) == (1, True)

    def test_a_crossed_seat_map_fails_before_the_browser_opens(
        self, tmp_path, profile_text, capsys
    ):
        # The rotation check is the one profile mistake no later check could
        # catch, so it runs before anything is launched.
        path = tmp_path / "crossed.toml"
        path.write_text(
            profile_text.replace('right = "E", bottom = "S", left = "W"',
                                 'right = "W", bottom = "S", left = "E"'),
            encoding="utf-8",
        )
        code = main(["check-profile", str(path)])
        assert (code, "rotation holds" in capsys.readouterr().out) == (1, True)

    def test_every_check_passing_exits_zero(self, monkeypatch, profile_path):
        monkeypatch.setattr("contrai_scraper.cli._check", _no_live_checks)
        assert main(["check-profile", str(profile_path)]) == 0

    def test_a_failed_live_check_exits_one(self, monkeypatch, profile_path,
                                            capsys):
        monkeypatch.setattr("contrai_scraper.cli._check", _one_failed_check)
        code = main(["check-profile", str(profile_path)])
        assert (code, "options" in capsys.readouterr().out) == (1, True)


class TestOrientationCheck:
    def test_a_matching_panel_passes(self, profile, builders):
        snapshot = _snapshot_with_a_score(profile, builders)
        name, passed, _ = _orientation_result(
            snapshot, ScoreboardReading(rows=((80, 0),), text="80 0")
        )
        assert (name.startswith("us"), passed) == (True, True)

    def test_a_reversed_panel_fails(self, profile, builders):
        snapshot = _snapshot_with_a_score(profile, builders)
        _, passed, detail = _orientation_result(
            snapshot, ScoreboardReading(rows=((0, 80),), text="0 80")
        )
        assert (passed, "[0, 80]" in detail) == (False, True)

    def test_nothing_scored_yet_is_nothing_to_compare(self, profile, builders):
        snapshot = read_snapshot(
            builders.snapshot_payload(rows=(), round_index=None),
            Translator(profile),
        )
        _, passed, detail = _orientation_result(
            snapshot, ScoreboardReading(rows=(), text="")
        )
        assert (passed, "no scored round" in detail) == (True, True)


class _Bare:
    """A stream-shaped object with nothing to reconfigure."""


def _refuse(**kwargs):
    """A stream that will not be reconfigured."""

    raise ValueError("this stream is not a console")


async def _no_live_checks(profile, headless):
    """A live pass that got as far as the browser and found nothing wrong."""

    return []


async def _one_failed_check(profile, headless):
    """A live pass that found the table playing another ruleset."""

    return [("options match [rules.options]", False, "differing ['opt_beta']")]


def _snapshot_with_a_score(profile, builders):
    """A snapshot carrying one scored round, for the orientation check."""

    return read_snapshot(
        builders.snapshot_payload(rows=[builders.score_row()], round_index=1),
        Translator(profile),
    )


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
