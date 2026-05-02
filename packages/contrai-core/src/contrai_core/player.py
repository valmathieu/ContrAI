"""BasePlayer: pure data class for player identity.

Shared across ContrAI packages. Game-flow concerns like ``choose_bid()`` and
``choose_card()`` live in engine-side subclasses (see
``contrai_engine.model.player.Player``).
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .team import Team


class BasePlayer:
    """A player's identity and table state.

    Attributes:
        name: The player's display name.
        position: Table position ('North', 'South', 'East', 'West').
        hand: Cards currently held (list of Card objects).
        team: The team this player belongs to (assigned by Game).
    """

    def __init__(self, name: str, position: str):
        self.name = name
        self.position = position
        self.hand: list = []
        self.team: Team | None = None
