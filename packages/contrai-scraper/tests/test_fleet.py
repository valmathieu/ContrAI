"""Pins the fleet: workers in the lobby, one chase per roster, budgets per worker."""

import asyncio
import dataclasses
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from contrai_core import Position, Suit, TeamSide
from contrai_data import EndReason

from contrai_scraper import (
    FAILURE_BUDGET,
    AccountSection,
    BrowserError,
    EgressReading,
    EgressRefusal,
    Fleet,
    HealthLog,
    LabelledAccount,
    OptionsReading,
    ProfileError,
    RawFrame,
    RecorderLimits,
    ScoreboardReading,
    SessionSummary,
    ShiftError,
    StopReason,
    TableRegistry,
    parse_range,
)

OPEN = EgressReading(refusal=None, exit_ip="203.0.113.7", country="XX", route_device=None)
BLOCKED = EgressReading(refusal=EgressRefusal.PROBE_FAILED, exit_ip=None, country=None,
                        route_device=None)

#: The tournament row's hash, and the four players ``session_frames`` seats.
CUP = "cfg-42"
FOUR = ("1001", "1002", "1003", "1004")
OTHERS = ("3001", "3002", "3003", "3004")


@pytest.fixture(autouse=True)
def quick_hall(monkeypatch):
    """A worker in the lobby looks up every hundredth of a second, not five."""

    monkeypatch.setattr("contrai_scraper.fleet.HALL_POLL_S", 0.01)


def accounts(count):
    """Labelled accounts ``bot01`` onwards, each with an address of its own."""

    return tuple(
        LabelledAccount(
            label=f"bot{index:02}",
            account=AccountSection(f"bot{index:02}@example.invalid", "0000"),
        )
        for index in range(1, count + 1)
    )


def received(text, at=0.0):
    return RawFrame(socket=0, direction="recv", at=at, text=text)


def full_row(builders, seats=FOUR, *, frame_id="l1", at=0.0):
    """The lobby event a tournament game starts on: four seats and the flag."""

    data = {"key": CUP, "ready": True, "chairs": {
        placement: {"acct": account}
        for placement, account in zip(("top", "right", "bottom", "left"), seats,
                                      strict=True)
    }}
    return received(builders.envelope("payload", "slot", data, frame_id=frame_id), at)


class Frames:
    """Scripted frames; once spent, a live socket with nothing to say.

    ``on_empty`` runs each time the script is found empty, which is how a
    test ends a run once its scenario has played out. ``ends`` makes the
    source stop instead, the way it does when its page has gone.
    """

    def __init__(self, frames=(), *, on_empty=None, ends=False):
        self._frames = list(frames)
        self._taken = 0
        self._on_empty = on_empty
        self._ends = ends

    @property
    def pending(self):
        return 0

    @property
    def elapsed(self):
        return float(self._taken)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            self._taken += 1
            return self._frames.pop(0)
        if self._ends:
            raise StopAsyncIteration
        if self._on_empty is not None:
            self._on_empty()
        await asyncio.sleep(3600)


class Walker:
    """A spectator whose walk is scripted; a named step may raise."""

    def __init__(self, *, fail=None, table_hash=CUP):
        self.calls: list[str] = []
        self._fail = dict(fail or {})
        self._hash = table_hash

    def _step(self, name):
        self.calls.append(name)
        if name in self._fail:
            raise BrowserError(self._fail[name])

    async def log_in(self):
        self._step("log_in")

    async def enter_lobby(self):
        self._step("enter_lobby")
        return False

    async def enter_variant(self):
        self._step("enter_variant")
        return False

    async def read_tournament_hash(self):
        self.calls.append("read_tournament_hash")
        return self._hash

    async def enter_table_from_lobby(self):
        self._step("enter_table_from_lobby")

    async def return_to_lobby(self):
        self._step("return_to_lobby")

    async def capture(self, stem):
        self.calls.append("capture")
        return (stem.with_suffix(".png"),)

    # -- what a real recorder asks of it -------------------------------------

    async def read_options(self, expected):
        return OptionsReading(observed=dict(expected), missing=(), extra=(), differing=())

    async def read_scoreboard(self):
        return ScoreboardReading(rows=(), text="")

    async def request_state(self, table_id, last_event_id):
        return False

    async def next_table(self):
        self.calls.append("next_table")


def summary(reason=StopReason.CHASE_ENDED, games=1):
    return SessionSummary(games_recorded=games, tables_seated=games, tables_rejected=0,
                          records=(), stop_reason=reason)


