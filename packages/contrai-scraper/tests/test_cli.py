"""Pins the CLI: dispatch, the limits, the profile check, what parse writes."""

import asyncio
import re
import signal
import sys

import pytest
from contrai_data import load_game

from contrai_scraper import (
    BrowserError,
    EgressReading,
    EgressRefusal,
    OptionsReading,
    RawFrame,
    ScoreboardReading,
    ShiftError,
    Translator,
    read_snapshot,
)
from contrai_scraper.cli import (
    _egress_result,
    _live_checks,
    _orientation_result,
    _reconfigure_streams,
    _treat_sigterm_as_interrupt,
    main,
)

#: What a working tunnel says, in documentation addresses.
OPEN = EgressReading(refusal=None, exit_ip="203.0.113.7", country="XX", route_device=None)


@pytest.fixture(autouse=True)
def open_egress(monkeypatch):
    """check-profile asks the network for its exit address; no test here may."""

    monkeypatch.setattr("contrai_scraper.cli._egress_reading", lambda profile: OPEN)


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
        monkeypatch.setattr("contrai_scraper.cli._shift",
                            lambda p, limits, headless: seen.append(limits))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path), "--minutes", "20"])
        assert seen[0].max_seconds == 1200.0

    def test_max_games_reaches_the_recorder(self, monkeypatch, profile_path):
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._shift",
                            lambda p, limits, headless: seen.append(limits))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path), "--max-games", "2"])
        assert (seen[0].max_games, seen[0].max_seconds) == (2, None)

    def test_headless_overrides_the_profile(self, monkeypatch, profile_path):
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._shift",
                            lambda p, limits, headless: seen.append(headless))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path), "--headless"])
        main(["run", "--profile", str(profile_path), "--headed"])
        assert seen == [True, False]

    def test_the_profile_decides_by_default(self, monkeypatch, profile_path):
        # ``None`` is what tells the launcher to read [browser].headless;
        # defaulting to False here would silently ignore the profile.
        seen = []
        monkeypatch.setattr("contrai_scraper.cli._shift",
                            lambda p, limits, headless: seen.append(headless))
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        main(["run", "--profile", str(profile_path)])
        assert seen == [None]

    def test_an_exhausted_shift_exits_3(self, monkeypatch, profile_path, capsys):
        # A spent budget is the process handing itself back: the supervisor
        # restarts it, and a code of its own says why it stopped.
        def exhausted(coroutine):
            coroutine.close()
            raise ShiftError("egress refused 6 times in a row")

        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", exhausted)
        code = main(["run", "--profile", str(profile_path)])
        assert (code, "refused" in capsys.readouterr().err) == (3, True)


