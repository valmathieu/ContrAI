"""Shared fixtures for the model-layer round tests.

The ``round/`` subpackage holds the lifecycle orchestrator plus the pure
``scoring`` transformation, and the round test suite mirrors that split
into ``test_round.py`` (lifecycle / play-state loop / belote / bidding)
and ``test_round_scoring.py`` (the scoring grid). The legal-play oracle
now lives in ``contrai-core`` (``test_play_legality.py``). The four
positioned players are used across the round tests, so the fixture lives
here. Each file keeps its own scenario-builder helpers, which are
specific to the state that file exercises.
"""

from __future__ import annotations

import pytest

from contrai_core.position import Position
from contrai_core.team import Team

from contrai_engine.model.player import AiPlayer


@pytest.fixture
def players():
    """Four positioned players wired into N-S and E-W teams."""
    north = AiPlayer("N", Position.NORTH)
    east = AiPlayer("E", Position.EAST)
    south = AiPlayer("S", Position.SOUTH)
    west = AiPlayer("W", Position.WEST)
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    for p in (north, south):
        p.team = ns
    for p in (east, west):
        p.team = ew
    return {"N": north, "E": east, "S": south, "W": west}
