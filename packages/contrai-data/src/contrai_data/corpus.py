"""A corpus: every game ever scraped, one record each, rebuilt from raw logs.

One game can reach the corpus more than once. The box and a fleet worker
may watch the same table, and a single session may leave a table and be
seated back at it — each raw log that saw the game yields its own record,
and the copies differ by how much of the game each one saw. The corpus
keeps exactly one, and this module decides which.

**The ranking is fixed, and it is lexicographic.** A copy that holds more
*scored* rounds wins outright, because a scored round is what verification
and training both consume; only on a tie does the plain round count
matter, then whether the copy closes on a ``game_ended`` carrying totals,
then the earlier join (a lower first round saw more of the opening). What
is left is a true tie between copies worth the same, broken on the source
label and then the origin path so that two builds over the same raw logs
always keep the same file.

**Every loser says why it lost.** The reason names the first tier on
which it fell behind the winner, with both values, so a build report can
be audited without re-running the ranking.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .events import GameEvent
from .projection import GameRecord, project


@dataclass(frozen=True, slots=True)
class RecordCopy:
    """One record of a game, as one raw log produced it.

    Attributes:
        source: The label of the machine or run the raw log came from,
            e.g. ``box`` or ``laptop``.
        origin: Where the copy came from, for the report — typically the
            raw log's path relative to the corpus root.
        events: The record's events, header first.
        game: The same events folded into rounds.
    """

    source: str
    origin: str
    events: tuple[GameEvent, ...]
    game: GameRecord

    @classmethod
    def of(cls, source: str, origin: str, events: Sequence[GameEvent]) -> RecordCopy:
        """Build a copy from its events, folding them once.

        Args:
            source: The source label.
            origin: Where the copy came from.
            events: The record's events, header first.

        Returns:
            The copy.

        Raises:
            RecordFormatError: If the events cannot be folded into rounds.
        """

        events = tuple(events)
        return cls(source=source, origin=origin, events=events, game=project(events))

    @property
    def game_id(self) -> str:
        """The game this copy is a record of."""

        return self.game.header.game_id

    @property
    def scored_rounds(self) -> int:
        """How many rounds carry a score line."""

        return sum(1 for round_ in self.game.rounds if round_.score is not None)

    @property
    def rounds(self) -> int:
        """How many rounds were dealt."""

        return len(self.game.rounds)

    @property
    def ended_with_totals(self) -> bool:
        """Whether the copy closes on a ``game_ended`` that states totals."""

        ended = self.game.ended
        return ended is not None and ended.totals is not None

    @property
    def first_round(self) -> int | None:
        """The first round the copy holds, or ``None`` when it holds none."""

        return self.game.rounds[0].number if self.game.rounds else None


@dataclass(frozen=True, slots=True)
class Rejection:
    """A copy that lost, and the first count it lost on.

    Attributes:
        copy: The losing copy.
        reason: One line naming the tier and both values, e.g.
            ``fewer scored rounds (3 against 5)``.
    """

    copy: RecordCopy
    reason: str


@dataclass(frozen=True, slots=True)
class CopyChoice:
    """The copy a corpus keeps for one game, and the ones it does not.

    Attributes:
        chosen: The copy kept.
        rejected: Every other candidate, in ranking order.
    """

    chosen: RecordCopy
    rejected: tuple[Rejection, ...]

    @property
    def game_id(self) -> str:
        """The game chosen for."""

        return self.chosen.game_id


def _rank(copy: RecordCopy) -> tuple[int, int, bool, float, str, str]:
    """A copy's sort key: smaller is better.

    Each "more is better" count is negated so a single ascending sort
    ranks every tier the right way round. A copy with no round at all
    joins "at infinity", behind any copy that holds one.
    """

    first = copy.first_round
    return (
        -copy.scored_rounds,
        -copy.rounds,
        not copy.ended_with_totals,
        math.inf if first is None else first,
        copy.source,
        copy.origin,
    )


def _reason(loser: RecordCopy, winner: RecordCopy) -> str:
    """The first tier on which ``loser`` fell behind ``winner``.

    Args:
        loser: A copy ranked after ``winner``.
        winner: The chosen copy.

    Returns:
        One line naming the tier and, where there is one, both values.
    """

    if loser.scored_rounds != winner.scored_rounds:
        return (
            f"fewer scored rounds ({loser.scored_rounds} against "
            f"{winner.scored_rounds})"
        )
    if loser.rounds != winner.rounds:
        return f"fewer rounds ({loser.rounds} against {winner.rounds})"
    if loser.ended_with_totals != winner.ended_with_totals:
        return "no game_ended with totals"
    if loser.first_round != winner.first_round:
        return (
            f"joined later (round {loser.first_round} against round "
            f"{winner.first_round})"
        )
    if loser.source != winner.source:
        return f"a tie, kept the first source ({winner.source} before {loser.source})"
    return "a tie, kept the first origin in sorted order"


def choose_copy(candidates: Sequence[RecordCopy]) -> CopyChoice:
    """Keep the most complete of several records of one game.

    Args:
        candidates: At least one copy, all of the same game. Their order
            does not matter: the ranking is total, so any order yields
            the same choice.

    Returns:
        The chosen copy and, for every other, why it lost.

    Raises:
        ValueError: If ``candidates`` is empty or mixes games.
    """

    if not candidates:
        raise ValueError("choose_copy needs at least one candidate")
    games = {copy.game_id for copy in candidates}
    if len(games) > 1:
        raise ValueError(f"The candidates are records of several games: {sorted(games)}")
    ranked = sorted(candidates, key=_rank)
    winner = ranked[0]
    return CopyChoice(
        chosen=winner,
        rejected=tuple(Rejection(copy, _reason(copy, winner)) for copy in ranked[1:]),
    )
