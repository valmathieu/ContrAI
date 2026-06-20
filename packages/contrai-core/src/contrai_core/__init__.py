"""Shared domain model for the ContrAI monorepo.

Public API — consumers can ``from contrai_core import Card, Suit, Rank, …``
without knowing the internal module layout.
"""

from .types import Suit, Rank, CARD_SUITS
from .card import Card
from .deck import Deck
from .hand import Hand
from .team import Team
from .player import BasePlayer
from .bid import Bid, PassBid, ContractBid, DoubleBid, RedoubleBid, SlamLevel
from .auction import Auction
from .contract import Contract
from .trick import Trick
from .exceptions import (
    ContraiError,
    InvalidPlayerCountError,
    InvalidCardCountError,
    IllegalBidError,
    PlayRuleViolation,
    IllegalPlayError,
    TrickStateError,
    InvalidContractError,
)

__all__ = [
    "Suit",
    "Rank",
    "CARD_SUITS",
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
    "ContraiError",
    "InvalidPlayerCountError",
    "InvalidCardCountError",
    "IllegalBidError",
    "PlayRuleViolation",
    "IllegalPlayError",
    "TrickStateError",
    "InvalidContractError",
]
