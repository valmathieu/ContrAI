"""A shift: the schedule says when, the egress says whether, a recorder says what.

``contrai-scrape run`` is a process meant to stay up for weeks in a container,
while the site is only watched inside the profile's windows and only through
the tunnel. This loop is what reconciles the two. Outside the window it holds
no browser at all — a closed browser costs nothing and cannot drift. Inside
it, every session starts with an egress check, gets a raw log of its own, and
hands the recorder the deadlines the window implies.

What it does **not** do is try forever. A tunnel refused six checks in a row,
or a browser that failed three sessions running, ends the process with a
distinct exit code. Inside a container that is the right move rather than a
give-up: the supervisor restarts it, and a fresh process is also what
re-attaches to a VPN container that was recreated underneath it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from .exceptions import BrowserError, ShiftError
from .health import HealthLog
from .profile import Profile
from .rawlog import RawLogWriter, new_session_id, prune_raw_logs, raw_path
from .recorder import Recorder, RecorderLimits, SessionSummary, StopReason

#: Refused egress checks in a row before the process hands itself back.
EGRESS_BUDGET: Final[int] = 6

#: Failed sessions in a row before the same.
FAILURE_BUDGET: Final[int] = 3

#: Seconds in a minute: the schedule counts its poll and overrun in minutes.
_MINUTE_S: Final[float] = 60.0


@dataclass(frozen=True, slots=True)
class ShiftSummary:
    """What a whole ``run`` did, across its sessions."""

    sessions: int
    games_recorded: int
    tables_seated: int
    tables_rejected: int
    records: tuple[Path, ...]


class Shift:
    """Schedule gate, egress gate, then one recorder per browser session."""

    __slots__ = (
        "_profile", "_health", "_open", "_egress", "_headless", "_limits", "_clock",
        "_monotonic", "_sleep", "_recorder", "_sessions", "_games", "_seated",
        "_rejected", "_records",
    )

    def __init__(
        self,
        profile: Profile,
        health: HealthLog,
        *,
        open_session: Callable[..., AbstractAsyncContextManager[tuple[Any, Any]]],
        egress: Any,
        headless: bool | None = None,
        limits: RecorderLimits = RecorderLimits(),
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        recorder: Callable[..., Any] = Recorder,
    ) -> None:
        """Wire a shift up.

        Every collaborator is injected, so the loop runs in tests with no
        browser, no network and no real time.

        Args:
            profile: The loaded profile; its ``[schedule]`` and ``[output]``
                drive the loop.
            health: Where transitions are written, shared by every session so
                its counters run for the whole process.
            open_session: ``open_spectator``, or anything shaped like it:
                called with the profile and ``headless``/``health`` keywords,
                it yields a spectator and a frame source.
            egress: Anything with ``async check() -> EgressReading``.
            headless: Override for ``[browser].headless``; ``None`` takes it.
            limits: The run's own limits. ``max_games`` counts across sessions
                and ``max_seconds`` runs on the monotonic clock;
                ``seat_until_s`` is ignored, since the schedule sets each
                session's.
            clock: The wall clock the schedule is read on; defaults to UTC now.
            monotonic: The clock the run's time limit is measured on.
            sleep: How the loop waits between polls.
            recorder: Builds a session's recorder; :class:`Recorder` itself
                outside tests.
        """

        self._profile = profile
        self._health = health
        self._open = open_session
        self._egress = egress
        self._headless = headless
        self._limits = limits
        self._clock = clock if clock is not None else _utc_now
        self._monotonic = monotonic
        self._sleep = sleep
        self._recorder = recorder
        self._sessions = self._games = self._seated = self._rejected = 0
        self._records: list[Path] = []

    async def run(self) -> ShiftSummary:
        """Watch inside the schedule until the run's limits are met.

        Returns:
            What every session did, added up.

        Raises:
            ShiftError: A failure budget was spent.
        """

        deadline = (
            None
            if self._limits.max_seconds is None
            else self._monotonic() + self._limits.max_seconds
        )
        schedule = self._profile.schedule
        idle = False
        blocked = failures = 0
        while not self._done(deadline):
            now = self._clock()
            if not schedule.is_active(now):
                if not idle:
                    # Once per closed stretch, not once per poll: a night of
                    # identical lines would bury the one that matters.
                    self._health.event(
                        "schedule_idle", next_opening=_stamp(schedule.next_change(now))
                    )
                    idle = True
                await self._idle(deadline)
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
                await self._idle(deadline)
                continue
            blocked = 0
            # The one line per session that says which network it ran on.
            self._health.event("egress_ok", **reading.fields())
            self._prune(now)

            try:
                summary = await self._session(now, deadline)
            except Exception as error:  # noqa: BLE001 - one session must not end the shift
                failure: str | None = f"{type(error).__name__}: {error}"
            else:
                self._absorb(summary)
                # A live frame source only ends when its browser has gone,
                # which is a failure however calmly the recorder took it.
                failure = (
                    "the frame source ended"
                    if summary.stop_reason is StopReason.SOURCE_ENDED
                    else None
                )
            if failure is None:
                failures = 0
                continue
            failures += 1
            self._health.event("session_failed", attempt=failures, error=failure)
            if failures >= FAILURE_BUDGET:
                raise ShiftError(f"{failures} sessions failed in a row: {failure}")
            await self._idle(deadline)

        return ShiftSummary(
            sessions=self._sessions,
            games_recorded=self._games,
            tables_seated=self._seated,
            tables_rejected=self._rejected,
            records=tuple(self._records),
        )

    async def _session(self, now: datetime, deadline: float | None) -> SessionSummary:
        """Open a browser, run one recorder under the window's deadlines, close it.

        Args:
            now: The instant the session opens at, inside the window.
            deadline: The run's own monotonic deadline, if it has one.

        Returns:
            What the session's recorder did.
        """

        schedule = self._profile.schedule
        # Both instants are UTC, so the subtraction is real elapsed time; the
        # daylight-saving reasoning lives in the schedule module.
        close = schedule.next_change(now)
        seat_until = None if close is None else (close - now).total_seconds()
        hard = seat_until
        if hard is not None and schedule.finish_current_game:
            hard += schedule.max_overrun_minutes * _MINUTE_S
        remaining = None if deadline is None else deadline - self._monotonic()
        games = self._limits.max_games
        limits = RecorderLimits(
            max_games=None if games is None else games - self._games,
            max_seconds=_earliest(hard, remaining),
            seat_until_s=_earliest(seat_until, remaining),
        )

        session = new_session_id(now=now)
        log = RawLogWriter(raw_path(self._profile.output.raw_root, session))
        headless = (
            self._profile.browser.headless if self._headless is None else self._headless
        )
        self._health.event(
            "session_started", session=session, raw=str(log.path), headless=headless
        )
        self._sessions += 1
        try:
            async with self._open(
                self._profile, headless=self._headless, health=self._health
            ) as (spectator, frames):
                try:
                    await spectator.log_in()
                    await spectator.enter_variant()
                    recorder = self._recorder(
                        spectator, frames, self._profile, self._health,
                        limits=limits, raw=log, egress=self._egress,
                    )
                    summary = await recorder.run()
                except BrowserError:
                    # The one moment the page can still be asked what it
                    # looked like. A step that fails names the profile key it
                    # was on, which never says whether the control was
                    # missing, covered or off-screen — and the browser is
                    # closed by the time the caller reads the error.
                    await self._capture(spectator, log.path)
                    raise
        finally:
            log.close()
        self._health.event(
            "session_ended", session=session, reason=str(summary.stop_reason)
        )
        return summary

    async def _capture(self, spectator: Any, raw: Path) -> None:
        """Save the page beside the session's raw log, if the profile asks.

        Args:
            spectator: The session's browser half, still open.
            raw: The session's raw log, whose stem the files share so a
                failure's evidence sorts next to the frames that led to it.
        """

        if not self._profile.browser.screenshot_on_error:
            return
        saved = await spectator.capture(raw.with_suffix(""))
        self._health.event("failure_captured", files=[str(path) for path in saved])

    def _done(self, deadline: float | None) -> bool:
        """Whether the run's own limits are met: games across sessions, or time."""

        games = self._limits.max_games
        if games is not None and self._games >= games:
            return True
        return deadline is not None and self._monotonic() >= deadline

    async def _idle(self, deadline: float | None) -> None:
        """Sleep one poll, never past the run's own deadline."""

        wait = self._profile.schedule.idle_poll_minutes * _MINUTE_S
        if deadline is not None:
            wait = max(0.0, min(wait, deadline - self._monotonic()))
        await self._sleep(wait)

    def _prune(self, now: datetime) -> None:
        """Delete raw logs past their retention, and say so when any went."""

        output = self._profile.output
        removed = prune_raw_logs(output.raw_root, output.raw_retention_days, now=now)
        if removed:
            self._health.event("raw_logs_pruned", count=len(removed))

    def _absorb(self, summary: SessionSummary) -> None:
        """Add one session's work to the running totals."""

        self._games += summary.games_recorded
        self._seated += summary.tables_seated
        self._rejected += summary.tables_rejected
        self._records.extend(summary.records)


def _earliest(*limits: float | None) -> float | None:
    """The smallest limit that is set, or ``None`` when none is."""

    present = [limit for limit in limits if limit is not None]
    return min(present) if present else None


def _stamp(instant: datetime | None) -> str | None:
    """An instant as the health log spells one."""

    return None if instant is None else instant.isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_now() -> datetime:
    """The current instant, in UTC."""

    return datetime.now(UTC)
