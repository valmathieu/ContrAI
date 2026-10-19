"""Which of a fleet's workers are logged in, and what each one is there for.

A worker waiting in the lobby holds a login and a browser context — about
0.43 GB — whether a game starts or not. In the 7-worker run of 2026-09-27
between two and six of them sat there at every moment, for one start every
2.5-5 minutes. The rota hands out roles instead, before each login:

* at most ``watchers`` workers wait in the lobby (:attr:`Role.HALL`);
* ``spares`` more stay logged in off it (:attr:`Role.SPARE`), so a watcher
  that leaves to chase is replaced by a walk, not by a login;
* ``seekers`` more wait on the online menu (:attr:`Role.SEEK`): when a watcher
  reads a start, every free seeker hunts it at once (:attr:`Role.HUNT`) while
  the watcher stays in the lobby;
* every other idle worker closes its session and waits logged out
  (:attr:`Role.OUT`) until a slot needs it, longest-waiting first.

**One rule.** Every change ends in :meth:`Rota._fill`: fill the lobby from the
spare first, then from an idle seeker, then from the queue of logged-out
workers; then the seek places from the spare and the queue; then the spare's
place from the queue. A chase therefore costs one login — the worker woken to
be the next spare — and so does a hunt, since only its winner leaves the seek
pool. A chaser that comes back to every place full is parked.

**The startup phase.** A window opens on games already under way, which no
lobby will announce. With ``bootstrap`` on, the rule starts differently: one
watcher first, then logged-out workers sent one at a time to join a running
table and record it from where they join (:attr:`Role.BOOT`), the next only
once the last has taken its table. The phase ends at the first worker that
finds none, or fails, or once the workers left to send would no longer fill
the second watcher's and the spare's places — so the lobby is never left thin
for the 8-18 minutes a mid-game recording lasts.

**Clamped to the fleet.** Both numbers are cut down to the workers still up,
so a fleet of one has one watcher and no spare, which is exactly the fleet
before the rota. A worker that goes down leaves the rota (:meth:`Rota.drop`)
and a revived one rejoins it logged out (:meth:`Rota.revive`).

**Atomic without a lock**, for the registry's reason: every worker runs in one
event loop, and nothing here awaits except :meth:`Rota.wait`, which changes
nothing. A check and the set that follows it cannot be interleaved.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable, Sequence
from enum import StrEnum

from .health import HealthLog


class Role(StrEnum):
    """What a worker is for, right now."""

    OUT = "out"
    """Logged out, queued for the next slot."""

    HALL = "hall"
    """In the lobby, or on its way there, waiting for a start."""

    SPARE = "spare"
    """Logged in off the lobby, ready to walk in when a watcher leaves."""

    SEEK = "seek"
    """Logged in on the online menu, ready to hunt the next start announced."""

    HUNT = "hunt"
    """A seeker hunting a start beside the others; it still holds its seek place."""

    CHASE = "chase"
    """Out of the lobby, chasing a start and watching its game."""

    BOOT = "boot"
    """In a window's startup phase: joining a game already under way."""


