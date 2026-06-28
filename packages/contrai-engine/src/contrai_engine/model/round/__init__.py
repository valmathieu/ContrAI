# Round subpackage — public API re-exports.
#
# The single ``round.py`` module was split into a ``round/`` subpackage
# (the lifecycle orchestrator plus the two near-pure transformations it
# calls — scoring and card-legality). This ``__init__`` re-exports the
# historical public names so external imports
# (``from contrai_engine.model.round import Round, UnannouncedSlam``) keep
# working byte-for-byte.

from .round import Round, UnannouncedSlam

__all__ = ["Round", "UnannouncedSlam"]