class TestFleetCommand:
    """``fleet``: what it is handed, what it refuses, what it exits with."""

    @pytest.fixture
    def handed(self, monkeypatch):
        """What ``fleet`` handed the async run, instead of running it."""

        seen = {}

        def capture(profile, accounts, limits, headless):
            seen.update(accounts=accounts, limits=limits, headless=headless)

        monkeypatch.setattr("contrai_scraper.cli._fleet", capture)
        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", lambda c: c)
        return seen

    @staticmethod
    def _accounts_file(tmp_path, count):
        path = tmp_path / "fixture-accounts.toml"
        path.write_text("".join(
            f'[bot{index:02}]\nemail = "bot{index:02}@example.invalid"\n'
            'verification_code = "0000"\n'
            for index in range(1, count + 1)
        ), encoding="utf-8")
        return path

    def test_without_accounts_it_is_one_worker_on_the_profiles_account(
        self, handed, profile_path, profile
    ):
        assert main(["fleet", "--profile", str(profile_path)]) == 0
        (only,) = handed["accounts"]
        assert (only.label, only.account.email) == ("bot01", profile.account.email)

    def test_with_accounts_it_takes_the_profiles_worker_count(
        self, handed, profile_path, tmp_path
    ):
        main(["fleet", "--profile", str(profile_path),
              "--accounts", str(self._accounts_file(tmp_path, 3))])
        assert [item.label for item in handed["accounts"]] == ["bot01", "bot02"]

    def test_workers_on_the_command_line_win(self, handed, profile_path, tmp_path):
        main(["fleet", "--profile", str(profile_path), "--workers", "3",
              "--accounts", str(self._accounts_file(tmp_path, 3))])
        assert len(handed["accounts"]) == 3

    def test_the_limits_and_the_window_mode_reach_the_fleet(self, handed, profile_path):
        main(["fleet", "--profile", str(profile_path), "--minutes", "20",
              "--max-games", "4", "--headless"])
        assert (handed["limits"].max_seconds, handed["limits"].max_games,
                handed["headless"]) == (1200.0, 4, True)

    def test_more_workers_than_accounts_is_a_usage_error(self, handed, profile_path,
                                                        tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["fleet", "--profile", str(profile_path), "--workers", "3",
                  "--accounts", str(self._accounts_file(tmp_path, 2))])
        assert (exc.value.code, "holds 2 account(s)" in capsys.readouterr().err) == (
            2, True)

    def test_several_workers_without_accounts_is_a_usage_error(self, handed,
                                                                profile_path):
        with pytest.raises(SystemExit) as exc:
            main(["fleet", "--profile", str(profile_path), "--workers", "2"])
        assert exc.value.code == 2

    @pytest.mark.parametrize("workers", ["0", "11"])
    def test_a_worker_count_out_of_range_is_a_usage_error(self, handed, profile_path,
                                                          tmp_path, workers):
        with pytest.raises(SystemExit):
            main(["fleet", "--profile", str(profile_path), "--workers", workers,
                  "--accounts", str(self._accounts_file(tmp_path, 3))])

    def test_accounts_that_do_not_load_are_a_usage_error(self, handed, profile_path,
                                                         tmp_path):
        with pytest.raises(SystemExit) as exc:
            main(["fleet", "--profile", str(profile_path),
                  "--accounts", str(tmp_path / "absent.toml")])
        assert exc.value.code == 2

    def test_a_profile_that_cannot_run_a_fleet_is_a_usage_error(
        self, handed, tmp_path, profile_text, capsys
    ):
        path = tmp_path / "no-fleet.toml"
        path.write_text(profile_text.split("\n[fleet]\n")[0], encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            main(["fleet", "--profile", str(path)])
        assert (exc.value.code, "[fleet] section" in capsys.readouterr().err) == (2, True)

    def test_a_fleet_that_hands_itself_back_exits_3(self, monkeypatch, profile_path):
        def down(coroutine):
            coroutine.close()
            raise ShiftError("2 of 3 workers are down")

        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", down)
        assert main(["fleet", "--profile", str(profile_path)]) == 3

    def test_an_interrupted_fleet_exits_130(self, monkeypatch, profile_path):
        def interrupted(coroutine):
            coroutine.close()
            raise KeyboardInterrupt

        monkeypatch.setattr("contrai_scraper.cli.asyncio.run", interrupted)
        assert main(["fleet", "--profile", str(profile_path)]) == 130


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


class TestCheckProfileEgress:
    def test_a_blocked_egress_fails_before_any_browser_opens(self, monkeypatch,
                                                             profile_path, capsys):
        opened = []
        monkeypatch.setattr(
            "contrai_scraper.cli._egress_reading",
            lambda profile: EgressReading(refusal=EgressRefusal.EXIT_IS_HOME,
                                          exit_ip=None, country="XX", route_device=None),
        )
        monkeypatch.setattr("contrai_scraper.cli._check",
                            lambda profile, headless: opened.append(True))
        code = main(["check-profile", str(profile_path)])
        assert (code, "exit_is_home" in capsys.readouterr().out, opened) == (1, True, [])

    def test_an_open_egress_names_the_exit_and_an_unchecked_route(self):
        name, passed, detail = _egress_result(OPEN)
        assert (name, passed, "203.0.113.7" in detail, "not checked" in detail) == (
            "egress leaves through the tunnel", True, True, True
        )


class FakeWalk:
    """A spectator whose walk and panels are scripted; a named step may raise."""

    def __init__(self, *, pledge=False, fail=None, marker=True, ids=None,
                 saved=None):
        self._pledge = pledge
        self._fail = fail or {}
        self._marker = marker
        self._ids = ids or {}
        self._saved = saved
        self.captured = []

    async def capture(self, stem):
        """Record the stem asked for, and answer with the scripted files."""

        self.captured.append(stem)
        return tuple(stem.with_suffix(suffix) for suffix in self._saved or ())

    def _step(self, name):
        if name in self._fail:
            raise BrowserError(self._fail[name])

    async def log_in(self):
        self._step("log_in")

    async def enter_variant(self):
        self._step("enter_variant")
        return self._pledge

    async def read_tournament_marker(self):
        return self._marker

    async def read_options(self, expected):
        self._step("read_options")
        return OptionsReading(observed=dict(expected), missing=(), extra=(), differing=())

    async def read_player_id(self, position):
        return self._ids.get(position)

    async def read_scoreboard(self):
        return ScoreboardReading(rows=(), text="")


class Frames:
    """A frame source that yields its frames, then ends."""

    def __init__(self, *frames):
        self._frames = list(frames)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def _received(text):
    return RawFrame(socket=0, direction="recv", at=0.0, text=text)


def _join_frame(builders):
    """The join snapshot a freshly seated spectator receives."""

    return _received(
        builders.envelope("payload", "joinTable", builders.snapshot_payload(), frame_id="s0")
    )


def _accounts(snapshot):
    """Seat to the account the wire names there — what an honest panel shows."""

    return {
        position: None if snapshot.players.get(handle) is None
        else snapshot.players[handle].account
        for handle, position in snapshot.seats.items()
    }


class TestLiveChecks:
    def test_a_missing_login_selector_is_a_failed_line_not_a_traceback(self, profile):
        # The walk is what finds a site change; a traceback would bury the one
        # line that says which profile key stopped matching.
        message = "[selectors].login_start matched nothing that could be clicked"
        results = asyncio.run(_live_checks(FakeWalk(fail={"log_in": message}), Frames(),
                                           profile))
        assert results == [("login", False, message)]

    @pytest.mark.parametrize(("answered", "detail"), [(True, "answered"),
                                                      (False, "not showing")])
    def test_the_pledge_line_reports_what_the_menu_walk_met(self, profile, answered, detail):
        # The dialog is drawn between the menu steps, so only the walk that
        # enters the variant can tell whether it was there.
        results = asyncio.run(_live_checks(FakeWalk(pledge=answered), Frames(), profile))
        assert results[:3] == [
            ("login", True, profile.account.email),
            ("pledge", True, detail),
            ("variant entered", True, "the server chose a table"),
        ]

    def test_a_panel_that_fails_mid_table_names_its_check(self, profile, builders):
        message = "[selectors].options_button matched nothing that could be clicked"
        results = asyncio.run(_live_checks(FakeWalk(fail={"read_options": message}),
                                           Frames(_join_frame(builders)), profile))
        assert results[-1] == ("options match [rules.options]", False, message)

    def test_a_snapshot_this_profile_cannot_read_is_a_failed_line(self, profile, builders):
        # The server seats a spectator wherever it likes, including at a
        # variant table whose contracts the tournament ruleset has no name
        # for. That is as much a profile answer as a missing selector, and
        # this command exists to name it rather than print a traceback.
        payload = builders.snapshot_payload(
            rows=[builders.score_row(suit="everything")]
        )
        frame = _received(
            builders.envelope("payload", "joinTable", payload, frame_id="s0")
        )
        results = asyncio.run(_live_checks(FakeWalk(), Frames(frame), profile))
        step, passed, detail = results[-1]
        assert (step, passed) == ("the snapshot reads", False)
        assert "everything" in detail

    def test_a_table_that_agrees_with_the_profile_passes_every_check(self, profile, builders):
        snapshot = read_snapshot(builders.snapshot_payload(), Translator(profile))
        walk = FakeWalk(marker=bool(snapshot.is_tournament), ids=_accounts(snapshot))
        # A keepalive ahead of the snapshot: the wait skips what is not one.
        results = asyncio.run(_live_checks(walk, Frames(_received("tick"),
                                                        _join_frame(builders)), profile))
        assert ([name for name, passed, _ in results if not passed], len(results)) == ([], 8)

    def test_a_panel_id_that_differs_from_the_wire_fails(self, profile, builders):
        snapshot = read_snapshot(builders.snapshot_payload(), Translator(profile))
        ids = _accounts(snapshot)
        ids[next(iter(ids))] = "999"
        walk = FakeWalk(marker=bool(snapshot.is_tournament), ids=ids)
        results = asyncio.run(_live_checks(walk, Frames(_join_frame(builders)), profile))
        passed = {name: ok for name, ok, _ in results}
        assert passed["panel ids equal the wire's accounts"] is False


class FakeLobbyWalk(FakeWalk):
    """A spectator whose lobby walk is scripted too."""

    def __init__(self, *, table_hash="cfg-42", **kwargs):
        super().__init__(**kwargs)
        self._hash = table_hash
        self.steps: list[str] = []

    async def log_in(self):
        self.steps.append("log_in")
        await super().log_in()

    async def enter_lobby(self):
        self.steps.append("enter_lobby")
        self._step("enter_lobby")
        return False

    async def read_tournament_hash(self):
        self.steps.append("read_tournament_hash")
        return self._hash

    async def enter_table_from_lobby(self):
        self.steps.append("enter_table_from_lobby")
        self._step("enter_table_from_lobby")


def _lobby_frame(builders, data=None, frame_id="l1"):
    """A lobby event, in the fixture vocabulary, for the tournament row."""

    if data is None:
        data = {"key": "cfg-42", "chairs": {"top": {"acct": "095024"}}}
    return _received(builders.envelope("payload", "slot", data, frame_id=frame_id))


class TestLobbyChecks:
    def test_a_lobby_the_profile_describes_passes_every_check(self, profile, builders):
        from contrai_scraper.cli import _lobby_checks

        walk = FakeLobbyWalk()
        frames = Frames(_lobby_frame(builders), _join_frame(builders))
        results = asyncio.run(_lobby_checks(walk, frames, profile))
        assert ([(name, passed) for name, passed, _ in results], walk.steps) == (
            [("lobby entered", True), ("tournament row found", True),
             ("lobby events read", True), ("back to a table from the lobby", True)],
            ["log_in", "enter_lobby", "read_tournament_hash", "enter_table_from_lobby"],
        )

    def test_a_list_with_no_tournament_row_fails_that_line_only(self, profile, builders):
        from contrai_scraper.cli import _lobby_checks

        walk = FakeLobbyWalk(table_hash=None)
        frames = Frames(_lobby_frame(builders), _join_frame(builders))
        results = asyncio.run(_lobby_checks(walk, frames, profile))
        assert [passed for _, passed, _ in results] == [True, False, True, True]

    def test_a_profile_that_cannot_read_the_lobbys_socket_fails_that_line(
        self, profile, builders
    ):
        import dataclasses

        from contrai_scraper.cli import _lobby_checks

        blind = dataclasses.replace(
            profile, wire=dataclasses.replace(
                profile.wire,
                events=dataclasses.replace(profile.wire.events, lobby_table=None)))
        results = asyncio.run(_lobby_checks(FakeLobbyWalk(), Frames(_join_frame(builders)),
                                            blind))
        name, passed, detail = results[2]
        assert (name, passed, "lobby_table" in detail) == ("lobby events read", False, True)

    def test_a_step_that_fails_is_a_line_naming_its_check(self, profile):
        from contrai_scraper.cli import _lobby_checks

        message = "[selectors].lobby_back matched 2 control(s), none of them on the screen shown"
        walk = FakeLobbyWalk(fail={"enter_table_from_lobby": message})
        results = asyncio.run(_lobby_checks(walk, Frames(), profile))
        assert results[-1] == ("back to a table from the lobby", False, message)

    def test_a_table_that_never_describes_itself_fails_the_round_trip(self, profile):
        from contrai_scraper.cli import _lobby_checks

        results = asyncio.run(_lobby_checks(FakeLobbyWalk(), Frames(), profile))
        assert results[-1] == (
            "back to a table from the lobby", False, "no snapshot within the timeout")

    def test_a_profile_with_no_lobby_is_told_so_and_not_failed(self):
        from contrai_scraper.cli import LOBBY_NOT_DESCRIBED

        assert LOBBY_NOT_DESCRIBED[:2] == ("lobby described", True)


class TestLobbyEventLine:
    def _result(self, profile, builders, data):
        from contrai_scraper import WireStream
        from contrai_scraper.cli import _lobby_event_result

        event = WireStream(profile.wire).ingest(
            _lobby_frame(builders, data).text, socket=0)
        return _lobby_event_result(event, profile, "cfg-42")

    def test_the_tournament_rows_event_passes_with_its_seat_count(self, profile, builders):
        assert self._result(profile, builders, None) == (
            "lobby events read", True,
            "an event for the tournament row named 1 seated account(s)")

    def test_another_rows_event_reads_the_paths_just_as_well(self, profile, builders):
        _, passed, detail = self._result(
            profile, builders, {"key": "cfg-7", "chairs": {}})
        assert (passed, "another row" in detail) == (True, True)

    def test_silence_passes_and_says_it_proved_nothing(self, profile):
        # The lobby speaks only when a seat changes: 27 s and 82 s of silence
        # after arriving were both measured.
        from contrai_scraper.cli import _lobby_event_result

        _, passed, detail = _lobby_event_result(None, profile, "cfg-42")
        assert (passed, "proves nothing yet" in detail) == (True, True)

    def test_an_event_naming_no_row_fails_on_the_hash_path(self, profile, builders):
        _, passed, detail = self._result(profile, builders, {"chairs": {}})
        assert (passed, "lobby_hash" in detail) == (False, True)

    def test_an_event_with_no_seat_map_fails_on_the_seats_path(self, profile, builders):
        _, passed, detail = self._result(profile, builders, {"key": "cfg-42"})
        assert (passed, "lobby_seats" in detail) == (False, True)


def _capturing_profile(profile_text, tmp_path, root):
    """The fixture profile with failure capture on and its roots in ``root``."""

    from contrai_scraper import load_profile

    text = re.sub(
        r'(?m)^raw_root = .*$', f'raw_root = "{root.as_posix()}"',
        re.sub(
            r'(?m)^root = .*$', f'root = "{root.as_posix()}"',
            profile_text.replace(
                "screenshot_on_error = false", "screenshot_on_error = true"
            ),
        ),
    )
    path = tmp_path / "capturing-profile.toml"
    path.write_text(text, encoding="utf-8")
    return load_profile(path)


class TestCheckProfileCapture:
    """What a failed live check leaves behind to be looked at."""

    def test_a_failed_check_saves_the_page_and_says_where(
        self, profile_text, tmp_path, tmp_root
    ):
        # The failed line names the key that stopped matching and never why.
        # On a host reached through a console the browser is gone by the time
        # the line is read, so the page has to be kept at the moment it broke.
        message = "[selectors].login_email matched no field to type into"
        walk = FakeWalk(fail={"log_in": message}, saved=[".png", ".html"])
        profile = _capturing_profile(profile_text, tmp_path, tmp_root)
        results = asyncio.run(_live_checks(walk, Frames(), profile))
        step, passed, detail = results[0]
        assert (step, passed) == ("login", False)
        assert message in detail and "page saved to" in detail
        assert detail.count(".png") == 1 and detail.count(".html") == 1
        # Beside a session's own evidence, not in some directory of its own.
        assert walk.captured[0].parent == tmp_root / "raw"

    def test_the_page_is_not_saved_when_the_profile_does_not_ask(self, profile):
        # The fixture profile leaves the switch off, which is the default a
        # deployment opts out of rather than into.
        message = "[selectors].login_start matched nothing that could be clicked"
        walk = FakeWalk(fail={"log_in": message}, saved=[".png"])
        results = asyncio.run(_live_checks(walk, Frames(), profile))
        assert (results, walk.captured) == ([("login", False, message)], [])

    def test_a_capture_that_writes_nothing_leaves_the_line_alone(
        self, profile_text, tmp_path, tmp_root
    ):
        # A screenshot of a page that has already gone can fail, and a
        # diagnosis must never replace the failure it was taken for.
        message = "[selectors].login_email matched no field to type into"
        walk = FakeWalk(fail={"log_in": message})
        profile = _capturing_profile(profile_text, tmp_path, tmp_root)
        results = asyncio.run(_live_checks(walk, Frames(), profile))
        assert results == [("login", False, message)]

    def test_an_unwritable_raw_root_does_not_replace_the_failure(
        self, profile_text, tmp_path
    ):
        # Worth knowing about, but not here: the line it would decorate is
        # already reporting a failure of its own.
        blocked = tmp_path / "a-file-not-a-directory"
        blocked.write_text("", encoding="utf-8")
        message = "[selectors].login_email matched no field to type into"
        walk = FakeWalk(fail={"log_in": message}, saved=[".png"])
        profile = _capturing_profile(profile_text, tmp_path, blocked)
        results = asyncio.run(_live_checks(walk, Frames(), profile))
        assert (results, walk.captured) == ([("login", False, message)], [])


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

    def test_a_log_holding_two_tables_becomes_two_records(
        self, tmp_path, profile_path, two_table_raw_log_path
    ):
        # A session hops, so its log carries every table it looked at.
        # Parsing the whole log as one game merged them: rounds whose plays
        # belonged to another table were dropped as undealable, and the
        # record took whichever game id came first.
        code = main(["parse", str(two_table_raw_log_path), "--profile",
                     str(profile_path), "--out", str(tmp_path)])
        written = sorted(p.stem for p in (tmp_path / "games").glob("*.jsonl"))
        assert (code, written) == (0, ["obs-g1", "obs-g2"])

    def test_each_of_those_records_keeps_its_own_rounds(
        self, tmp_path, profile_path, two_table_raw_log_path
    ):
        main(["parse", str(two_table_raw_log_path), "--profile",
              str(profile_path), "--out", str(tmp_path)])
        for stem in ("obs-g1", "obs-g2"):
            record = load_game((tmp_path / "games") / f"{stem}.jsonl")
            assert record.complete is True

    def test_a_visit_that_held_no_game_writes_no_record(
        self, tmp_path, profile_path, source_game, synthesize, builders, capsys
    ):
        # Most visits are tables a gate refused seconds after arriving: they
        # describe themselves and nothing else happens. A record is a game,
        # so such a visit is counted and dropped, and the games after it are
        # written as usual.
        from contrai_scraper import RawFrame, RawLogWriter, raw_path

        looked_at = builders.envelope(
            "payload", "joinTable", builders.snapshot_payload(table_id="t0"),
            frame_id="pre0",
        )
        path = raw_path(tmp_path / "corpus", "session-4")
        with RawLogWriter(path) as log:
            frames = [(looked_at, 0), *synthesize(source_game)]
            for index, (text, socket) in enumerate(frames):
                log.write_frame(
                    RawFrame(socket=socket, direction="recv", at=index / 10,
                             text=text)
                )
        code = main(["parse", str(path), "--profile", str(profile_path),
                     "--out", str(tmp_path)])
        assert (code, len(list((tmp_path / "games").glob("*.jsonl")))) == (0, 1)
        assert "2 table visits, 1 with rounds" in capsys.readouterr().out

    def test_a_visit_this_profile_cannot_read_does_not_cost_the_rest(
        self, tmp_path, profile_path, source_game, synthesize, builders, capsys
    ):
        # The site runs variants whose contracts this ruleset cannot name.
        # One such table in the middle of a sweep must not take the games
        # around it down with it.
        from contrai_scraper import RawFrame, RawLogWriter, raw_path

        unreadable = builders.envelope(
            "payload", "joinTable",
            builders.snapshot_payload(
                table_id="t9", rows=[builders.score_row(suit="everything")]
            ),
            frame_id="odd0",
        )
        path = raw_path(tmp_path / "corpus", "session-3")
        with RawLogWriter(path) as log:
            frames = [*synthesize(source_game), (unreadable, 0)]
            for index, (text, socket) in enumerate(frames):
                log.write_frame(
                    RawFrame(socket=socket, direction="recv", at=index / 10,
                             text=text)
                )
        code = main(["parse", str(path), "--profile", str(profile_path),
                     "--out", str(tmp_path)])
        assert (code, len(list((tmp_path / "games").glob("*.jsonl")))) == (0, 1)
        assert "could not be read" in capsys.readouterr().out

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
