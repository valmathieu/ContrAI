# Player subpackage — public API re-exports.
#
# The single ``player.py`` module was split into a ``player/`` subpackage
# (base classes, the pluggable strategies, the AiPlayer that injects
# them, and the AI-level registry). This ``__init__`` re-exports the
# historical public names so external imports
# (``from contrai_engine.model.player import Player, AiPlayer, …``) keep
# working byte-for-byte, plus the strategy seam.

from .ai import AiPlayer
from .base import HumanPlayer, Player
from .levels import AI_LEVELS, make_ai_player
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
    "AI_LEVELS",
    "make_ai_player",
]
