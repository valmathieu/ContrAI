"""A fleet: workers that wait in the lobby together and split the games.

``contrai-scrape run`` sits wherever the server seats it, which is always a
game already under way. A fleet catches games from their first card instead:
a few workers idle in the lobby, where each tournament game's four players are
announced on the socket the moment it starts; one worker claims the roster and
chases it to its table, and the rest go on waiting. Watchers belong in the
lobby rather than at a table because a spectator left at a table after its game
is sent back to the menu anyway, and because a worker already in the lobby
spends none of the few seconds a game's first deal allows on getting there.

Only a few, though: the :class:`~contrai_scraper.rota.Rota` keeps at most
``[fleet].lobby_watchers`` in the lobby and ``[fleet].spares`` logged in off
it, and every other idle worker logs out until a chase needs it. And a window
opens on games already under way, which no lobby will announce: once the first
watcher is placed, the next logins each join one of them and record it from
where they join, until one finds none to take.

The shape is the shift's, multiplied. The schedule says when, the egress says
whether — one tunnel and one gate for everyone — and inside an open window one
Chromium carries a session per worker. What changes is failure. A shift's
budgets end the process, which would take every worker down for one account's
bad luck; here each worker spends a budget of its own and goes down alone, and
the process hands itself back only once a majority of workers are down. A down
worker is not lost for good: after a backoff it is given a probation login, and
only an account that keeps failing those is retired.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from .accounts import LabelledAccount
from .exceptions import BrowserError, ParseError, ProfileError, ShiftError
from .frames import RECEIVED
from .health import HealthLog
from .lobby import LobbyRoster, LobbyWatcher
from .parse.snapshot import Snapshot, read_snapshot
from .parse.translate import Translator
from .profile import FleetSection, Profile
from .rawlog import RawLogWriter, new_session_id, prune_raw_logs, raw_path
from .recorder import ChaseTarget, Recorder, RecorderLimits, SessionSummary, StopReason
from .registry import TableRegistry, WorkerClaims, estimate_population
from .rota import Role, Rota
from .shift import EGRESS_BUDGET, FAILURE_BUDGET, _earliest, _stamp, _utc_now
from .wire import WireStream

#: Seconds in a minute: the schedule counts its poll and overrun in minutes.
_MINUTE_S: Final[float] = 60.0

#: How often a worker waiting in the lobby looks up from the socket, to notice
#: that the fleet has stopped while the lobby said nothing.
HALL_POLL_S: Final[float] = 5.0

#: How often a spare, standing by off the lobby, drains its page's frames into
#: the raw log and looks at whether it has been sent in.
STANDBY_POLL_S: Final[float] = 1.0

#: How long a worker in the lobby may hear nothing at all before it rebuilds.
#:
#: The lobby can stay quiet for over a minute, but the page never does: its
#: keepalive answers every 20 s, and across the ramp's healthy sessions no
#: received frame came more than 20.9 s after the last. Three times that also
#: rides out the 5-8 s reconnections measured in the 5-worker run.
HALL_SILENCE_S: Final[float] = 60.0

#: How long a worker in the lobby may hear keepalives and nothing else before
#: it rebuilds.
#:
#: The page holds two sockets, and either may carry the lobby. When the one
#: carrying it closes, the other can go on answering pings without ever
#: delivering an event, so ``HALL_SILENCE_S`` never trips: live, such lobbies
#: sat deaf for 4 to 15 hours. The lobby itself goes quiet for minutes when
#: few tables are filling: across 140 h of healthy lobby waits on the box, the
#: longest stretch with no site event at all was 12.7 min (p99 6.3 min), every
#: one of them in the hour after the 07:00 UTC opening.
HALL_QUIET_S: Final[float] = 900.0

#: How long a worker waits after the first failed session of a streak before
#: asking for a place again; later failures in a row wait a schedule poll.
#:
#: The poll is 5 minutes, and a live run lost its only lobby watcher to one
#: failed session for all of that: nobody else was free to take the place.
FIRST_RETRY_S: Final[float] = 30.0

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
    bootstraps: int = 0
    """Startup workers sent to join a game already under way."""

    bootstrap_games: int = 0
    """Games those workers recorded, from where they joined."""

    revivals: int = 0
    """Probation logins given to down workers."""

    hunts: int = 0
    """Starts hunted by seekers in parallel; each also counts as one chase."""

    hunts_won: int = 0
    """Hunts in which one searcher took the table."""

    hunts_lost: int = 0
    """Hunts in which every searcher gave up; each also a chase given up."""

    hunt_runs: int = 0
    """Searcher recorders run across every hunt, winners and losers."""


@dataclass(slots=True)
class _Hunt:
    """One start a watcher read, hunted by several seekers at once."""

    roster: LobbyRoster
    watcher: str
    """Who read it, and holds the roster claim until the hunt closes."""

    searchers: set[str]
    """The seekers still hunting, or watching the game the winner found."""

    runs: int
    """How many seekers were sent."""

    age_s: float
    """How old the roster was when the hunt opened."""

    opened_at: float
    """When the hunt opened, on the fleet's clock."""

    won_by: str | None = None
    """The searcher whose claim on the table was granted first."""

    def roster_for(self, elapsed: float, now: float) -> LobbyRoster:
        """The roster, with its time moved onto one searcher's own page.

        A roster's ``at`` is on the frame clock of the page that read it, and
        every page's clock starts at its own login. A searcher's chase reads
        its match time off its own page, so the roster it carries must say
        when the start was announced in that page's time.

        Args:
            elapsed: The searcher's frame clock now.
            now: The fleet's clock now.
        """

        return dataclasses.replace(
            self.roster, at=elapsed - (self.age_s + now - self.opened_at)
        )


@dataclass(slots=True)
class _Down:
    """One down worker: when it may try again, and how often it already has."""

    attempts: int = 0
    """Revivals that failed since the worker first went down."""

    next_try: float | None = None
    """When the next revival is due, on the fleet's clock; ``None`` when none
    is: revival is off, one is under way, or the worker is retired."""

    reviving: bool = False
    """Whether the worker is on probation now, logged in or logging in."""

    retired: bool = False
    """Whether it has spent ``revive_attempts`` and stays down for the process."""


