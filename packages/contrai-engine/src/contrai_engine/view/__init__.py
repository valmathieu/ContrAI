"""CLI View: user interactions.

The view layer is split into focused modules — design tokens
(:mod:`theme`), stateless formatters (:mod:`formatting`), input parsers
(:mod:`parsing`), auction-legality helpers (:mod:`bidding_rules`),
game-state readers (:mod:`state_helpers`), shared layout
(:mod:`layout`), and per-screen rendering (:mod:`screens`). The stateful
orchestrator :class:`~contrai_engine.view.rich_view.RichView` ties them
together and owns all terminal I/O.

``RichView`` (and the :class:`~contrai_engine.view.rich_view.RoundSummary`
row it tracks) are re-exported here so callers can ``from
contrai_engine.view import RichView``.
"""

from contrai_engine.view.rich_view import RichView, RoundSummary

__all__ = ["RichView", "RoundSummary"]
