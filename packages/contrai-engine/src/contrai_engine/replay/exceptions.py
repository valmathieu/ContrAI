"""Errors the replay driver raises.

All of them mean the same thing: the record and the engine stopped
agreeing about what game is being played. A verifier catches them and
turns them into a mismatch; a caller driving a replay by hand sees them
as the exception they are.

They are *not* record-format errors. ``contrai-data`` already refused
anything malformed before the driver saw it, so by the time these fire
the file is well-formed and merely describes something that could not
have happened — a distinction ``contrai verify`` exists to report.
"""

from __future__ import annotations

from contrai_core.exceptions import ContraiError


class ReplayError(ContraiError, ValueError):
    """Base class for every replay-driver error."""


class ScriptExhaustedError(ReplayError):
    """A seat was asked for an action the record does not hold.

    The engine consulted a seat more times than the record has actions
    for it — which means the replayed auction or trick ran longer than
    the recorded one. That is a genuine divergence, not a missing line:
    the record's own structure was checked when it was projected.
    """


class SeatMismatchError(ReplayError):
    """A seat was handed an action recorded for a different seat.

    Only reachable if the engine's turn order and the record's disagree,
    which is exactly the sort of silent corruption a replay exists to
    surface.
    """


class RoundExhaustedError(ReplayError):
    """The game started a round the record does not hold.

    Raised by :class:`~contrai_engine.replay.deal.ScriptedDealSource`
    when it is asked to deal past the end of its script — a controller
    bug rather than a record defect, since the controller decides how
    many rounds to run.
    """
