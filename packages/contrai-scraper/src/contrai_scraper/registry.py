"""Who is chasing which game, and who is seated where — shared by a fleet.

A fleet's workers cannot choose their tables: the server seats a spectator
wherever it likes, so two workers are routinely handed the same one. That is
worse than wasteful. Both would write ``games/obs-<game_id>.jsonl``, and a
record is opened for *appending*, so the two streams would interleave into one
file that is well formed and wrong. The registry is what stops the second
worker at the gate.

**Two keys, two jobs, and they are not unified.** The lobby says who is about
to play and nothing about where, so a chase is claimed by its *roster* — the
four accounts. A seated table says where and carries its own id on the wire,
so exclusion is claimed by *table id*. A chase that finds its table moves from
the first kind of claim to the second.

**Atomic without a lock.** Every worker runs in one event loop, and a task
only yields at an ``await``; none of the methods here awaits anything, so a
check-and-set cannot be interleaved with another. A lock would guard against
nothing — and an ``async`` surface would invite the one change that breaks
this, an ``await`` between the check and the set.

**Claims expire.** Workers release what they hold as they leave, which is the
mechanism. The time-to-live is only the backstop for a worker that wedged
while holding something; a worker watching a table keeps its claim fresh.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Claim:
    """One worker's hold on a roster or a table."""

    worker: str
    at: float
    """When the claim was taken or last refreshed, on the registry's clock."""


@dataclass(frozen=True, slots=True)
class Sighting:
    """What was last seen of one table."""

    table_id: str
    is_tournament: bool | None
    round_index: int | None
    """The last completed round when it was seen; ``None`` before the first."""

    at: float


class TableRegistry:
    """The fleet's claims on rosters and tables, and its census of tables seen."""

    __slots__ = ("_ttl_s", "_monotonic", "_roster_claims", "_table_claims", "_census")

    def __init__(
        self, *, claim_ttl_s: float, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        """An empty registry.

        Args:
            claim_ttl_s: How long an unrefreshed claim holds against others.
            monotonic: The clock claims are aged on.
        """

        self._ttl_s = claim_ttl_s
        self._monotonic = monotonic
        self._roster_claims: dict[frozenset[str], Claim] = {}
        self._table_claims: dict[str, Claim] = {}
        self._census: dict[str, Sighting] = {}

    # -- rosters: a chase target ---------------------------------------------

    def claim_roster(self, accounts: frozenset[str], worker: str) -> bool:
        """Take a roster to chase, unless another worker already is.

        Args:
            accounts: The roster's four accounts.
            worker: The claiming worker's label.

        Returns:
            Whether this worker holds it now — refreshed, if it already did.
        """

        return self._claim(self._roster_claims, accounts, worker)

    def release_roster(self, accounts: frozenset[str], worker: str) -> None:
        """Give a roster up, if this worker still holds it."""

        self._release(self._roster_claims, accounts, worker)

    # -- tables: exclusion ---------------------------------------------------

    def claim_table(self, table_id: str, worker: str) -> bool:
        """Take a table to watch, unless another worker is watching it.

        Args:
            table_id: The table's own id, as its join snapshot gave it.
            worker: The claiming worker's label.

        Returns:
            Whether this worker holds it now — refreshed, if it already did.
        """

        return self._claim(self._table_claims, table_id, worker)

    def release_table(self, table_id: str, worker: str) -> None:
        """Give a table up, if this worker still holds it."""

        self._release(self._table_claims, table_id, worker)

    def table_holder(self, table_id: str) -> str | None:
        """The worker a table's live claim belongs to, if any."""

        claim = self._live(self._table_claims, table_id)
        return None if claim is None else claim.worker

    # -- the census ----------------------------------------------------------

    def note_seen(
        self, table_id: str, *, is_tournament: bool | None, round_index: int | None
    ) -> None:
        """Record a table as seen now, replacing what was known of it."""

        self._census[table_id] = Sighting(
            table_id=table_id,
            is_tournament=is_tournament,
            round_index=round_index,
            at=self._monotonic(),
        )

    def census(self) -> Mapping[str, Sighting]:
        """Every table seen so far, by id, as it was last seen."""

        return dict(self._census)

    def for_worker(self, worker: str) -> WorkerClaims:
        """The registry as one worker's recorder uses it."""

        return WorkerClaims(self, worker)

    # -- the one rule ----------------------------------------------------------

    def _live(self, claims: dict[Any, Claim], key: Hashable) -> Claim | None:
        """A claim that still holds against others, or ``None``."""

        claim = claims.get(key)
        if claim is None or self._monotonic() - claim.at >= self._ttl_s:
            return None
        return claim

    def _claim(self, claims: dict[Any, Claim], key: Hashable, worker: str) -> bool:
        """Grant a claim if nobody else holds it live. No ``await``: atomic."""

        holder = self._live(claims, key)
        if holder is not None and holder.worker != worker:
            return False
        claims[key] = Claim(worker=worker, at=self._monotonic())
        return True

    def _release(self, claims: dict[Any, Claim], key: Hashable, worker: str) -> None:
        """Drop a claim this worker holds, and leave anyone else's alone.

        A worker whose claim expired and was taken over must not free the new
        holder's by releasing what it thinks is still its own.
        """

        claim = claims.get(key)
        if claim is not None and claim.worker == worker:
            del claims[key]


class WorkerClaims:
    """One worker's view of the registry: at most one table held at a time."""

    __slots__ = ("_registry", "_worker", "_table")

    def __init__(self, registry: TableRegistry, worker: str) -> None:
        """Bind a worker to the registry.

        Args:
            registry: The fleet's registry.
            worker: The worker's label.
        """

        self._registry = registry
        self._worker = worker
        self._table: str | None = None

    @property
    def worker(self) -> str:
        """The label this view claims under."""

        return self._worker

    def claim(self, table_id: str | None) -> bool:
        """Take the table just seated at, giving up any other first.

        Args:
            table_id: The table's id; ``None`` when its snapshot named none,
                which no claim can protect, so it is let through.

        Returns:
            Whether the worker may watch it.
        """

        if table_id is None:
            return True
        if table_id != self._table:
            self.release()
        if not self._registry.claim_table(table_id, self._worker):
            return False
        self._table = table_id
        return True

    def holder(self, table_id: str | None) -> str | None:
        """Who holds a table, for the line that says why it was refused."""

        return None if table_id is None else self._registry.table_holder(table_id)

    def hold(self) -> None:
        """Refresh the held table's claim, so its time-to-live never runs out."""

        if self._table is not None:
            self._registry.claim_table(self._table, self._worker)

    def release(self) -> None:
        """Give up the held table, if any."""

        if self._table is not None:
            self._registry.release_table(self._table, self._worker)
            self._table = None

    def seen(
        self, table_id: str | None, *, is_tournament: bool | None, round_index: int | None
    ) -> None:
        """Add a table this worker judged to the fleet's census."""

        if table_id is not None:
            self._registry.note_seen(
                table_id, is_tournament=is_tournament, round_index=round_index
            )
