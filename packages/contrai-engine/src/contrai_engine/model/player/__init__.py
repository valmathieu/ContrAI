# Player subpackage — public API re-exports.
#
# The single ``player.py`` module was split into a ``player/`` subpackage
# (base classes, the wire-format bridge, and the AiPlayer). This
# ``__init__`` re-exports the historical public names so external imports
# (``from contrai_engine.model.player import Player, AiPlayer, …``) keep
# working byte-for-byte.

from .ai import AiPlayer
from .base import HumanPlayer, Player
from .wire import bid_to_wire, wire_to_bid

__all__ = [
    "Player",
    "HumanPlayer",
    "AiPlayer",
    "wire_to_bid",
    "bid_to_wire",
]
