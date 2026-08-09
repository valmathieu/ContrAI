"""BasePlayer: pure data class for player identity.

Shared across ContrAI packages. Game-flow concerns like ``choose_bid()`` and
``choose_card()`` live in engine-side subclasses (see
``contrai_engine.model.player.Player``).
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .hand import Hand
from .position import Position

if TYPE_CHECKING:
    from .team import Team


class BasePlayer:
    """A player's identity and table state.

    The seat is optional at construction. A player built without one is
    *unseated*: it has an identity but no place at a table yet, which is
    the natural state of a roster assembled before anyone sits down —
    the engine's ``Game`` seats such a list itself. Seating is what fills
    the attribute in, and every table rule (turn order, partnership,
    trick winners) reads it afterwards, so a player still holding
    ``None`` has simply not been dealt into a game.

    Attributes:
        name: The player's display name.
        position: The seat this player occupies, or ``None`` while
            unseated.
        hand: Cards currently held (a :class:`Hand` instance).
        team: The team this player belongs to (assigned by Game).
    """

    def __init__(self, name: str, position: Position | None = None):
        self.name = name
        self.position = position
        self.hand: Hand = Hand()
        self.team: Team | None = None
