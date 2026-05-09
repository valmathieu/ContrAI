"""Shared domain model for the ContrAI monorepo.

Public API — consumers can ``from contrai_core import Card, Suit, Rank, …``
without knowing the internal module layout.
"""

from .types import Suit, Rank, CARD_SUITS
from .card import Card
from .deck import Deck
from .team import Team
from .player import BasePlayer
from .bid import Bid, PassBid, ContractBid, DoubleBid, RedoubleBid, BidValidator
from .contract import Contract
from .trick import Trick
from .exceptions import InvalidPlayerCountError, InvalidCardCountError

__all__ = [
    "Suit",
    "Rank",
    "CARD_SUITS",
    "Card",
    "Deck",
    "Team",
    "BasePlayer",
    "Bid",
    "PassBid",
    "ContractBid",
    "DoubleBid",
    "RedoubleBid",
    "BidValidator",
    "Contract",
    "Trick",
    "InvalidPlayerCountError",
    "InvalidCardCountError",
]
