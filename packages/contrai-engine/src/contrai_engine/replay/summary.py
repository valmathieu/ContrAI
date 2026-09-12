"""One row per recorded round, for a screen that lets you pick one.

A record's rounds and a verdict's rounds are two lists that describe the
same thing and are joined **by the record's own round number**, which
both carry. Joining by position would be wrong for the reason
:meth:`~contrai_engine.replay.verify.VerifyingObserver.expect` spells out:
a record's numbers are its source's deal count, which may start above one
and may skip, so one incomplete round in the middle puts a positional
join permanently off by one with nothing looking wrong.

Plain data over ``contrai-data`` and ``replay`` values — no Rich, no
console — so the picker's panel stays the throwaway half and this stays
the stable one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from .verdict import Verdict

if TYPE_CHECKING:
    from contrai_core.team_side import TeamSide
    from contrai_data import GameRecord
    from contrai_data.events import RoundOutcome
    from contrai_core.contract import ObservedContract

    from .verdict import GameVerdict


@dataclass(frozen=True, slots=True)
class ReplayRow:
    """One recorded round as the picker shows it.

    Attributes:
        number: The record's own round number — what the viewer types to
            step it.
        contract: The contract the auction settled on, or ``None`` for a
            passed-out round.
        outcome: How the round resolved, or ``None`` when no source
            covered it — which is the common case in an observed game,
            where a spectator sees the cards long before a score sheet.
        totals: The running totals after the round, when the record holds
            a score line for it; ``None`` otherwise.
        verdict: What verification made of the round, or ``None`` when it
            was not verified.
        steppable: Whether the round can be replayed at all. A round
            whose auction never closed, or whose eighth trick was never
            seen, cannot be driven to the end by any set of actions.
    """

    number: int
    contract: Optional["ObservedContract"]
    outcome: Optional["RoundOutcome"]
    totals: Optional[Mapping["TeamSide", int]]
    verdict: Optional[Verdict]
    steppable: bool

    @property
    def declarer_side(self) -> Optional["TeamSide"]:
        """The side that took the contract, or ``None`` if it was passed out."""

        if self.contract is None:
            return None
        return self.contract.declarer.team_side


def replay_rows(
    record: "GameRecord", verdict: Optional["GameVerdict"] = None
) -> tuple[ReplayRow, ...]:
    """One row per round of ``record``, in file order.

    Every round is listed, steppable or not: a viewer looking for the
    round that went wrong needs to see the one the driver refuses as much
    as the ones it accepts.

    Args:
        record: The record to summarise.
        verdict: Its verification result, if one was computed. Rounds it
            does not mention carry no verdict.

    Returns:
        The rows, in the record's own order.
    """

    verdicts = {
        round_.number: round_.verdict
        for round_ in (verdict.rounds if verdict is not None else ())
    }
    return tuple(
        ReplayRow(
            number=round_.number,
            contract=round_.contract,
            outcome=round_.outcome,
            totals=round_.score.totals if round_.score is not None else None,
            verdict=verdicts.get(round_.number),
            steppable=round_.complete,
        )
        for round_ in record.rounds
    )