class Recorders:
    """Builds recorders that answer from a script, taking their time about it.

    The pause is what lets a second worker read the same roster while the
    first is still chasing it, as it would live.
    """

    def __init__(self, *summaries):
        self._summaries = list(summaries)
        self.built: list[dict] = []

    def __call__(self, spectator, frames, profile, health, **kwargs):
        self.built.append({"account": profile.account.email, **kwargs})
        result = self._summaries.pop(0)

        class Scripted:
            async def run(self):
                await asyncio.sleep(0.05)
                return result

        return Scripted()


class Harness:
    """A fleet over fakes: a hand-moved clock, scripted sessions, one log."""

    def __init__(self, profile, workers=1, *, egress=(OPEN,), recorder=None,
                 limits=RecorderLimits(max_seconds=1e6), clock=None):
        self.profile = profile
        self.accounts = accounts(workers)
        self.now = 0.0
        self.lines: list[str] = []
        self.sessions: dict[str, list] = {}
        self.opened: list[str] = []
        self.browsers = 0
        self.sleeps: list[float] = []
        self._egress = list(egress)
        self.recorder = recorder if recorder is not None else Recorders()
        self.limits = limits
        self.clock = clock

    def script(self, label, *sessions):
        """The sessions one worker will open, in order: ``(walker, frames)``."""

        email = f"{label}@example.invalid"
        self.sessions.setdefault(email, []).extend(sessions)

    def end(self):
        """Put the clock past every deadline: the run is over."""

        self.now = 1e9

    def saw(self, name):
        return any(json.loads(line)["event"] == name for line in self.lines)

    def events(self, name):
        return [entry for entry in map(json.loads, self.lines) if entry["event"] == name]

    def fleet(self):
        harness = self

        class Egress:
            probes = 0

            async def check(self):
                reading = harness._egress[0] if len(harness._egress) == 1 \
                    else harness._egress.pop(0)
                return reading

        @asynccontextmanager
        async def open_browser(profile, *, headless=None):
            harness.browsers += 1
            yield object()

        @asynccontextmanager
        async def open_session(browser, profile, *, health):
            email = profile.account.email
            harness.opened.append(email)
            queue = harness.sessions.get(email, [])
            if not queue:
                raise BrowserError("no session scripted for this account")
            yield queue.pop(0)

        async def sleep(seconds):
            harness.sleeps.append(seconds)
            harness.now += seconds
            await asyncio.sleep(0)

        return Fleet(
            self.profile, self.accounts,
            HealthLog(write=self.lines.append, monotonic=lambda: harness.now),
            open_browser=open_browser, open_session=open_session, egress=Egress(),
            registry=TableRegistry(claim_ttl_s=600, monotonic=lambda: harness.now),
            limits=self.limits, clock=self.clock, monotonic=lambda: harness.now,
            sleep=sleep, recorder=self.recorder,
        )

    def run(self):
        return asyncio.run(self.fleet().run())


def _finished_game(game_builders):
    """One round played to the target, so the table closes its record itself."""

    return game_builders.game_events(
        game_builders.round_events(1, Position.SOUTH, Position.WEST, 80, Suit.SPADES,
                                   True, {TeamSide.NS: 0, TeamSide.EW: 170}),
        reason=EndReason.TARGET_REACHED,
    )


