"""Shared domain model for the ContrAI monorepo.

Public API — consumers can ``from contrai_core import Card, Suit, Rank, …``
without knowing the internal module layout.
"""

from .types import (
    Suit,
    TrumpVariant,
    ContractSuit,
    Rank,
    CONTRACT_SUITS,
    is_trump,
    trump_suits,
)
from .position import Position
from .card import Card
from .deck import Deck
from .hand import Hand
from .team import Team
from .player import BasePlayer
from .bid import Bid, PassBid, ContractBid, DoubleBid, RedoubleBid, SlamLevel
from .auction import Auction
from .contract import Contract
from .trick import Trick
from .play import Play, ObservedPlay, PlayState, PlayObservation
from .exceptions import (
    ContraiError,
    InvalidPlayerCountError,
    InvalidCardCountError,
    InvalidCardError,
    IllegalBidError,
    PlayRuleViolation,
    IllegalPlayError,
    TrickStateError,
    InvalidContractError,
)

__all__ = [
    "Suit",
    "TrumpVariant",
    "ContractSuit",
    "Rank",
    "CONTRACT_SUITS",
    "is_trump",
    "trump_suits",
    "Position",
    "Card",
    "Deck",
    "Hand",
    "Team",
    "BasePlayer",
    "Bid",
    "PassBid",
    "ContractBid",
    "DoubleBid",
    "RedoubleBid",
    "SlamLevel",
    "Auction",
    "Contract",
    "Trick",
    "Play",
    "ObservedPlay",
    "PlayState",
    "PlayObservation",
    "ContraiError",
    "InvalidPlayerCountError",
    "InvalidCardCountError",
    "InvalidCardError",
    "IllegalBidError",
    "PlayRuleViolation",
    "IllegalPlayError",
    "TrickStateError",
    "InvalidContractError",
]