class Rota:
    """The fleet's roles: who waits in the lobby, who stands by, who is out."""

    __slots__ = ("_watchers", "_spares", "_seekers", "_stagger_s", "_monotonic",
                 "_stopping", "_health", "_bootstrap", "_live", "_roles", "_queue",
                 "_wakes", "_next_login", "_logins", "_opening", "_phase", "_scanning",
                 "_sent")

    def __init__(
        self,
        *,
        watchers: int,
        spares: int,
        stagger_s: float,
        monotonic: Callable[[], float],
        stopping: Callable[[], bool],
        health: HealthLog,
        bootstrap: bool = False,
        seekers: int = 0,
    ) -> None:
        """A rota with nobody on it until :meth:`open`.

        Args:
            watchers: At most this many workers in the lobby.
            spares: This many logged in off it.
            seekers: This many on the online menu, ready to hunt a start.
            stagger_s: The least time between one login and the next.
            monotonic: The clock logins are spaced on.
            stopping: Whether the fleet has stopped handing out work; once it
                has, nobody is woken.
            health: The fleet's log, which every change of role is written to.
            bootstrap: Whether each window opens with a startup phase.
        """

        self._watchers = watchers
        self._spares = spares
        self._seekers = seekers
        self._stagger_s = stagger_s
        self._monotonic = monotonic
        self._stopping = stopping
        self._health = health
        self._bootstrap = bootstrap
        self._live: list[str] = []
        self._roles: dict[str, Role] = {}
        self._queue: deque[str] = deque()
        self._wakes: dict[str, asyncio.Event] = {}
        self._next_login: float | None = None
        self._logins = 0
        self._opening = False
        self._phase = False
        self._scanning: str | None = None
        self._sent = 0

    # -- a window ------------------------------------------------------------

    def open(self, live: Sequence[str]) -> None:
        """Start a window: every live worker logged out, queued in fleet order.

        The whole fleet is queued here rather than as each worker first asks:
        the first worker's task runs its ask before the others are scheduled,
        and a queue built lazily would look empty to the startup phase.

        Args:
            live: The labels of the workers still up, in the order they log in.
        """

        self._live = list(live)
        self._roles = dict.fromkeys(self._live, Role.OUT)
        self._queue = deque(self._live)
        self._wakes = {label: asyncio.Event() for label in self._live}
        self._next_login = None
        self._opening = True
        self._phase = self._bootstrap
        self._scanning = None
        self._sent = 0
        self._fill()

    @property
    def opening(self) -> bool:
        """Whether the window is still in its opening wave: no chase yet."""

        return self._opening

    # -- what a worker asks --------------------------------------------------

    def ask(self, label: str) -> Role:
        """Between sessions: join the queue if out, and say what the role is.

        Args:
            label: The asking worker.

        Returns:
            Its role, which is :attr:`Role.OUT` until a slot is found for it.
        """

        if self._roles[label] is Role.OUT and label not in self._queue:
            self._queue.append(label)
        self._fill()
        return self._roles[label]

    def role(self, label: str) -> Role:
        """A worker's role, read again wherever it may have changed."""

        return self._roles[label]

    async def wait(self, label: str, timeout: float) -> None:
        """Wait, logged out, until woken or ``timeout`` seconds have passed.

        Real time, never the fleet's own clock: the fleet's clock is shared,
        and a logged-out worker moving it on would age every other worker's
        silence watchdog.

        Args:
            label: The waiting worker.
            timeout: The longest wait, so a stopping fleet is still noticed.
        """

        wake = self._wakes[label]
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), timeout)
        wake.clear()

    def login_delay(self) -> float:
        """Seconds to wait before logging in, so logins arrive one at a time.

        Each login takes the next turn, ``stagger_s`` after the last one's:
        a window's opening wave is spread as it always was, and a spare
        woken just after another waits its turn too.

        Returns:
            The wait; ``0.0`` when the last login was long enough ago.
        """

        now = self._monotonic()
        turn = now if self._next_login is None else max(now, self._next_login)
        self._next_login = turn + self._stagger_s
        return turn - now

    def logged_in(self) -> None:
        """Count a login; the fleet's heartbeat reports the running total."""

        self._logins += 1

    def chasing(self, label: str) -> None:
        """A watcher has claimed a start and leaves the lobby for it.

        Called right after the claim, before anything awaits, so its place
        is refilled at once: the spare walks in, and the longest-waiting
        logged-out worker is woken to become the next spare.
        """

        self._opening = False
        self._assign(label, Role.CHASE, "chase")
        self._fill()

    def free_seekers(self) -> list[str]:
        """The seekers waiting on the online menu, in fleet order: a hunt's searchers."""

        return [label for label in self._live if self._roles[label] is Role.SEEK]

    def hunt(self, labels: Sequence[str]) -> None:
        """Send seekers after a start a watcher has just read.

        Each keeps its seek place while it hunts, so nothing is refilled and
        nobody logs in: the place is given up only by the hunt's winner, through
        :meth:`chasing`, exactly as a watcher gives up its own.

        Args:
            labels: The seekers sent, all of them holding :attr:`Role.SEEK`.
        """

        self._opening = False
        for label in labels:
            self._assign(label, Role.HUNT, "hunt")

    def seated(self, label: str) -> None:
        """A startup worker has taken its table, so the next one can go.

        Sent on the table's claim, not at the end of its game: a mid-game
        recording lasts until the game does, 8-18 minutes, and the phase is
        about the few minutes after the window opens.
        """

        if label != self._scanning:
            return
        self._scanning = None
        self._fill()

    def done(self, label: str) -> Role:
        """A chase, a hunt or a startup recording is over: the lobby, a seek place, the spare's, or out.

        A worker already logged in is worth more than one that would have to
        log in, so a place still open goes to it before anyone queued. A
        searcher called off comes back to the seek place it held. A startup
        worker that took no table ends the startup phase.

        Args:
            label: The worker coming back.

        Returns:
            The worker's new role.
        """

        if label == self._scanning:
            # Still scanning at its end, so :meth:`seated` never came: it took
            # no table. The running games are all taken, or too far along.
            self._scanning = None
            self._close("empty")
        watchers, seekers, spares = self._targets()
        if self._count(Role.HALL) < watchers:
            self._assign(label, Role.HALL, "returned")
        elif self._seeking(but=label) < seekers:
            self._assign(label, Role.SEEK, "returned")
        elif self._count(Role.SPARE) < spares:
            self._assign(label, Role.SPARE, "returned")
        else:
            self._assign(label, Role.OUT, "parked")
        self._fill()
        return self._roles[label]

    def vacate(self, label: str, why: str) -> None:
        """A worker gives its place up — a counted failure, a refused egress, an exit.

        Its place goes to the next in line at once, rather than waiting out
        the failed worker's idle poll. A worker already counted out is let be.
        A startup worker failing mid-scan ends the startup phase.

        Args:
            label: The worker leaving.
            why: What the ``role`` line says.
        """

        if label not in self._roles:
            return
        if label == self._scanning:
            self._scanning = None
            self._close("failed")
        if label in self._queue:
            self._queue.remove(label)
        self._assign(label, Role.OUT, why)
        self._fill()

    def drop(self, label: str) -> None:
        """Count a worker out for the rest of the process.

        The numbers are clamped again to the workers left. Called before the
        window opens as well, for a worker already down; then there is
        nothing to drop.
        """

        if label not in self._roles:
            return
        if label in self._queue:
            self._queue.remove(label)
        self._assign(label, Role.OUT, "down")
        self._live.remove(label)
        del self._roles[label]
        self._fill()

    def revive(self, label: str) -> None:
        """Bring a dropped worker back, logged out, at the back of the queue.

        The undoing of :meth:`drop`: the numbers are clamped again to the
        workers now up, so a revival can give back the spare's place a down
        worker took away. A worker already on the rota is let be.

        Args:
            label: The worker coming back.
        """

        if label in self._roles:
            return
        self._live.append(label)
        self._roles[label] = Role.OUT
        self._queue.append(label)
        self._wakes[label] = asyncio.Event()
        # Not through _assign: a dropped worker has no role to change from.
        self._health.event("role", worker=label, was="down", now=Role.OUT.value,
                           why="revived")
        self._fill()

    def counts(self) -> dict[str, int]:
        """How many workers hold each role, and the logins so far, for the heartbeat."""

        return {role.value: self._count(role) for role in Role} | {
            "logins": self._logins
        }

    # -- the one rule --------------------------------------------------------

    def _fill(self) -> None:
        """Fill the lobby, send the next startup worker, then the seek places and the spare's.

        Nobody is woken once the fleet is stopping: a worker logging in then
        would only log out again.
        """

        if self._stopping():
            self._close("stopping")
            return
        watchers, seekers, spares = self._targets()
        # One watcher while the startup phase runs, the rest once it is over.
        self._fill_lobby(min(1, watchers) if self._phase else watchers)
        if self._phase and self._scanning is None:
            # Send another only while the workers queued outnumber the places
            # still empty once the phase is over: the lobby's second watcher,
            # the seekers and the spare must not wait out a mid-game recording.
            empty = (
                (watchers - self._count(Role.HALL))
                + (seekers - self._seeking())
                + (spares - self._count(Role.SPARE))
            )
            if len(self._queue) > empty:
                label = self._queue.popleft()
                if not self._sent:
                    self._health.event("bootstrap_started")
                self._sent += 1
                self._scanning = label
                self._assign(label, Role.BOOT, "bootstrap")
            else:
                self._close("exhausted")
        if not self._phase:
            self._fill_lobby(watchers)
            while self._seeking() < seekers:
                spare = self._first(Role.SPARE)
                if spare is not None:
                    # Spares and seekers wait on the same menu: no walk at all.
                    self._assign(spare, Role.SEEK, "promoted")
                elif self._queue:
                    self._assign(self._queue.popleft(), Role.SEEK, "seeker_wanted")
                else:
                    break
            while self._count(Role.SPARE) < spares and self._queue:
                self._assign(self._queue.popleft(), Role.SPARE, "spare_wanted")

    def _fill_lobby(self, watchers: int) -> None:
        """Bring the lobby up to ``watchers``: the spare, an idle seeker, then the queue.

        A hunting seeker is never taken: it is out after a start already.
        """

        while self._count(Role.HALL) < watchers:
            standing = self._first(Role.SPARE) or self._first(Role.SEEK)
            if standing is not None:
                self._assign(standing, Role.HALL, "promoted")
            elif self._queue:
                self._assign(self._queue.popleft(), Role.HALL, "watcher_wanted")
            else:
                break

    def _first(self, role: Role) -> str | None:
        """The first worker in fleet order holding one role, if any does."""

        return next((label for label in self._live if self._roles[label] is role), None)

    def _seeking(self, *, but: str | None = None) -> int:
        """How many seek places are held, hunting or not, leaving ``but`` out."""

        return sum(
            1 for label, role in self._roles.items()
            if role in (Role.SEEK, Role.HUNT) and label != but
        )

    def _close(self, reason: str) -> None:
        """End the window's startup phase, saying why and how many were sent."""

        if not self._phase:
            return
        self._phase = False
        self._health.event("bootstrap_done", reason=reason, sent=self._sent)

    def _targets(self) -> tuple[int, int, int]:
        """The watchers, seekers and spares wanted, clamped in that order to the workers up."""

        live = len(self._live)
        watchers = min(self._watchers, live)
        seekers = min(self._seekers, live - watchers)
        return watchers, seekers, min(self._spares, live - watchers - seekers)

    def _count(self, role: Role) -> int:
        """How many workers hold one role."""

        return sum(1 for held in self._roles.values() if held is role)

    def _assign(self, label: str, role: Role, why: str) -> None:
        """Give a worker a role, say so, and wake it if it is waiting."""

        was = self._roles[label]
        if was is role:
            return
        self._roles[label] = role
        self._health.event("role", worker=label, was=was.value, now=role.value, why=why)
        self._wakes[label].set()