class TestTheChase:
    def test_one_worker_catches_a_game_from_the_lobby(
        self, profile, builders, session_frames, game_builders
    ):
        # The whole route with the real recorder: the lobby announces the
        # start, the worker claims it, walks to a table and records the game.
        from contrai_scraper import Recorder

        harness = Harness(profile, recorder=Recorder,
                          limits=RecorderLimits(max_games=1))
        walker = Walker()
        filling = received(builders.envelope("payload", "slot", {
            "key": CUP, "chairs": {"top": {"acct": "1001"}, "left": {"acct": "1004"}},
        }, frame_id="l0"))
        harness.script("bot01", (walker, Frames(
            [filling, full_row(builders),
             *session_frames(_finished_game(game_builders))])))
        result = harness.run()
        recorded = harness.events("game_recorded")[0]
        assert (result.games_recorded, result.chases, recorded["worker"],
                recorded["first_round"], walker.calls) == (
            1, 1, "bot01", 1,
            ["log_in", "enter_lobby", "read_tournament_hash", "enter_table_from_lobby"])
        assert result.records[0].exists()

    def test_two_workers_seeing_one_start_chase_it_once(self, profile, builders):
        harness = Harness(profile, workers=2, recorder=Recorders(summary()))
        for label in ("bot01", "bot02"):
            harness.script(label, (Walker(), Frames([full_row(builders)],
                                                     on_empty=harness.end)))
        result = harness.run()
        assert (result.chases, len(harness.events("roster_taken"))) == (1, 1)

    def test_the_chase_carries_the_fleets_budget_and_its_claims(self, profile, builders):
        recorders = Recorders(summary())
        harness = Harness(profile, recorder=recorders)
        harness.script("bot01", (Walker(), Frames([full_row(builders)],
                                                  on_empty=harness.end)))
        harness.run()
        built = recorders.built[0]
        assert (built["target"].distinct_budget, built["target"].deadline_s,
                built["target"].roster.accounts, built["claims"].worker) == (
            5, 60, frozenset(FOUR), "bot01")

    def test_a_roster_read_too_late_is_not_chased(self, profile, builders):
        # Two keepalives came first, so the roster is two ticks old when read;
        # a game that old is already under way.
        stale = dataclasses.replace(
            profile, fleet=dataclasses.replace(profile.fleet, roster_max_age_s=1))
        harness = Harness(stale)
        harness.script("bot01", (Walker(), Frames(
            [received("tick"), received("tick"), full_row(builders)],
            on_empty=harness.end)))
        result = harness.run()
        assert (result.chases, harness.events("roster_stale")[0]["age_s"]) == (0, 3.0)

    def test_a_chase_given_up_goes_back_to_the_lobby_for_the_next(
        self, profile, builders
    ):
        harness = Harness(profile, recorder=Recorders(
            summary(StopReason.CHASE_GAVE_UP, games=0), summary()))
        walker = Walker()
        harness.script("bot01", (walker, Frames(
            [full_row(builders), full_row(builders, OTHERS, frame_id="l2")],
            on_empty=harness.end)))
        result = harness.run()
        assert ((result.chases, result.chases_given_up, result.games_recorded),
                walker.calls.count("return_to_lobby")) == ((2, 1, 1), 2)


