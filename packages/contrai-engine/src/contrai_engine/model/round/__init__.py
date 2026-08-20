"""Round subpackage — public API re-exports.

The single ``round.py`` module was split into a ``round/`` subpackage:
the lifecycle orchestrator, the near-pure scoring transformation it
calls, and the §7.2 component arithmetic that transformation delegates
to. This ``__init__`` re-exports the public names so external imports
(``from contrai_engine.model.round import Round, UnannouncedSlam``) keep
working byte-for-byte.
"""

from .components import Mark, contract_components, marked_total
from .round import Round
from .scoring import RoundScore, UnannouncedSlam

__all__ = [
    "Mark",
    "Round",
    "RoundScore",
    "UnannouncedSlam",
    "contract_components",
    "marked_total",
]
