"""Replay a recorded game through the real engine.

Reading a record back is how the engine proves one describes a game that
could actually have happened. The driver does it by **replaying**, not by
re-checking: four :class:`~contrai_engine.replay.player.RecordedPlayer`
seats hand the recorded actions to the real
:class:`~contrai_engine.model.game.Game`, under the record's own
``RuleConfig`` and its own deal, and every core invariant fires exactly
as it does in a live game. There is no second implementation of the rules
to drift from the first.

Public API — consumers write ``from contrai_engine.replay import
ReplayController, verify_record, …`` without knowing the module layout.
"""

from ..model.deal import DealSource, RandomDealSource
from .controller import ReplayController
from .deal import ScriptedDealSource
from .exceptions import (
    ReplayError,
    RoundExhaustedError,
    ScriptExhaustedError,
    SeatMismatchError,
)
from .player import RecordedPlayer, RoundScript
from .stepping import (
    ReplayInterrupt,
    StepMode,
    SteppingView,
    read_step_key,
)
from .summary import ReplayRow, replay_rows
# The verdict model lives in ``contrai-data`` — a corpus catalog reads
# verdict files without importing the engine — and is re-exported here so
# ``from contrai_engine.replay import Verdict`` keeps working.
from contrai_data import (
    GameVerdict,
    Mismatch,
    MismatchKind,
    RoundVerdict,
    Verdict,
    verdict_path,
    verdicts_dir,
    write_verdict,
)
from .verify import (
    VerifyingObserver,
    default_out_root,
    verify_game,
    verify_record,
)

__all__ = [
    "DealSource",
    "RandomDealSource",
    "ScriptedDealSource",
    "RecordedPlayer",
    "RoundScript",
    "ReplayController",
    "ReplayError",
    "RoundExhaustedError",
    "ScriptExhaustedError",
    "SeatMismatchError",
    "ReplayInterrupt",
    "StepMode",
    "SteppingView",
    "read_step_key",
    "ReplayRow",
    "replay_rows",
    "Verdict",
    "MismatchKind",
    "Mismatch",
    "RoundVerdict",
    "GameVerdict",
    "verdicts_dir",
    "verdict_path",
    "write_verdict",
    "VerifyingObserver",
    "verify_game",
    "verify_record",
    "default_out_root",
]
