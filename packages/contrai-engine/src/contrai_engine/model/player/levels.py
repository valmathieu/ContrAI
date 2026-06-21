# AI difficulty levels — registry + factory over the strategy seam.
#
# A thin convenience layer mapping a human-readable level name to a
# (bidding, card-play) strategy pair. This is the seam for a future CLI
# ``--difficulty`` flag, a web-app difficulty picker, and eval-by-name
# match protocols (AI roadmap §6). The raw
# ``AiPlayer(..., bidding=…, cardplay=…)`` form stays available for
# mix-and-match (e.g. rule-based bidding + a learned card-play).

from .ai import AiPlayer
from .rule_based import RuleBasedBiddingStrategy, RuleBasedCardPlayStrategy

AI_LEVELS = {
    "expert": (RuleBasedBiddingStrategy, RuleBasedCardPlayStrategy),
    # future: "mcts": (MctsBiddingStrategy, MctsCardPlayStrategy), ...
}


def make_ai_player(name, position, level="expert"):
    """Build an :class:`AiPlayer` wired to a named difficulty level.

    Args:
        name: Display name.
        position: Seat position (``'North'`` / ``'South'`` / …).
        level: A key of :data:`AI_LEVELS`. Defaults to ``"expert"``.

    Returns:
        An :class:`AiPlayer` whose ``bidding`` / ``cardplay`` are the
        strategy pair registered for ``level``.

    Raises:
        KeyError: If ``level`` is not a registered level.
    """

    bidding_cls, cardplay_cls = AI_LEVELS[level]
    return AiPlayer(name, position, bidding=bidding_cls, cardplay=cardplay_cls)