async def hold_forever() -> None:
    """Wait until cancelled: what a drained fleet does in place of exiting."""

    await asyncio.Event().wait()


class _Place(StrEnum):
    """Where a session's page stands, which decides its walk to the lobby."""

    MENU = "menu"
    """Where a login leaves it: the walk in is ``enter_lobby``, or the census."""

    TABLE = "table"
    """At a table after a chase, or just stepped off one onto the online menu:
    the walk back is ``return_to_lobby``, which skips the exit when a menu
    already shows."""


class _SeatedClaims:
    """A worker's claims that also say, once, when a table was taken.

    A startup worker's recorder returns only at the end of its game, 8-18
    minutes after it took the table; the rota needs to hear at the taking, to
    send the next one. The recorder claims a table exactly when it accepts it,
    so the first claim granted is that moment. Everything else is passed
    through, and the recorder takes its claims by duck type, so neither it
    nor the registry knows this wrapper exists.
    """

    __slots__ = ("_claims", "_on_seated", "_told")

    def __init__(self, claims: WorkerClaims, on_seated: Callable[[], None]) -> None:
        """Wrap one worker's claims.

        Args:
            claims: The worker's view of the registry.
            on_seated: Called on the first claim granted, and never again.
        """

        self._claims = claims
        self._on_seated = on_seated
        self._told = False

    def claim(self, table_id: str | None) -> bool:
        """Take a table, as :meth:`WorkerClaims.claim` does, and say so the first time."""

        granted = self._claims.claim(table_id)
        if granted and not self._told:
            self._told = True
            self._on_seated()
        return granted

    def __getattr__(self, name: str) -> Any:
        """Everything else is the wrapped claims'."""

        return getattr(self._claims, name)


class _ReturnFailed(BrowserError):
    """The walk back to the lobby did not land; a fresh session is the way back.

    Not a failure of the worker: nothing has measured that walk, and a
    rebuilt session is its documented fallback, so it spends no budget.
    """


class _HallDeaf(BrowserError):
    """The lobby's page has heard nothing for ``HALL_SILENCE_S``, or only
    keepalives for ``HALL_QUIET_S``.

    Its sockets closed, or stopped delivering, and a wait for a roster would
    last until the fleet stops. The first time is the connection's fault, not
    the account's, so the rebuild spends no budget.
    """


