"""Player subpackage — public API re-exports.

Re-exports the subpackage's public names (players, strategies, and the
AI-level registry) so callers import them directly from
``contrai_engine.model.player`` without knowing the internal module
layout.
"""

from .ai import AiPlayer
from .base import HumanPlayer, Player
from .levels import AI_LEVELS, make_ai_player
from .rationale import BidDecision, CardDecision, Rationale, RuleCitation
from .rule_based import RuleBasedBiddingStrategy, RuleBasedCardPlayStrategy
from .strategy import BiddingStrategy, CardPlayStrategy

__all__ = [
    "Player",
    "HumanPlayer",
    "AiPlayer",
    "BiddingStrategy",
    "CardPlayStrategy",
    "RuleBasedBiddingStrategy",
    "RuleBasedCardPlayStrategy",
    "BidDecision",
    "CardDecision",
    "Rationale",
    "RuleCitation",
    "AI_LEVELS",
    "make_ai_player",
]
