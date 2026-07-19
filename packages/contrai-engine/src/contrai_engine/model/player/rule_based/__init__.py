"""Rule-based AI strategies.

These are the *first* concrete rung of the AI ladder (AI roadmap §6),
registered as ``AI_LEVELS["expert"]`` (see :mod:`.levels`). They are the
logic that used to live inline on ``AiPlayer``; injecting them behind the
:mod:`.strategy` interfaces means future levels are new classes, not
edits to ``AiPlayer``.

The two strategy families live in sibling modules — :mod:`.bidding` for
the auction policy, :mod:`.card_play` for trick play — and are
re-exported here so consumers keep importing from ``rule_based``.
"""

from .bidding import RuleBasedBiddingStrategy
from .card_play import RuleBasedCardPlayStrategy

__all__ = ["RuleBasedBiddingStrategy", "RuleBasedCardPlayStrategy"]
