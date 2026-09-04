"""The ContrAI game-record format.

Public API — consumers write ``from contrai_data import RecordWriter,
load_game, …`` without knowing the internal module layout.
"""

from .exceptions import (
    RecordError,
    RecordFormatError,
    UnsupportedFormatError,
)
from .events import (
    RecordSource,
    SeatKind,
    JoinPhase,
    HandsDerivation,
    RoundOutcome,
    SlamOutcome,
    ScoreSource,
    EndReason,
    Seat,
    ObservedFrom,
    Ruleset,
    SideMark,
    ContractTerms,
    Header,
    GameStarted,
    RoundDealt,
    BidMade,
    CardPlayed,
    BeloteHeld,
    RoundScored,
    GameEnded,
    GameEvent,
)

__all__: list[str] = [
    "RecordError",
    "RecordFormatError",
    "UnsupportedFormatError",
    "RecordSource",
    "SeatKind",
    "JoinPhase",
    "HandsDerivation",
    "RoundOutcome",
    "SlamOutcome",
    "ScoreSource",
    "EndReason",
    "Seat",
    "ObservedFrom",
    "Ruleset",
    "SideMark",
    "ContractTerms",
    "Header",
    "GameStarted",
    "RoundDealt",
    "BidMade",
    "CardPlayed",
    "BeloteHeld",
    "RoundScored",
    "GameEnded",
    "GameEvent",
]
