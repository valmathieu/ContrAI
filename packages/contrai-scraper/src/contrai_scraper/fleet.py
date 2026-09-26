"""A fleet: workers that wait in the lobby together and split the games.

``contrai-scrape run`` sits wherever the server seats it, which is always a
game already under way. A fleet catches games from their first card instead:
every worker idles in the lobby, where each tournament game's four players are
announced on the socket the moment it starts; one worker claims the roster and
chases it to its table, and the rest go on waiting. Idle workers belong in the
lobby rather than at a table because a spectator left at a table after its game
is sent back to the menu anyway, and because a worker already in the lobby
spends none of the few seconds a game's first deal allows on getting there.

The shape is the shift's, multiplied. The schedule says when, the egress says
whether — one tunnel and one gate for everyone — and inside an open window one
Chromium carries a session per worker. What changes is failure. A shift's
budgets end the process, which would take every worker down for one account's
bad luck; here each worker spends a budget of its own and goes down alone, and
the process hands itself back only once a majority of workers are down.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from .accounts import LabelledAccount
from .exceptions import BrowserError, ParseError, ProfileError, ShiftError
from .health import HealthLog
from .lobby import LobbyRoster, LobbyWatcher
from .parse.snapshot import Snapshot, read_snapshot
from .parse.translate import Translator
from .profile import FleetSection, Profile
from .rawlog import RawLogWriter, new_session_id, prune_raw_logs, raw_path
from .recorder import ChaseTarget, Recorder, RecorderLimits, SessionSummary, StopReason
from .registry import TableRegistry, estimate_population
from .shift import EGRESS_BUDGET, FAILURE_BUDGET, _earliest, _stamp, _utc_now
from .wire import WireStream

#: Seconds in a minute: the schedule counts its poll and overrun in minutes.
_MINUTE_S: Final[float] = 60.0

#: How often a worker waiting in the lobby looks up from the socket, to notice
#: that the fleet has stopped while the lobby said nothing.
HALL_POLL_S: Final[float] = 5.0

#: The label the one worker of a fleet run without an accounts file goes by.
SOLE_WORKER: Final[str] = "bot01"


@dataclass(frozen=True, slots=True)
class FleetSummary:
    """What a whole ``fleet`` run did, across its windows and workers."""

    windows: int
    chases: int
    chases_given_up: int
    games_recorded: int
    tables_seated: int
    tables_rejected: int
    records: tuple[Path, ...]
    workers_down: tuple[str, ...]


class _ReturnFailed(BrowserError):
    """The walk back to the lobby did not land; a fresh session is the way back.

    Not a failure of the worker: nothing has measured that walk, and a
    rebuilt session is its documented fallback, so it spends no budget.
    """


class Fleet:
    """Schedule gate, egress gate, then one browser and N workers per window."""

    __slots__ = (
        "_profile", "_health", "_open_browser", "_open_session", "_egress",
        "_registry", "_headless", "_limits", "_clock", "_monotonic", "_sleep",
        "_recorder", "_workers", "_windows", "_chases", "_gave_up", "_games",
        "_seated", "_rejected", "_records", "_down", "_run_deadline",
        "_seat_deadline", "_hard_deadline", "_census_due", "_census_sightings",
    )

    def __init__(
        self,
        profile: Profile,
        accounts: tuple[LabelledAccount, ...],
        health: HealthLog,
        *,
        open_browser: Callable[..., AbstractAsyncContextManager[Any]],
        open_session: Callable[..., AbstractAsyncContextManager[tuple[Any, Any]]],
        egress: Any,
        registry: TableRegistry,
        headless: bool | None = None,
        limits: RecorderLimits = RecorderLimits(),
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        recorder: Callable[..., Any] = Recorder,
    ) -> None:
        """Wire a fleet up.

        Every collaborator is injected, so the whole supervisor runs in tests
        with no browser, no network and no real time.

        Args:
            profile: The loaded profile, which must describe the lobby and
                carry a ``[fleet]`` section.
            accounts: One per worker, in the order they log in.
            health: The fleet's log; each worker writes through a labelled
                log of its own made from it.
            open_browser: ``open_browser``, or anything shaped like it.
            open_session: ``open_session``, or anything shaped like it:
                called with the browser, a worker's profile and a
                ``health`` keyword, it yields a spectator and frames.
            egress: The fleet's one gate — a
                :class:`~contrai_scraper.egress.SharedEgressGate` outside
                tests, since every worker and every hop asks it.
            registry: The fleet's claims and census.
            headless: Override for ``[browser].headless``; ``None`` takes it.
            limits: The run's own limits. ``max_games`` counts across the
                fleet and stops new chases once reached; ``max_seconds``
                runs on the monotonic clock.
            clock: The wall clock the schedule is read on.
            monotonic: The clock every deadline is measured on.
            sleep: How waits happen.
            recorder: Builds a chase's recorder; :class:`Recorder` itself
                outside tests.

        Raises:
            ProfileError: If the profile cannot run a fleet.
        """

        gaps = profile.fleet_gaps()
        if gaps:
            raise ProfileError(f"a fleet needs {', '.join(gaps)}")
        self._profile = profile
        self._health = health
        self._open_browser = open_browser
        self._open_session = open_session
        self._egress = egress
        self._registry = registry
        self._headless = headless
        self._limits = limits
        self._clock = clock if clock is not None else _utc_now
        self._monotonic = monotonic
        self._sleep = sleep
        self._recorder = recorder
        self._windows = self._chases = self._gave_up = 0
        self._games = self._seated = self._rejected = 0
        self._records: list[Path] = []
        self._down: list[str] = []
        self._run_deadline: float | None = None
        self._seat_deadline: float | None = None
        self._hard_deadline: float | None = None
        self._census_due = 0
        self._census_sightings: list[tuple[str, bool | None]] = []
        # Made once and kept across windows: a worker's log, its claims and
        # its failure streak belong to the account, not to one browser.
        self._workers = [Worker(self, account) for account in accounts]

    async def run(self) -> FleetSummary:
        """Run workers inside the schedule until the run's limits are met.

        Returns:
            What every window and every worker did, added up.

        Raises:
            ShiftError: The egress was refused too often before a window
                could open, or a majority of workers went down.
        """

        if self._limits.max_seconds is not None:
            self._run_deadline = self._monotonic() + self._limits.max_seconds
        schedule = self._profile.schedule
        idle = False
        blocked = 0
        while not self._over():
            now = self._clock()
            if not schedule.is_active(now):
                if not idle:
                    self._health.event(
                        "schedule_idle", next_opening=_stamp(schedule.next_change(now))
                    )
                    idle = True
                await self.idle()
                continue
            if idle:
                self._health.event("schedule_resume")
                idle = False

            reading = await self._egress.check()
            if not reading.ok:
                blocked += 1
                self._health.event("egress_blocked", attempt=blocked, **reading.fields())
                if blocked >= EGRESS_BUDGET:
                    raise ShiftError(f"egress refused {blocked} times in a row")
                await self.idle()
                continue
            blocked = 0
            self._health.event("egress_ok", **reading.fields())
            output = self._profile.output
            removed = prune_raw_logs(output.raw_root, output.raw_retention_days, now=now)
            if removed:
                self._health.event("raw_logs_pruned", count=len(removed))
            await self._window(now)

        return FleetSummary(
            windows=self._windows,
            chases=self._chases,
            chases_given_up=self._gave_up,
            games_recorded=self._games,
            tables_seated=self._seated,
            tables_rejected=self._rejected,
            records=tuple(self._records),
            workers_down=tuple(self._down),
        )

    async def _window(self, now: datetime) -> None:
        """One browser for the open window, and every live worker on it."""

        schedule = self._profile.schedule
        close = schedule.next_change(now)
        seat_in = None if close is None else (close - now).total_seconds()
        hard_in = seat_in
        if hard_in is not None and schedule.finish_current_game:
            hard_in += schedule.max_overrun_minutes * _MINUTE_S
        base = self._monotonic()
        self._seat_deadline = _earliest(
            None if seat_in is None else base + seat_in, self._run_deadline
        )
        self._hard_deadline = _earliest(
            None if hard_in is None else base + hard_in, self._run_deadline
        )

        live = [worker for worker in self._workers if worker.label not in self._down]
        if self.section.census_enabled:
            self._census_due = sum(1 for worker in live if not worker.censused)
        self._windows += 1
        self._health.event("fleet_window_opened", workers=len(live))
        async with self._open_browser(self._profile, headless=self._headless) as browser:
            done = asyncio.Event()
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._beat(done))
                    tasks = [
                        group.create_task(
                            worker.run(browser, delay=index * self.section.login_stagger_s)
                        )
                        for index, worker in enumerate(live)
                    ]
                    await asyncio.wait(tasks)
                    done.set()
            except* ShiftError as raised:
                raise raised.exceptions[0] from None
        self._health.event("fleet_window_closed")

    async def _beat(self, done: asyncio.Event) -> None:
        """Write the fleet's heartbeat on the recorder's cadence until done."""

        interval = self._profile.recorder.health_interval_s
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), interval)
            except TimeoutError:
                self._health.fleet_heartbeat(
                    down=len(self._down),
                    egress_probes=getattr(self._egress, "probes", None),
                )

    def _over(self) -> bool:
        """Whether the run's own limits are met: games across the fleet, or time."""

        games = self._limits.max_games
        if games is not None and self._games >= games:
            return True
        return (
            self._run_deadline is not None
            and self._monotonic() >= self._run_deadline
        )

    # -- what workers ask of the fleet -----------------------------------------

    @property
    def profile(self) -> Profile:
        """The site's profile, which each worker copies with its own account."""

        return self._profile

    @property
    def section(self) -> FleetSection:
        """The profile's ``[fleet]``, which a fleet cannot be built without."""

        assert self._profile.fleet is not None
        return self._profile.fleet

    @property
    def health(self) -> HealthLog:
        """The fleet's log, which each worker's is made from."""

        return self._health

    @property
    def egress(self) -> Any:
        """The one gate every worker and every hop asks."""

        return self._egress

    @property
    def registry(self) -> TableRegistry:
        """The fleet's claims and census."""

        return self._registry

    def session(
        self, browser: Any, profile: Profile, health: HealthLog
    ) -> AbstractAsyncContextManager[tuple[Any, Any]]:
        """One isolated session for a worker, on the window's browser."""

        return self._open_session(browser, profile, health=health)

    def recorder(self, *args: Any, **kwargs: Any) -> Any:
        """A chase's recorder, built the way the fleet was told to build one."""

        return self._recorder(*args, **kwargs)

    def stopping(self) -> bool:
        """Whether workers should take no new chase and leave the lobby."""

        if self._over():
            return True
        return (
            self._seat_deadline is not None
            and self._monotonic() >= self._seat_deadline
        )

    def seconds_left(self) -> tuple[float | None, float | None]:
        """A chase's two limits: when to stop seating, and when to stop at all."""

        now = self._monotonic()
        return (
            None if self._seat_deadline is None else self._seat_deadline - now,
            None if self._hard_deadline is None else self._hard_deadline - now,
        )

    def absorb(self, summary: SessionSummary) -> None:
        """Add one chase's work to the fleet's totals."""

        self._chases += 1
        if summary.stop_reason is StopReason.CHASE_GAVE_UP:
            self._gave_up += 1
        self._games += summary.games_recorded
        self._seated += summary.tables_seated
        self._rejected += summary.tables_rejected
        self._records.extend(summary.records)

    def census_report(self, sightings: list[tuple[str, bool | None]]) -> None:
        """Take one worker's startup sweep, and say what the sweeps have seen so far.

        The line is the fleet's first measurement of the population it is
        sized against: how many different tournament tables the sweeps met,
        how many times in all, and what population that resighting rate
        suggests — which is what says whether a ceiling of ten is right. It
        is written after every sweep with the number still out, rather than
        once at the end, so a worker that goes down before its sweep costs the
        report one worker's sightings and not the report.

        Args:
            sightings: ``(table id, is tournament)`` per table the worker's
                sweep read, repeats included.
        """

        self._census_sightings.extend(sightings)
        self._census_due -= 1
        tournament = [table for table, cup in self._census_sightings if cup]
        distinct = len(set(tournament))
        self._health.event(
            "census",
            pending=max(0, self._census_due),
            tables=len({table for table, _ in self._census_sightings}),
            tournament_tables=distinct,
            sightings=len(tournament),
            estimate=estimate_population(len(tournament), distinct),
        )

    def now(self) -> float:
        """The fleet's monotonic clock."""

        return self._monotonic()

    def worker_down(self, label: str) -> None:
        """Count a worker out, and hand the process back if most of them are.

        Raises:
            ShiftError: Once more than half the fleet is down — the point at
                which a fresh process is worth more than the workers left.
        """

        self._down.append(label)
        self._health.event("worker_down", worker=label, down=len(self._down))
        if len(self._down) * 2 > len(self._workers):
            raise ShiftError(f"{len(self._down)} of {len(self._workers)} workers are down")

    async def pause(self, seconds: float) -> None:
        """Wait on the fleet's own clock."""

        await self._sleep(seconds)

    async def idle(self) -> None:
        """Sleep one schedule poll, never past the run's own deadline."""

        wait = self._profile.schedule.idle_poll_minutes * _MINUTE_S
        if self._run_deadline is not None:
            wait = max(0.0, min(wait, self._run_deadline - self._monotonic()))
        await self._sleep(wait)