class Fleet:
    """Schedule gate, egress gate, then one browser and N workers per window."""

    __slots__ = (
        "_profile", "_health", "_open_browser", "_open_session", "_egress",
        "_registry", "_headless", "_limits", "_clock", "_monotonic", "_sleep",
        "_recorder", "_workers", "_windows", "_chases", "_gave_up", "_games",
        "_seated", "_rejected", "_records", "_down", "_run_deadline",
        "_seat_deadline", "_hard_deadline", "_census_due", "_census_sightings",
        "_bootstraps", "_bootstrap_games", "_revivals",
        "_rota", "_hold", "_drain_at", "_drained", "_drain_games", "_drain_cut",
        "_hunts", "_hunting", "_hunts_opened", "_hunts_won", "_hunts_lost", "_hunt_runs",
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
        hold: Callable[[], Awaitable[None]] | None = None,
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
            hold: What a drained fleet does instead of returning; waits
                forever by default, until the process is stopped.

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
        self._hold = hold if hold is not None else hold_forever
        self._drain_at: float | None = None
        self._drained = asyncio.Event()
        self._drain_games = self._drain_cut = 0
        self._windows = self._chases = self._gave_up = 0
        self._games = self._seated = self._rejected = 0
        self._bootstraps = self._bootstrap_games = self._revivals = 0
        self._hunts_opened = self._hunts_won = self._hunts_lost = self._hunt_runs = 0
        # Open hunts by the roster's accounts, and each searcher's hunt.
        self._hunts: dict[frozenset[str], _Hunt] = {}
        self._hunting: dict[str, _Hunt] = {}
        self._records: list[Path] = []
        # Every worker down now, retired ones included, in the order they went.
        self._down: dict[str, _Down] = {}
        self._run_deadline: float | None = None
        self._seat_deadline: float | None = None
        self._hard_deadline: float | None = None
        self._census_due = 0
        self._census_sightings: list[tuple[str, bool | None]] = []
        section = self.section
        self._rota = Rota(
            watchers=section.lobby_watchers,
            spares=section.spares,
            stagger_s=section.login_stagger_s,
            monotonic=monotonic,
            stopping=self.stopping,
            health=health,
            bootstrap=section.bootstrap_enabled,
            seekers=section.seekers,
        )
        # Made once and kept across windows: a worker's log, its claims and
        # its failure streak belong to the account, not to one browser.
        self._workers = [Worker(self, account) for account in accounts]

    async def run(self) -> FleetSummary:
        """Run workers inside the schedule until the run's limits are met.

        A drained fleet does not return: once its last game in hand has ended
        it logs ``fleet_drained`` and holds, because a process that exited 0
        would be started again by the service's restart policy. The stop that
        follows finds nothing in hand.

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
            if self._over():
                # A drain that arrived during the probe opens no browser.
                break
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

        if self._drain_at is not None:
            self._health.event(
                "fleet_drained",
                games=self._games - self._drain_games,
                observer_left=self._drain_cut,
                waited_s=round(self._monotonic() - self._drain_at, 1),
            )
            await self._hold()
        return FleetSummary(
            windows=self._windows,
            chases=self._chases,
            chases_given_up=self._gave_up,
            games_recorded=self._games,
            tables_seated=self._seated,
            tables_rejected=self._rejected,
            records=tuple(self._records),
            workers_down=tuple(self._down),
            bootstraps=self._bootstraps,
            bootstrap_games=self._bootstrap_games,
            revivals=self._revivals,
            hunts=self._hunts_opened,
            hunts_won=self._hunts_won,
            hunts_lost=self._hunts_lost,
            hunt_runs=self._hunt_runs,
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

        # A down worker whose wait ran out while no window was open comes back
        # with the others, on probation.
        for worker in self._due():
            self._begin_revival(worker)
        live = [
            worker for worker in self._workers
            if worker.label not in self._down or self._down[worker.label].reviving
        ]
        self._windows += 1
        self._health.event("fleet_window_opened", workers=len(live))
        async with self._open_browser(self._profile, headless=self._headless) as browser:
            # Every worker starts the window logged out; the rota hands the
            # first roles out here, in fleet order, and the rest wait.
            self._rota.open([worker.label for worker in live])
            done = asyncio.Event()
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._beat(done))
                    tasks = {group.create_task(worker.run(browser)) for worker in live}
                    group.create_task(self._reviver(done, browser, group, tasks))
                    # The set grows while the window runs, as workers are
                    # revived; the window lasts until every one has finished,
                    # or the heartbeat would stop under a revived worker.
                    while tasks:
                        await asyncio.wait(set(tasks))
                        tasks.difference_update({task for task in tasks if task.done()})
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
                    probation=sum(entry.reviving for entry in self._down.values()),
                    hunts_open=len(self._hunts),
                    egress_probes=getattr(self._egress, "probes", None),
                    **self._rota.counts(),
                )

    async def _reviver(
        self,
        done: asyncio.Event,
        browser: Any,
        group: asyncio.TaskGroup,
        tasks: set[asyncio.Task[None]],
    ) -> None:
        """Bring down workers back into the window as their waits run out.

        Polled on real time, like the lobby's waits, since the fleet's own
        clock only moves when somebody reads it. A stopping fleet revives
        nobody: the worker would only log out again.

        Args:
            done: Set once every worker of the window has finished.
            browser: The window's browser, which a revived worker opens on.
            group: The window's task group, which a revived worker runs in.
            tasks: The window's worker tasks, which the revived join.
        """

        while not done.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), HALL_POLL_S)
            if done.is_set() or self.stopping():
                continue
            for worker in self._due():
                self._rota.revive(worker.label)
                self._begin_revival(worker)
                tasks.add(group.create_task(worker.run(browser)))

    def _due(self) -> list[Worker]:
        """The down workers whose wait has run out, in fleet order."""

        now = self._monotonic()
        return [
            worker for worker in self._workers
            if (entry := self._down.get(worker.label)) is not None
            and entry.next_try is not None and now >= entry.next_try
        ]

    def _begin_revival(self, worker: Worker) -> None:
        """Put a down worker on probation: one login, one failure allowed."""

        entry = self._down[worker.label]
        entry.reviving = True
        entry.next_try = None
        worker.probation()
        self._revivals += 1
        self._health.event(
            "worker_reviving", worker=worker.label, attempt=entry.attempts + 1
        )

    def _over(self) -> bool:
        """Whether the run is over: drained, or its own limits met — games or time."""

        if self._drain_at is not None:
            return True
        games = self._limits.max_games
        if games is not None and self._games >= games:
            return True
        return (
            self._run_deadline is not None
            and self._monotonic() >= self._run_deadline
        )

    def drain(self) -> None:
        """Take no new game, let the games in hand finish, then stop.

        A schedule close was the only way to do this: a service stop writes
        every game in hand ``interrupted``. A drain is that close, brought
        forward to now. Seating ends at once across the fleet — the rota,
        the lobby and every running recorder, through :meth:`curfew` — and
        each game in hand may run for the schedule's overrun, or not at all
        when ``finish_current_game`` is off. Idle waits end at once. A second
        drain changes nothing.
        """

        if self._drain_at is not None:
            return
        now = self._monotonic()
        schedule = self._profile.schedule
        overrun = (
            schedule.max_overrun_minutes * _MINUTE_S
            if schedule.finish_current_game else 0.0
        )
        self._drain_at = now
        self._drain_games = self._games
        self._seat_deadline = now
        self._hard_deadline = _earliest(self._hard_deadline, now + overrun)
        self._drained.set()
        counts = self._rota.counts()
        self._health.event(
            "fleet_draining",
            in_hand=counts[Role.CHASE.value] + counts[Role.BOOT.value],
            overrun_s=overrun,
        )

    def curfew(self) -> float | None:
        """What a running recorder asks: ``None``, or the seconds a drain leaves it.

        Returns:
            ``None`` until the fleet drains; then how long the game in hand
            may still run, which is never negative.
        """

        if self._drain_at is None:
            return None
        assert self._hard_deadline is not None
        return max(0.0, self._hard_deadline - self._monotonic())

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

    @property
    def rota(self) -> Rota:
        """Who is in the lobby, who stands by, and who is logged out."""

        return self._rota

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

    def absorb(
        self, summary: SessionSummary, *, bootstrap: bool = False, hunt: bool = False
    ) -> None:
        """Add one chase's work, or one startup recording's, to the fleet's totals.

        Both count towards the games recorded, and so towards ``max_games``.

        Args:
            summary: What the recorder did.
            bootstrap: Whether it was a startup worker's, joined mid-game,
                rather than a chase's.
            hunt: Whether it was one searcher's run in a hunt, which is
                counted as a chase once, when the hunt closes.
        """

        if bootstrap:
            self._bootstraps += 1
            self._bootstrap_games += summary.games_recorded
        elif hunt:
            self._hunt_runs += 1
        else:
            self._chases += 1
            if summary.stop_reason is StopReason.CHASE_GAVE_UP:
                self._gave_up += 1
        if self._drain_at is not None and summary.stop_reason is StopReason.TIME_LIMIT:
            # The game in hand outlived the drain's overrun and was left.
            self._drain_cut += summary.games_recorded
        self._games += summary.games_recorded
        self._seated += summary.tables_seated
        self._rejected += summary.tables_rejected
        self._records.extend(summary.records)

    # -- hunts -------------------------------------------------------------------

    def hunted(self, accounts: frozenset[str]) -> bool:
        """Whether a hunt for this roster is open: a start heard again after a rebuild."""

        return accounts in self._hunts

    def open_hunt(self, roster: LobbyRoster, watcher: str, age_s: float) -> bool:
        """Send every free seeker after a start a watcher has read and claimed.

        The watcher stays in the lobby and keeps the roster's claim until the
        hunt closes, so no other watcher chases the same start meanwhile.

        Args:
            roster: The start.
            watcher: The worker that read it.
            age_s: How old the roster was when read.

        Returns:
            Whether a hunt opened. ``False`` when no seeker is free, and the
            watcher chases the start itself.
        """

        free = self._rota.free_seekers()
        if not free:
            return False
        hunt = _Hunt(
            roster=roster, watcher=watcher, searchers=set(free), runs=len(free),
            age_s=age_s, opened_at=self._monotonic(),
        )
        self._hunts[roster.accounts] = hunt
        for label in free:
            self._hunting[label] = hunt
        self._rota.hunt(free)
        self._hunts_opened += 1
        self._health.event(
            "hunt_opened", roster=roster.digest, watcher=watcher, seekers=free,
            age_s=round(age_s, 1),
        )
        return True

    def hunt_of(self, label: str) -> _Hunt | None:
        """The hunt a searcher is on, if it is still on one."""

        return self._hunting.get(label)

    def hunt_won(self, hunt: _Hunt, label: str) -> None:
        """A searcher's claim on the table was granted: it chases, the rest are called off.

        Its seek place is refilled at once, as a watcher's is when it leaves to
        chase: the hunt costs the one login a chase does.
        """

        if hunt.won_by is not None:
            return
        hunt.won_by = label
        self._rota.chasing(label)
        self._hunts_won += 1
        self._health.event(
            "hunt_won", roster=hunt.roster.digest, worker=label,
            after_s=round(self._monotonic() - hunt.opened_at, 1),
        )

    def hunt_leave(self, label: str) -> None:
        """A searcher is out of its hunt; the last one out closes it.

        Called when a searcher's recorder returns and, as a guard, whenever its
        session ends, so a session that fails mid-hunt cannot leave the hunt —
        and the watcher's claim on the roster — open for good. Closing counts
        the hunt as one chase, and as one given up when nobody won it.
        """

        hunt = self._hunting.pop(label, None)
        if hunt is None:
            return
        hunt.searchers.discard(label)
        if hunt.searchers:
            return
        del self._hunts[hunt.roster.accounts]
        self._registry.release_roster(hunt.roster.accounts, hunt.watcher)
        self._chases += 1
        if hunt.won_by is None:
            self._gave_up += 1
            self._hunts_lost += 1
            self._health.event("hunt_lost", roster=hunt.roster.digest, runs=hunt.runs)
        self._health.event(
            "hunt_closed", roster=hunt.roster.digest, won_by=hunt.won_by,
            runs=hunt.runs, open_s=round(self._monotonic() - hunt.opened_at, 1),
        )

    def census_started(self) -> None:
        """Count a startup sweep as under way, until :meth:`census_report` takes it.

        Counted as sweeps start rather than from the workers alive at the
        window's opening: a worker that begins the window logged out may
        never sweep at all, and must not leave the report waiting for it.
        """

        self._census_due += 1

    def census_report(self, sightings: list[tuple[str, bool | None]]) -> None:
        """Take one worker's startup sweep, and say what the sweeps have seen so far.

        The line is the fleet's first measurement of the population it is
        sized against: how many different tournament tables the sweeps met,
        how many times in all, and what population that resighting rate
        suggests — which is what says whether the fleet's size is right. It
        is written after every sweep with the number of sweeps still under
        way, rather than once at the end, so a worker that goes down before
        its sweep costs the report one worker's sightings and not the report.

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
            pending=self._census_due,
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

        A worker going down for the first time waits ``revive_after_minutes``
        for a probation login; one whose probation failed waits twice as long
        as the last time, up to ``revive_max_minutes``, and after
        ``revive_attempts`` failed revivals it is retired for the process.
        Workers on probation and retired ones still count as down.

        Raises:
            ShiftError: Once more than half the fleet is down — the point at
                which a fresh process is worth more than the workers left.
        """

        section = self.section
        entry = self._down.get(label)
        if entry is None:
            entry = self._down[label] = _Down()
        else:
            # Only a worker on probation runs while down: its revival failed.
            entry.reviving = False
            entry.attempts += 1
        after = section.revive_after_minutes * _MINUTE_S
        revive_in: float | None = None
        # Any earlier revival time is spent, whatever comes next.
        entry.next_try = None
        if entry.attempts >= section.revive_attempts:
            entry.retired = True
        elif after:
            revive_in = min(after * 2**entry.attempts, section.revive_max_minutes * _MINUTE_S)
            entry.next_try = self._monotonic() + revive_in
        self._health.event(
            "worker_down", worker=label, down=len(self._down), revive_in_s=revive_in
        )
        if entry.retired:
            self._health.event("worker_retired", worker=label, attempts=entry.attempts)
        self._rota.drop(label)
        if len(self._down) * 2 > len(self._workers):
            raise ShiftError(f"{len(self._down)} of {len(self._workers)} workers are down")

    def worker_revived(self, label: str) -> None:
        """A worker on probation has reached a place: it is up again."""

        entry = self._down.pop(label)
        self._health.event(
            "worker_revived", worker=label, attempt=entry.attempts + 1,
            down=len(self._down),
        )

    async def pause(self, seconds: float) -> None:
        """Wait on the fleet's own clock."""

        await self._sleep(seconds)

    async def idle(self, seconds: float | None = None) -> None:
        """Sleep one schedule poll, never past the run's own deadline nor a drain.

        Args:
            seconds: A shorter wait to take instead of the poll.
        """

        if self._drain_at is not None:
            return
        wait = (
            self._profile.schedule.idle_poll_minutes * _MINUTE_S
            if seconds is None else seconds
        )
        if self._run_deadline is not None:
            wait = max(0.0, min(wait, self._run_deadline - self._monotonic()))
        # The sleep stays the injected one; the drain only cuts it short.
        sleeping = asyncio.ensure_future(self._sleep(wait))
        drained = asyncio.ensure_future(self._drained.wait())
        try:
            await asyncio.wait((sleeping, drained), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeping, drained):
                task.cancel()
            await asyncio.gather(sleeping, drained, return_exceptions=True)


class Worker:
    """One account: log in, wait in the lobby, chase, record, and back again."""

    __slots__ = ("_fleet", "_label", "_profile", "_browser", "_health", "_claims",
                 "_failures", "_censused", "_deaf_streak", "_probation")

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
        # Free rebuilds of a deaf lobby since a roster was last heard.
        self._deaf_streak = 0
        self._probation = False

    @property
    def label(self) -> str:
        """The account's opaque name."""

        return self._label

    def probation(self) -> None:
        """Come back from down with one failure left before going down again.

        A probation costs one login: a worker revived on an account that
        still cannot log in is down again after that one session, not after
        another whole failure budget.
        """

        self._probation = True
        self._failures = FAILURE_BUDGET - 1
        self._deaf_streak = 0

    def _placed(self) -> None:
        """Mark the first place a session reaches: the lobby, a stand-by, a table.

        For a worker on probation that is the proof the account works again:
        it is up, and its failures start from nothing like any fresh worker's.
        """

        if not self._probation:
            return
        self._probation = False
        self._failures = 0
        self._fleet.worker_revived(self._label)

    async def run(self, browser: Any) -> None:
        """Sessions, one after another, until the fleet stops or this worker is down.

        Each session waits for a role first, logged out: a worker only logs
        in once the rota has a place for it.

        Args:
            browser: The window's shared browser, which sessions open on.

        Raises:
            ShiftError: If this worker going down leaves most of the fleet down.
        """

        fleet = self._fleet
        rota = fleet.rota
        self._browser = browser
        blocked = 0
        try:
            while True:
                await self._await_slot()
                if fleet.stopping():
                    return
                reading = await fleet.egress.check()
                if not reading.ok:
                    blocked += 1
                    rota.vacate(self._label, "egress_blocked")
                    self._health.event(
                        "egress_blocked", attempt=blocked, **reading.fields()
                    )
                    if blocked >= EGRESS_BUDGET:
                        fleet.worker_down(self._label)
                        return
                    await fleet.idle()
                    continue
                blocked = 0
                try:
                    await self._session()
                except _ReturnFailed as error:
                    # A free rebuild keeps the worker's place: nobody is woken
                    # to replace a worker that is only logging in again.
                    self._health.event("return_rebuilt", error=str(error))
                except _HallDeaf:
                    # Logged as `hall_deaf` where it was noticed. Straight back
                    # to the egress check and a fresh context, with no budget
                    # spent and the place kept.
                    self._deaf_streak += 1
                except Exception as error:  # noqa: BLE001 - one session must not end the worker
                    self._failures += 1
                    self._health.event(
                        "session_failed", attempt=self._failures,
                        error=f"{type(error).__name__}: {error}",
                    )
                    # Given up before the idle poll, not after: the next
                    # worker in line takes the place now.
                    rota.vacate(self._label, "session_failed")
                    if self._failures >= FAILURE_BUDGET:
                        fleet.worker_down(self._label)
                        return
                    # A first failure is most often one bad page, and when no
                    # other worker is free the place it gave up stays empty —
                    # the lobby it held can go unwatched. So it tries again
                    # soon; a second in a row looks like the site, and waits
                    # the whole poll as before.
                    await fleet.idle(
                        FIRST_RETRY_S if self._failures == 1 else None
                    )
        finally:
            rota.vacate(self._label, "stopped")

    async def _await_slot(self) -> None:
        """Wait logged out until the rota has a place, then take a login's turn.

        The wait is on real time and is cut short when the worker is woken;
        the login stagger that follows is on the fleet's clock, as it always
        was. Returns early, with no role, once the fleet is stopping.
        """

        fleet = self._fleet
        rota = fleet.rota
        while rota.ask(self._label) is Role.OUT:
            if fleet.stopping():
                return
            await rota.wait(self._label, HALL_POLL_S)
        delay = rota.login_delay()
        if delay:
            await fleet.pause(delay)

    async def _session(self) -> None:
        """One browser context: log in, then serve whatever role the rota gives.

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
                    self._fleet.rota.logged_in()
                    reason = await self._serve(spectator, frames, log)
                except BrowserError:
                    await self._capture(spectator, log.path)
                    raise
        finally:
            log.close()
            # However the session ended, it hunts no more: a searcher whose
            # page failed mid-hunt must not hold the hunt open behind it.
            self._fleet.hunt_leave(self._label)
        self._health.event("session_ended", session=session, reason=reason)

    async def _serve(self, spectator: Any, frames: Any, log: RawLogWriter) -> str:
        """Serve the worker's role from wherever the page stands, until it ends.

        The session is a loop over the page's place: a login leaves it on the
        menu, a chase leaves it at a table, and each place has its own walk to
        the lobby. The role is read again at the top of every turn, since the
        rota changes it between games — and first of all right after the
        login, because a spare still logging in may already have been sent in.
        A worker the rota has no place for closes its session, which is its
        logout. The lobby's readers are made on the first arrival and kept for
        the whole session.

        Returns:
            Why the session ended: ``parked`` when the rota took the worker's
            place away, ``stopping``, or ``egress_blocked``.

        Raises:
            BrowserError: A step of the walk failed, or the lobby shows no
                tournament row.
            _ReturnFailed: The walk back from a table did not land.
        """

        fleet = self._fleet
        rota = fleet.rota
        # Only a worker sent straight to the lobby by its login sweeps, and
        # only in the window's opening wave; a spare's session never does.
        sweep = rota.role(self._label) is Role.HALL and rota.opening
        at = _Place.MENU
        # The run loop has just asked the egress; any later walk asks again.
        checked = True
        reader: tuple[LobbyWatcher, WireStream] | None = None
        while not fleet.stopping():
            role = rota.role(self._label)
            if role is Role.OUT:
                return "parked"
            if role in (Role.SPARE, Role.SEEK):
                at = await self._stand_by(spectator, frames, log, at, hold=role)
                checked = False
                continue
            if not checked and not (await fleet.egress.check()).ok:
                return "egress_blocked"
            if role is Role.BOOT:
                # Only ever handed to a worker logged out, so the page is
                # where its login left it.
                left = await self._bootstrap(spectator, frames, log)
            elif role is Role.HUNT:
                left = await self._hunt(spectator, frames, log, at)
            else:
                if not await self._reach_lobby(spectator, frames, log, at, sweep=sweep):
                    return self._ended()
                if reader is None:
                    reader = await self._lobby_reader(spectator)
                left = await self._hall(spectator, frames, log, *reader)
            if left is None:
                return self._ended()
            at, checked = left, False
        return "stopping"

    def _ended(self) -> str:
        """Why a walk or a chase that stopped short ended the session."""

        return "stopping" if self._fleet.stopping() else "egress_blocked"

    async def _stand_by(
        self, spectator: Any, frames: Any, log: RawLogWriter, at: _Place, *, hold: Role
    ) -> _Place:
        """Stand by off the lobby, logged in, until sent somewhere or no longer needed.

        A spare or a seeker coming from a table steps off it first, onto the
        online menu. From there, or from where its login left it, it only
        drains its page's frames into the raw log. Each wait for a frame is
        cut short the moment the rota changes the worker's role: a seeker's
        hunt cannot afford the poll's second. There is no silence watchdog
        here: such a page never hears a roster to prove it works, and nobody
        has measured how quiet a page off the lobby is. ``spare_silent``
        records that, once per spell of silence.

        Args:
            spectator: The worker's browser half, logged in.
            frames: Its frame source.
            log: The session's raw log.
            at: Where the page stands now.
            hold: The role being held, :attr:`Role.SPARE` or :attr:`Role.SEEK`.

        Returns:
            Where the page stands once the wait is over.

        Raises:
            BrowserError: The frames ended, which only a closed page does.
        """

        fleet = self._fleet
        rota = fleet.rota
        if at is _Place.TABLE:
            exited = await spectator.exit_table()
            self._health.event("table_exited", exited=exited)
        # ``place``, not ``at``: every line's ``at`` is its timestamp.
        self._health.event("standing_by", place=at.value, role=hold.value)
        self._placed()
        iterator = aiter(frames)
        interval = self._profile.recorder.health_interval_s
        heard = fleet.now()
        told = False
        while rota.role(self._label) is hold and not fleet.stopping():
            if self._health.due(interval):
                self._health.heartbeat(state=hold.value)
            frame = await self._frame_or_wake(iterator)
            if frame is not None:
                log.write_frame(frame)
                if frame.direction == RECEIVED:
                    heard, told = fleet.now(), False
            silent = fleet.now() - heard
            if silent >= HALL_SILENCE_S and not told:
                self._health.event("spare_silent", silent_s=round(silent, 1))
                told = True
        return at

    async def _frame_or_wake(self, iterator: Any) -> Any:
        """The page's next frame within ``STANDBY_POLL_S``, or ``None`` once woken.

        The two waits race; the one still pending is cancelled, which loses
        nothing — the frame source keeps a frame it had not yet handed out.

        Raises:
            BrowserError: The frames ended, which only a closed page does.
        """

        reading = asyncio.ensure_future(anext(iterator))
        woken = asyncio.ensure_future(self._fleet.rota.wait(self._label, STANDBY_POLL_S))
        try:
            await asyncio.wait((reading, woken), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reading, woken):
                task.cancel()
            await asyncio.gather(reading, woken, return_exceptions=True)
        if reading.cancelled():
            return None
        try:
            return reading.result()
        except StopAsyncIteration:
            raise BrowserError("the frame source ended: the page has gone") from None

    async def _reach_lobby(
        self, spectator: Any, frames: Any, log: RawLogWriter, at: _Place, *, sweep: bool
    ) -> bool:
        """Take the page to the lobby from the menu or from a table.

        Args:
            spectator: The worker's browser half, logged in.
            frames: Its frame source.
            log: The session's raw log.
            at: Where the page stands now.
            sweep: Whether this walk may be the worker's startup census.

        Returns:
            Whether the page is in the lobby. ``False`` means the walk stopped
            short and the session should end where it stands.

        Raises:
            _ReturnFailed: The walk back from a table did not land.
        """

        if at is _Place.MENU:
            return await self._arrive(spectator, frames, log, sweep=sweep)
        try:
            await spectator.return_to_lobby()
        except BrowserError as error:
            raise _ReturnFailed(str(error)) from error
        return True

    async def _lobby_reader(self, spectator: Any) -> tuple[LobbyWatcher, WireStream]:
        """The session's readers of the lobby, made once on its first arrival.

        One of each for the whole session, not one per wait. The lobby sends
        every event on both sockets, and a wait resumed after a roster was
        refused would otherwise meet the second copy as news and announce the
        same start twice.

        Raises:
            BrowserError: The lobby shows no tournament row.
        """

        table_hash = await spectator.read_tournament_hash()
        if table_hash is None:
            raise BrowserError("[selectors].lobby_row_tournament_class matched no row")
        return LobbyWatcher(self._profile, table_hash), WireStream(self._profile.wire)

    async def _arrive(
        self, spectator: Any, frames: Any, log: RawLogWriter, *, sweep: bool
    ) -> bool:
        """Reach the lobby — by way of the startup census, the first time.

        Args:
            spectator: The worker's browser half, logged in.
            frames: Its frame source.
            log: The session's raw log.
            sweep: Whether this session may sweep at all: only a watcher's,
                in the window's opening wave.

        Returns:
            Whether the worker is in the lobby. ``False`` means the census was
            cut short — by the fleet stopping or the egress refusing — and the
            session should end where it stands.

        Raises:
            _ReturnFailed: The walk back from the census's last table did not
                land.
        """

        fleet = self._fleet
        if not (sweep and fleet.section.census_enabled) or self._censused:
            await spectator.enter_lobby()
            return True
        # Marked before the sweep, not after: a sweep that fails is not one to
        # repeat at the price of every later session's login.
        self._censused = True
        if fleet.section.bootstrap_enabled:
            # The startup workers' own gates fill the registry's census as
            # they scan, and they record what the sweep would only have seen.
            self._health.event("census_skipped", reason="bootstrap")
            await spectator.enter_lobby()
            return True
        fleet.census_started()
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
        self,
        spectator: Any,
        frames: Any,
        log: RawLogWriter,
        watcher: LobbyWatcher,
        stream: WireStream,
    ) -> _Place | None:
        """Wait in the lobby for one start, and chase it.

        Args:
            spectator: The worker's browser half, in the lobby.
            frames: Its frame source.
            log: The session's raw log.
            watcher: The session's reader of the tournament row.
            stream: The session's de-duplicating reader of the socket.

        Returns:
            :attr:`_Place.TABLE` once a chase has run its course, or ``None``
            when the session should end where it stands: the fleet has
            stopped, or the chase's egress was refused.

        Raises:
            BrowserError: The frames ended, which only a closed page does.
        """

        fleet = self._fleet
        registry = fleet.registry
        while True:
            # The wait is where a stopping fleet is noticed, before and
            # between chases alike: it returns no roster once told to stop.
            roster = await self._await_roster(frames, log, watcher, stream)
            if roster is None:
                return None
            age = frames.elapsed - roster.at
            if age > fleet.section.roster_max_age_s:
                # Read too late to be the game about to start; chasing it
                # would find a game already under way.
                self._health.event("roster_stale", roster=roster.digest, age_s=round(age, 1))
                continue
            if not registry.claim_roster(roster.accounts, self._label):
                self._health.event("roster_taken", roster=roster.digest)
                continue
            if fleet.hunted(roster.accounts):
                # This watcher's own start, heard again on a rebuilt session:
                # its seekers are on it already.
                self._health.event("hunt_reannounced", roster=roster.digest)
                continue
            if fleet.open_hunt(roster, self._label, age):
                # The seekers hunt it; this watcher stays for the next start.
                continue
            # No seeker free: the watcher chases the start itself. Before
            # anything awaits, so the spare is sent in while it walks out.
            fleet.rota.chasing(self._label)
            try:
                self._health.event("chase_started", roster=roster.digest)
                await spectator.enter_table_from_lobby()
                summary = await self._chase(spectator, frames, log, roster)
            finally:
                registry.release_roster(roster.accounts, self._label)
            fleet.absorb(summary)
            fleet.rota.done(self._label)
            return self._settle(summary)

    def _settle(self, summary: SessionSummary) -> _Place | None:
        """What a recorder that ran its course leaves the session to do next.

        Args:
            summary: What the chase or the startup recording did.

        Returns:
            :attr:`_Place.TABLE`, where the page now stands, or ``None`` when
            the session should end there: the egress was refused, or the
            fleet has stopped.

        Raises:
            BrowserError: The frames ended, which only a closed page does.
        """

        if summary.stop_reason is StopReason.SOURCE_ENDED:
            raise BrowserError("the frame source ended: the page has gone")
        # A recorder that ran its course is progress, whatever it found.
        self._failures = 0
        if summary.stop_reason is StopReason.EGRESS_BLOCKED or self._fleet.stopping():
            # Nothing more goes to the site on this session: the worker's
            # own loop checks the egress before the next.
            return None
        return _Place.TABLE

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
            _HallDeaf: Nothing was received for ``HALL_SILENCE_S``, or no site
                event for ``HALL_QUIET_S``, the first time since a roster was
                last heard.
            BrowserError: The frames ended, or the lobby went deaf again
                before any roster was heard — which counts against the
                worker, so an account kicked out at every login cannot log
                in every minute.
        """

        fleet = self._fleet
        iterator = aiter(frames)
        interval = self._profile.recorder.health_interval_s
        self._health.event("in_hall")
        self._placed()
        # Two watchdogs. ``heard`` listens to the connection: any received
        # frame counts, keepalives included, and only what we sent ourselves
        # does not. ``news`` listens to the site: only a frame that is a site
        # event counts, any event and from either socket, a copy included.
        heard = news = fleet.now()
        while not fleet.stopping():
            if self._health.due(interval):
                self._health.heartbeat(state="hall", lobby_states=watcher.states)
            try:
                frame = await asyncio.wait_for(anext(iterator), HALL_POLL_S)
            except TimeoutError:
                self._check_heard(heard, news, watcher)
                continue
            except StopAsyncIteration:
                raise BrowserError("the frame source ended: the page has gone") from None
            log.write_frame(frame)
            # The stream counts every frame that unwrapped to an event before
            # dropping the copies, so a rise is an event, copy or not.
            unwrapped = stream.received
            event = stream.ingest(frame.text, frame.socket)
            if frame.direction == RECEIVED:
                heard = fleet.now()
                if stream.received > unwrapped:
                    news = heard
            self._check_heard(heard, news, watcher)
            if event is None:
                continue
            roster = watcher.read(event, at=frame.at)
            if roster is not None:
                self._deaf_streak = 0
                self._health.event("roster_announced", roster=roster.digest)
                return roster
        return None

    def _check_heard(self, heard: float, news: float, watcher: LobbyWatcher) -> None:
        """Give up on a lobby that has sent nothing for ``HALL_SILENCE_S``,
        or nothing but keepalives for ``HALL_QUIET_S``.

        Args:
            heard: When the last frame was received, on the fleet's clock.
            news: When the last site event was received, on the same clock.
            watcher: The session's reader of the tournament row, whose count
                of lobby states the event carries.

        Raises:
            _HallDeaf: The first deafness since a roster was last heard.
            BrowserError: Another one, which counts against the worker.
        """

        fleet = self._fleet
        silent = fleet.now() - heard
        quiet = fleet.now() - news
        if (silent < HALL_SILENCE_S and quiet < HALL_QUIET_S) or fleet.stopping():
            # A stopping fleet is noticed at the top of the wait, and a clock
            # moved past every deadline is not a silence.
            return
        self._health.event(
            "hall_deaf", silent_s=round(silent, 1), quiet_s=round(quiet, 1),
            lobby_states=watcher.states,
        )
        message = (
            f"the lobby sent nothing for {silent:.0f} s" if silent >= HALL_SILENCE_S
            else f"the lobby sent only keepalives for {quiet:.0f} s"
        )
        if self._deaf_streak == 0:
            raise _HallDeaf(message)
        raise BrowserError(f"{message}, again, with no roster heard since")

    async def _chase(
        self,
        spectator: Any,
        frames: Any,
        log: RawLogWriter,
        roster: LobbyRoster,
        hunt: _Hunt | None = None,
    ) -> SessionSummary:
        """Run one chase's recorder on this worker's session.

        Args:
            spectator: The worker's browser half, on its way to a table.
            frames: Its frame source.
            log: The session's raw log.
            roster: The start to find, on this page's clock.
            hunt: The hunt this chase is one searcher of, if it is: its first
                granted table claim wins the hunt, and it is called off as
                soon as another searcher's does.
        """

        fleet = self._fleet
        label = self._label
        seat_in, hard_in = fleet.seconds_left()
        claims: Any = self._claims
        called_off: Callable[[], bool] | None = None
        if hunt is not None:
            searched = hunt

            def lost() -> bool:
                return searched.won_by not in (None, label)

            claims = _SeatedClaims(self._claims, lambda: fleet.hunt_won(searched, label))
            called_off = lost
        recorder = fleet.recorder(
            spectator, frames, self._profile, self._health,
            limits=RecorderLimits(max_seconds=hard_in, seat_until_s=seat_in),
            raw=log,
            egress=fleet.egress,
            claims=claims,
            curfew=fleet.curfew,
            target=ChaseTarget(
                roster=roster,
                distinct_budget=fleet.section.scan_distinct_budget,
                deadline_s=fleet.section.scan_deadline_s,
                called_off=called_off,
            ),
        )
        return await recorder.run()

    async def _hunt(
        self, spectator: Any, frames: Any, log: RawLogWriter, at: _Place
    ) -> _Place | None:
        """Hunt the start this seeker was sent after, beside the other seekers.

        The walk in depends on where the page stands: from the menu a login
        leaves, the observe branch is walked from the top; from the online
        menu a table's exit lands on, it is the same back-out a watcher
        takes from the lobby, which fails fast when the page is still at a
        table — a free rebuild, like a walk back that does not land.

        Returns:
            Where the page is left, or ``None`` when the session should end
            there — as after a chase.

        Raises:
            _ReturnFailed: The walk to the observe branch did not land.
            BrowserError: A step of the walk from the menu failed, or the
                frames ended.
        """

        fleet = self._fleet
        hunt = fleet.hunt_of(self._label)
        if hunt is None or hunt.won_by is not None:
            # Over before this seeker set off: it was still logging in when
            # the table was found, or its last session left the hunt.
            fleet.hunt_leave(self._label)
            fleet.rota.done(self._label)
            return at
        try:
            self._health.event("hunt_started", roster=hunt.roster.digest, place=at.value)
            if at is _Place.MENU:
                await spectator.enter_variant()
            else:
                try:
                    await spectator.enter_table_from_lobby()
                except BrowserError as error:
                    raise _ReturnFailed(str(error)) from error
            roster = hunt.roster_for(frames.elapsed, fleet.now())
            summary = await self._chase(spectator, frames, log, roster, hunt)
        finally:
            fleet.hunt_leave(self._label)
        fleet.absorb(summary, hunt=True)
        fleet.rota.done(self._label)
        return self._settle(summary)

    async def _bootstrap(
        self, spectator: Any, frames: Any, log: RawLogWriter
    ) -> _Place | None:
        """Join one game already under way and record it from there.

        The ordinary seating, untargeted: the server offers tables and the
        recorder's own gates keep what is worth taking — one worker per table
        (``claimed_by_other``), tournaments only, nothing too far along
        (``hop_after_rows``), no game recorded before. It takes one game, and
        seats nothing after ``bootstrap_scan_s``. The next startup worker is
        sent as soon as this one takes its table, not when its game ends.

        Returns:
            Where the page is left, or ``None`` when the session should end
            there — as after a chase.

        Raises:
            BrowserError: A step of the walk failed, or the frames ended.
        """

        fleet = self._fleet
        await spectator.enter_variant()
        self._placed()
        seat_in, hard_in = fleet.seconds_left()
        recorder = fleet.recorder(
            spectator, frames, self._profile, self._health,
            limits=RecorderLimits(
                max_games=1,
                max_seconds=hard_in,
                seat_until_s=_earliest(seat_in, fleet.section.bootstrap_scan_s),
            ),
            raw=log,
            egress=fleet.egress,
            claims=_SeatedClaims(self._claims, lambda: fleet.rota.seated(self._label)),
            curfew=fleet.curfew,
        )
        summary = await recorder.run()
        fleet.absorb(summary, bootstrap=True)
        # The rota knows by now whether a table was taken: the claim that
        # seated one told it. Not the stop reason, since a game seated and
        # watched can end on the window closing as well as an empty scan.
        fleet.rota.done(self._label)
        return self._settle(summary)

    async def _capture(self, spectator: Any, raw: Path) -> None:
        """Save the page beside the session's raw log, if the profile asks."""

        if not self._profile.browser.screenshot_on_error:
            return
        saved = await spectator.capture(raw.with_suffix(""))
        self._health.event("failure_captured", files=[str(path) for path in saved])