class TestSessions:
    def test_a_failed_walk_back_rebuilds_the_session_and_spends_no_budget(
        self, profile, builders
    ):
        harness = Harness(profile, recorder=Recorders(summary()))
        harness.script(
            "bot01",
            (Walker(fail={"return_to_lobby": "[selectors].mode_new_games was not in "
                                             "reach after 4 steps back"}),
             Frames([full_row(builders)])),
            (Walker(), Frames(on_empty=harness.end)),
        )
        harness.run()
        assert (len(harness.opened), harness.saw("return_rebuilt"),
                harness.saw("session_failed")) == (2, True, False)

    def test_a_lobby_with_no_tournament_row_fails_the_session_and_keeps_the_page(
        self, profile
    ):
        capturing = dataclasses.replace(
            profile, browser=dataclasses.replace(profile.browser, screenshot_on_error=True))
        harness = Harness(capturing)
        walker = Walker(table_hash=None)
        harness.script("bot01", (walker, Frames()), (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        failed = harness.events("session_failed")[0]
        assert ("lobby_row_tournament_class" in failed["error"], "capture" in walker.calls,
                harness.saw("failure_captured")) == (True, True, True)

    def test_a_page_that_goes_away_in_the_lobby_fails_the_session(self, profile):
        harness = Harness(profile)
        harness.script("bot01", (Walker(), Frames(ends=True)),
                       (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert "the page has gone" in harness.events("session_failed")[0]["error"]

    def test_a_chase_whose_frames_ended_fails_the_session(self, profile, builders):
        harness = Harness(profile, recorder=Recorders(
            summary(StopReason.SOURCE_ENDED, games=0)))
        harness.script("bot01", (Walker(), Frames([full_row(builders)])),
                       (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert harness.events("session_failed")[0]["attempt"] == 1

    def test_a_chase_stopped_by_the_egress_leaves_the_page_where_it_is(
        self, profile, builders
    ):
        # Nothing more goes to the site on a session whose tunnel just
        # failed; the worker's own loop checks the egress before the next.
        harness = Harness(profile, recorder=Recorders(
            summary(StopReason.EGRESS_BLOCKED, games=0)))
        walker = Walker()
        harness.script("bot01", (walker, Frames([full_row(builders)])),
                       (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert ("return_to_lobby" in walker.calls, len(harness.opened)) == (False, 2)

    def test_logins_arrive_one_at_a_time(self, profile):
        staggered = dataclasses.replace(
            profile, fleet=dataclasses.replace(profile.fleet, login_stagger_s=20))
        harness = Harness(staggered, workers=3)
        for label in ("bot01", "bot02", "bot03"):
            harness.script(label, (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert sorted(harness.sleeps[:2]) == [20, 40]


class TestBudgets:
    def test_a_worker_that_keeps_failing_goes_down_alone(self, profile):
        harness = Harness(profile, workers=3)

        def end_once_down():
            if harness.saw("worker_down"):
                harness.end()

        harness.script("bot01", *[(Walker(fail={"log_in": "refused"}), Frames())] * 3)
        for label in ("bot02", "bot03"):
            harness.script(label, (Walker(), Frames(on_empty=end_once_down)))
        result = harness.run()
        assert (result.workers_down, harness.opened.count("bot01@example.invalid")) == (
            ("bot01",), FAILURE_BUDGET)

    @pytest.mark.parametrize("workers", [1, 2])
    def test_a_majority_down_hands_the_process_back(self, profile, workers):
        harness = Harness(profile, workers=workers)
        with pytest.raises(ShiftError, match=f"{workers} of {workers} workers are down"):
            harness.run()

    def test_a_finished_chase_clears_a_workers_failures(self, profile, builders):
        # Two failures, then a chase that ran its course, then two more: never
        # three in a row, so the worker is still up.
        harness = Harness(profile, recorder=Recorders(summary()))
        broken = (Walker(fail={"log_in": "refused"}), Frames())
        harness.script(
            "bot01", broken, broken,
            (Walker(fail={"return_to_lobby": "out of reach"}), Frames([full_row(builders)])),
            broken, broken,
            (Walker(), Frames(on_empty=harness.end)),
        )
        result = harness.run()
        assert (result.workers_down, len(harness.events("session_failed"))) == ((), 4)

    def test_a_worker_behind_a_refused_egress_goes_down_after_its_budget(self, profile):
        # The fleet's own check passes; every one of the worker's is refused.
        harness = Harness(profile, egress=(OPEN, *[BLOCKED] * 6))
        with pytest.raises(ShiftError, match="1 of 1 workers are down"):
            harness.run()
        assert [entry["attempt"] for entry in harness.events("egress_blocked")] == [
            1, 2, 3, 4, 5, 6]

    def test_a_refused_egress_waits_and_lets_the_worker_through_after(
        self, profile, builders
    ):
        # Bounded by a game count alone, so the wait has no deadline to cut it.
        harness = Harness(profile, egress=(OPEN, BLOCKED, OPEN),
                          recorder=Recorders(summary()),
                          limits=RecorderLimits(max_games=1))
        harness.script("bot01", (Walker(), Frames([full_row(builders)])))
        result = harness.run()
        assert (len(harness.events("egress_blocked")), harness.sleeps,
                result.games_recorded) == (1, [300.0], 1)

    def test_a_blocked_tunnel_takes_the_fleet_down_worker_by_worker(self, profile):
        # Every worker is refused in turn; the first to spend its budget goes
        # down alone, and the second makes a majority.
        # (The third may spend its last attempt before the group is cancelled;
        # the process ends on the first majority either way.)
        harness = Harness(profile, workers=3, egress=(OPEN, BLOCKED))
        with pytest.raises(ShiftError, match="2 of 3 workers are down"):
            harness.run()
        assert [entry["worker"] for entry in harness.events("worker_down")][:2] == [
            "bot01", "bot02"]


class TestWindows:
    def test_a_refused_egress_opens_no_browser_and_ends_on_its_budget(self, profile):
        harness = Harness(profile, egress=(BLOCKED,))
        with pytest.raises(ShiftError, match="refused 6 times"):
            harness.run()
        assert harness.browsers == 0

    def test_a_closed_schedule_opens_no_browser(self, profile):
        night = dataclasses.replace(
            profile, schedule=dataclasses.replace(
                profile.schedule, active=(parse_range("01:00-02:00"),)))
        harness = Harness(night, clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
                          limits=RecorderLimits(max_seconds=900.0))
        result = harness.run()
        assert (harness.browsers, harness.saw("schedule_idle"), result.windows) == (
            0, True, 0)

    def test_a_schedule_that_reopens_says_so(self, profile):
        night = dataclasses.replace(
            profile, schedule=dataclasses.replace(
                profile.schedule, active=(parse_range("01:00-02:00"),)))
        instants = iter([datetime(2026, 9, 24, 22, 0, tzinfo=UTC),
                         datetime(2026, 9, 24, 23, 30, tzinfo=UTC)])
        harness = Harness(night, clock=lambda: next(instants))
        harness.script("bot01", (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert (harness.saw("schedule_resume"), harness.browsers) == (True, 1)

    def test_a_window_closing_ends_the_wait_in_the_lobby(self, profile):
        # Open 01:00-02:00 Paris, which is 23:00-00:00 UTC in September: the
        # window opens at 23:59 UTC with a minute to run, and the lobby says
        # nothing for longer than that.
        night = dataclasses.replace(
            profile, schedule=dataclasses.replace(
                profile.schedule, active=(parse_range("01:00-02:00"),)))
        instants = iter([datetime(2026, 9, 24, 23, 59, tzinfo=UTC)])
        harness = Harness(
            night, limits=RecorderLimits(max_seconds=100.0),
            clock=lambda: next(instants, datetime(2026, 9, 25, 0, 30, tzinfo=UTC)))

        def a_minute_passes():
            harness.now += 61.0

        harness.script("bot01", (Walker(), Frames(on_empty=a_minute_passes)))
        result = harness.run()
        assert (result.windows, harness.saw("fleet_window_closed"),
                harness.saw("schedule_idle")) == (1, True, True)

    def test_old_raw_logs_are_pruned_before_a_window(self, profile, tmp_path):
        from contrai_scraper import raw_path

        old = raw_path(profile.output.raw_root, "20200101T000000Z-aaaaaa")
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("", encoding="utf-8")
        harness = Harness(profile, clock=lambda: datetime(2099, 1, 1, tzinfo=UTC))
        harness.script("bot01", (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert (harness.saw("raw_logs_pruned"), old.exists()) == (True, False)

    def test_a_down_worker_sits_out_the_next_window(self, profile):
        # A worker that spent its budget stays down for the process: the
        # operator is told once, rather than the account failing every night.
        harness = Harness(profile, workers=3)
        for label in ("bot02", "bot03"):
            harness.script(label, (Walker(), Frames(on_empty=harness.end)))
        fleet = harness.fleet()
        fleet.worker_down("bot01")
        asyncio.run(fleet.run())
        assert (harness.events("fleet_window_opened")[0]["workers"],
                "bot01@example.invalid" in harness.opened) == (2, False)


class TestHeartbeat:
    def test_the_fleet_beats_with_its_workers_added_up(self, profile):
        beating = dataclasses.replace(
            profile, recorder=dataclasses.replace(profile.recorder, health_interval_s=0))
        harness = Harness(beating, workers=2)
        for label in ("bot01", "bot02"):
            harness.script(label, (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        beat = harness.events("fleet_heartbeat")[0]
        assert (beat["workers"], beat["down"], beat["egress_probes"]) == (2, 0, 0)

    def test_a_worker_in_the_lobby_beats_too(self, profile):
        beating = dataclasses.replace(
            profile, recorder=dataclasses.replace(profile.recorder, health_interval_s=0))
        harness = Harness(beating)
        harness.script("bot01", (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        hall = [beat for beat in harness.events("heartbeat") if beat.get("state") == "hall"]
        assert hall[0]["worker"] == "bot01"


def table(builders, table_id, *, frame_id, cup=True, suit="wood"):
    """A join snapshot a census hop lands on."""

    payload = builders.snapshot_payload(table_id=table_id,
                                        rows=[builders.score_row(suit=suit)])
    payload["table"]["cup"] = cup
    return received(builders.envelope("payload", "joinTable", payload, frame_id=frame_id))


def censusing(profile, hops=3):
    """The fixture profile with the startup census switched on."""

    return dataclasses.replace(profile, fleet=dataclasses.replace(
        profile.fleet, census_enabled=True, census_hops=hops))


class TestCensus:
    def test_each_worker_sweeps_before_its_first_lobby(self, profile, builders):
        harness = Harness(censusing(profile))
        walker = Walker()
        harness.script("bot01", (walker, Frames(
            [table(builders, "tA", frame_id="a"), table(builders, "tB", frame_id="b"),
             table(builders, "tA", frame_id="a2")],
            on_empty=harness.end)))
        harness.run()
        census = harness.events("census")[-1]
        assert ((census["tournament_tables"], census["sightings"], census["pending"]),
                walker.calls) == (
            (2, 3, 0),
            ["log_in", "enter_variant", "next_table", "next_table", "return_to_lobby",
             "read_tournament_hash"])
        assert census["estimate"] is not None

    def test_the_table_just_left_is_not_counted_twice(self, profile, builders):
        # The snapshot waiting after a hop is routinely the last table's; a
        # resighting counted there never happened.
        harness = Harness(censusing(profile, hops=2))
        harness.script("bot01", (Walker(), Frames(
            [table(builders, "tA", frame_id="a"), table(builders, "tA", frame_id="a2"),
             table(builders, "tB", frame_id="b")],
            on_empty=harness.end)))
        harness.run()
        assert [entry["table"] for entry in harness.events("census_seen")] == ["tA", "tB"]

    def test_a_table_this_profile_cannot_read_is_not_counted(self, profile, builders):
        harness = Harness(censusing(profile, hops=1))
        harness.script("bot01", (Walker(), Frames(
            [received("tick"), table(builders, "tX", frame_id="x", suit="everything"),
             table(builders, "tA", frame_id="a")],
            on_empty=harness.end)))
        harness.run()
        assert [entry["table"] for entry in harness.events("census_seen")] == ["tA"]

    def test_plain_tables_are_seen_but_not_counted_as_the_population(
        self, profile, builders
    ):
        harness = Harness(censusing(profile, hops=2))
        harness.script("bot01", (Walker(), Frames(
            [table(builders, "tA", frame_id="a"),
             table(builders, "tP", frame_id="p", cup=False)],
            on_empty=harness.end)))
        harness.run()
        census = harness.events("census")[-1]
        assert (census["tables"], census["tournament_tables"]) == (2, 1)

    def test_the_census_happens_once_a_process(self, profile, builders):
        # The walk back after the sweep fails: the rebuilt session goes
        # straight to the lobby rather than sweeping again.
        harness = Harness(censusing(profile, hops=1))
        second = Walker()
        harness.script(
            "bot01",
            (Walker(fail={"return_to_lobby": "out of reach"}),
             Frames([table(builders, "tA", frame_id="a")])),
            (second, Frames(on_empty=harness.end)),
        )
        harness.run()
        assert (len(harness.events("census")), second.calls[:2]) == (
            1, ["log_in", "enter_lobby"])

    def test_a_census_that_hears_nothing_moves_on(self, profile):
        harness = Harness(censusing(profile, hops=2))

        def time_passes():
            harness.now += 31.0
            if harness.saw("census_done"):
                harness.end()

        harness.script("bot01", (Walker(), Frames(on_empty=time_passes)))
        harness.run()
        assert harness.events("census_done")[0]["seen"] == 0

    def test_a_refused_egress_cuts_the_census_short(self, profile, builders):
        # The fleet's check and the worker's pass; the census's next hop is
        # refused, so the page stays put and the worker asks again.
        harness = Harness(censusing(profile), egress=(OPEN, OPEN, BLOCKED, OPEN))
        harness.script(
            "bot01",
            (Walker(), Frames([table(builders, "tA", frame_id="a")])),
            (Walker(), Frames(on_empty=harness.end)),
        )
        harness.run()
        assert (harness.events("census_stopped")[0]["reason"],
                harness.events("census")[-1]["tournament_tables"]) == ("egress_blocked", 1)

    def test_a_stopping_fleet_cuts_the_census_short(self, profile, builders):
        harness = Harness(censusing(profile))
        harness.script("bot01", (Walker(), Frames(
            [table(builders, "tA", frame_id="a")], on_empty=harness.end)))
        harness.run()
        assert harness.events("census_stopped")[0]["reason"] == "fleet_stopping"

    def test_a_census_whose_page_goes_away_still_reports(self, profile, builders):
        harness = Harness(censusing(profile))
        harness.script("bot01",
                       (Walker(), Frames([table(builders, "tA", frame_id="a")], ends=True)),
                       (Walker(), Frames(on_empty=harness.end)))
        harness.run()
        assert (harness.events("census")[0]["tournament_tables"],
                harness.saw("session_failed")) == (1, True)


class TestConstruction:
    def test_a_profile_that_cannot_run_a_fleet_is_refused(self, profile):
        bare = dataclasses.replace(profile, fleet=None)
        with pytest.raises(ProfileError, match=r"a \[fleet\] section"):
            Harness(bare).fleet()