class Worker:
    """One account: log in, wait in the lobby, chase, record, and back again."""

    __slots__ = ("_fleet", "_label", "_profile", "_browser", "_health", "_claims",
                 "_failures", "_censused")

    def __init__(self, fleet: Fleet, account: LabelledAccount) -> None:
        """Bind a worker to its fleet and its account.

        Args:
            fleet: The supervisor.
            account: The account this worker logs in with. The worker's
                profile is the site's with this account swapped in.
        """

        self._fleet = fleet
        self._label = account.label
        self._profile = dataclasses.replace(fleet.profile, account=account.account)
        self._browser: Any = None
        self._health = fleet.health.worker(account.label)
        self._claims = fleet.registry.for_worker(account.label)
        self._failures = 0
        self._censused = False

    @property
    def label(self) -> str:
        """The account's opaque name."""

        return self._label

    @property
    def censused(self) -> bool:
        """Whether this worker's startup sweep has been made — once a process."""

        return self._censused

    async def run(self, browser: Any, *, delay: float = 0.0) -> None:
        """Sessions, one after another, until the fleet stops or this worker is down.

        Args:
            browser: The window's shared browser, which sessions open on.
            delay: Seconds to wait before the first login, so a fleet's
                logins arrive one at a time rather than all at once.

        Raises:
            ShiftError: If this worker going down leaves most of the fleet down.
        """

        fleet = self._fleet
        self._browser = browser
        if delay:
            await fleet.pause(delay)
        blocked = 0
        while not fleet.stopping():
            reading = await fleet.egress.check()
            if not reading.ok:
                blocked += 1
                self._health.event("egress_blocked", attempt=blocked, **reading.fields())
                if blocked >= EGRESS_BUDGET:
                    fleet.worker_down(self._label)
                    return
                await fleet.idle()
                continue
            blocked = 0
            try:
                await self._session()
            except _ReturnFailed as error:
                self._health.event("return_rebuilt", error=str(error))
            except Exception as error:  # noqa: BLE001 - one session must not end the worker
                self._failures += 1
                self._health.event(
                    "session_failed", attempt=self._failures,
                    error=f"{type(error).__name__}: {error}",
                )
                if self._failures >= FAILURE_BUDGET:
                    fleet.worker_down(self._label)
                    return
                await fleet.idle()

    async def _session(self) -> None:
        """One browser context: log in, reach the lobby, and serve it.

        Raises:
            BrowserError: A step of the walk failed; the page is saved first.
            _ReturnFailed: The walk back from a table did not land.
        """

        session = f"{new_session_id()}-{self._label}"
        log = RawLogWriter(raw_path(self._profile.output.raw_root, session))
        self._health.event("session_started", session=session, raw=str(log.path))
        try:
            async with self._fleet.session(
                self._browser, self._profile, self._health
            ) as (spectator, frames):
                try:
                    await spectator.log_in()
                    if await self._arrive(spectator, frames, log):
                        table_hash = await spectator.read_tournament_hash()
                        if table_hash is None:
                            raise BrowserError(
                                "[selectors].lobby_row_tournament_class matched no row"
                            )
                        await self._hall(spectator, frames, log, table_hash)
                except BrowserError:
                    await self._capture(spectator, log.path)
                    raise
        finally:
            log.close()
        self._health.event("session_ended", session=session)

    async def _arrive(self, spectator: Any, frames: Any, log: RawLogWriter) -> bool:
        """Reach the lobby — by way of the startup census, the first time.

        Returns:
            Whether the worker is in the lobby. ``False`` means the census was
            cut short — by the fleet stopping or the egress refusing — and the
            session should end where it stands.

        Raises:
            _ReturnFailed: The walk back from the census's last table did not
                land.
        """

        fleet = self._fleet
        if not fleet.section.census_enabled or self._censused:
            await spectator.enter_lobby()
            return True
        # Marked before the sweep, not after: a sweep that fails is not one to
        # repeat at the price of every later session's login.
        self._censused = True
        sightings: list[tuple[str, bool | None]] = []
        try:
            finished = await self._census(spectator, frames, log, sightings)
        finally:
            fleet.census_report(sightings)
        if not finished:
            return False
        try:
            await spectator.return_to_lobby()
        except BrowserError as error:
            raise _ReturnFailed(str(error)) from error
        return True

    async def _census(
        self,
        spectator: Any,
        frames: Any,
        log: RawLogWriter,
        sightings: list[tuple[str, bool | None]],
    ) -> bool:
        """Look at a few tables the ordinary way, recording nothing.

        Before its first lobby each worker walks the observe branch and hops
        ``census_hops`` times, reading every join snapshot into the registry's
        census. Workers sweep in parallel, so the fleet starts knowing which
        tables are running and how far along, and how often the same ones
        came round — the resightings the population estimate is built on.

        Args:
            spectator: The worker's browser half, logged in.
            frames: Its frame source.
            log: The session's raw log.
            sightings: Filled with ``(table id, is tournament)`` per table
                read, repeats included — kept by the caller even if the sweep
                raises, so the fleet's report still counts what was seen.

        Returns:
            Whether the sweep ran to its end.
        """

        fleet = self._fleet
        self._health.event("census_started", hops=fleet.section.census_hops)
        await spectator.enter_variant()
        stream = WireStream(self._profile.wire)
        iterator = aiter(frames)
        left: str | None = None
        for hop in range(fleet.section.census_hops):
            if hop:
                if fleet.stopping():
                    self._health.event("census_stopped", reason="fleet_stopping")
                    return False
                if not (await fleet.egress.check()).ok:
                    # Nothing more to the site; the worker's own loop asks the
                    # egress again, and counts the refusals, before anything else.
                    self._health.event("census_stopped", reason="egress_blocked")
                    return False
                await spectator.next_table()
            snapshot = await self._next_snapshot(iterator, stream, log, left)
            if snapshot is None:
                continue
            left = snapshot.table_id
            sightings.append((snapshot.table_id, snapshot.is_tournament))
            self._claims.seen(
                snapshot.table_id,
                is_tournament=snapshot.is_tournament,
                round_index=snapshot.round_index,
            )
            self._health.event(
                "census_seen", table=snapshot.table_id,
                tournament=snapshot.is_tournament, round=snapshot.round_index,
            )
        self._health.event(
            "census_done", seen=len(sightings),
            distinct=len({table for table, _ in sightings}),
        )
        return True

    async def _next_snapshot(
        self, iterator: Any, stream: WireStream, log: RawLogWriter, left: str | None
    ) -> Snapshot | None:
        """The next table that describes itself, within ``snapshot_timeout_s``.

        The table just left does not count: a hop moves the page long before
        the reader hears of it, so the snapshot waiting in the queue is
        routinely the last table's — counting it again would be a resighting
        that never happened, and resightings are what the estimate is made of.
        A table this profile cannot read is not counted either.

        Returns:
            The snapshot, or ``None`` when none came in time or the fleet
            stopped.

        Raises:
            BrowserError: The frames ended.
        """

        fleet = self._fleet
        translator = Translator(self._profile)
        join = self._profile.wire.events.join_snapshot
        started = fleet.now()
        timeout = self._profile.recorder.snapshot_timeout_s
        while not fleet.stopping() and fleet.now() - started < timeout:
            try:
                frame = await asyncio.wait_for(anext(iterator), HALL_POLL_S)
            except TimeoutError:
                continue
            except StopAsyncIteration:
                raise BrowserError("the frame source ended: the page has gone") from None
            log.write_frame(frame)
            event = stream.ingest(frame.text, frame.socket)
            if event is None or event.kind != join:
                continue
            try:
                snapshot = read_snapshot(event.data, translator, at=event.received_ms)
            except ParseError:
                continue
            if snapshot.table_id is None or snapshot.table_id == left:
                continue
            return snapshot
        return None

    async def _hall(
        self, spectator: Any, frames: Any, log: RawLogWriter, table_hash: str
    ) -> None:
        """Wait in the lobby, chase what starts, and come back — until told to stop.

        Raises:
            _ReturnFailed: The walk back from a table did not land.
            BrowserError: The frames ended, which only a closed page does.
        """

        fleet = self._fleet
        registry = fleet.registry
        # One reader for the whole session, not one per wait. The lobby sends
        # every event on both sockets, and a wait resumed after a roster was
        # refused would otherwise meet the second copy as news and announce
        # the same start twice.
        watcher = LobbyWatcher(self._profile, table_hash)
        stream = WireStream(self._profile.wire)
        while True:
            # The wait is where a stopping fleet is noticed, before and
            # between chases alike: it returns no roster once told to stop.
            roster = await self._await_roster(frames, log, watcher, stream)
            if roster is None:
                return
            age = frames.elapsed - roster.at
            if age > fleet.section.roster_max_age_s:
                # Read too late to be the game about to start; chasing it
                # would find a game already under way.
                self._health.event("roster_stale", roster=roster.digest, age_s=round(age, 1))
                continue
            if not registry.claim_roster(roster.accounts, self._label):
                self._health.event("roster_taken", roster=roster.digest)
                continue
            try:
                self._health.event("chase_started", roster=roster.digest)
                await spectator.enter_table_from_lobby()
                summary = await self._chase(spectator, frames, log, roster)
            finally:
                registry.release_roster(roster.accounts, self._label)
            fleet.absorb(summary)
            if summary.stop_reason is StopReason.SOURCE_ENDED:
                raise BrowserError("the frame source ended: the page has gone")
            # A chase that ran its course is progress, whatever it found.
            self._failures = 0
            if summary.stop_reason is StopReason.EGRESS_BLOCKED or fleet.stopping():
                # Nothing more goes to the site on this session: the worker's
                # own loop checks the egress before the next.
                return
            try:
                await spectator.return_to_lobby()
            except BrowserError as error:
                raise _ReturnFailed(str(error)) from error

    async def _await_roster(
        self, frames: Any, log: RawLogWriter, watcher: LobbyWatcher, stream: WireStream
    ) -> LobbyRoster | None:
        """Read the lobby's socket until a tournament game starts.

        Args:
            frames: The session's frames.
            log: The session's raw log.
            watcher: The session's reader of the tournament row.
            stream: The session's de-duplicating reader of the socket.

        Returns:
            The starting roster, or ``None`` once the fleet has stopped.

        Raises:
            BrowserError: The frames ended.
        """

        fleet = self._fleet
        iterator = aiter(frames)
        interval = self._profile.recorder.health_interval_s
        self._health.event("in_hall")
        while not fleet.stopping():
            if self._health.due(interval):
                self._health.heartbeat(state="hall", lobby_states=watcher.states)
            try:
                frame = await asyncio.wait_for(anext(iterator), HALL_POLL_S)
            except TimeoutError:
                continue
            except StopAsyncIteration:
                raise BrowserError("the frame source ended: the page has gone") from None
            log.write_frame(frame)
            event = stream.ingest(frame.text, frame.socket)
            if event is None:
                continue
            roster = watcher.read(event, at=frame.at)
            if roster is not None:
                self._health.event("roster_announced", roster=roster.digest)
                return roster
        return None

    async def _chase(
        self, spectator: Any, frames: Any, log: RawLogWriter, roster: LobbyRoster
    ) -> SessionSummary:
        """Run one chase's recorder on this worker's session."""

        fleet = self._fleet
        seat_in, hard_in = fleet.seconds_left()
        recorder = fleet.recorder(
            spectator, frames, self._profile, self._health,
            limits=RecorderLimits(max_seconds=hard_in, seat_until_s=seat_in),
            raw=log,
            egress=fleet.egress,
            claims=self._claims,
            target=ChaseTarget(
                roster=roster,
                distinct_budget=fleet.section.scan_distinct_budget,
                deadline_s=fleet.section.scan_deadline_s,
            ),
        )
        return await recorder.run()

    async def _capture(self, spectator: Any, raw: Path) -> None:
        """Save the page beside the session's raw log, if the profile asks."""

        if not self._profile.browser.screenshot_on_error:
            return
        saved = await spectator.capture(raw.with_suffix(""))
        self._health.event("failure_captured", files=[str(path) for path in saved])
