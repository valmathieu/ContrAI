"""Shared fixtures for the view-layer tests.

The ``rich_view`` module was split into focused sub-modules
(``theme`` / ``formatting`` / ``parsing`` / ``bidding_rules`` /
``state_helpers`` / ``layout`` / ``screens``), and the test suite mirrors
that split into one file per module. The ``four_players`` quartet is used
across several of them, so it lives here.
"""

from __future__ import annotations

import pytest

from contrai_engine.model.player import AiPlayer
from contrai_core.team import Team


@pytest.fixture
def four_players():
    """A North/East/South/West quartet wired into N-S and E-W teams."""
    north = AiPlayer("North", "North")
    east = AiPlayer("East", "East")
    south = AiPlayer("South", "South")
    west = AiPlayer("West", "West")
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    north.team = south.team = ns
    east.team = west.team = ew
    return north, east, south, west
