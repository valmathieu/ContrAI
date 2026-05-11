"""BasePlayer: pure data class for player identity.

Shared across ContrAI packages. Game-flow concerns like ``choose_bid()`` and
``choose_card()`` live in engine-side subclasses (see
``contrai_engine.model.player.Player``).
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .hand import Hand

if TYPE_CHECKING:
    from .team import Team


class BasePlayer:
    """A player's identity and table state.

    Attributes:
        name: The player's display name.
        position: Table position ('North', 'South', 'East', 'West').
        hand: Cards currently held (a :class:`Hand` instance).
        team: The team this player belongs to (assigned by Game).
    """

    def __init__(self, name: str, position: str):
        self.name = name
        self.position = position
        self.hand: Hand = Hand()
        self.team: Team | None = None
