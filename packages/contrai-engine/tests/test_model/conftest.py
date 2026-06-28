"""Shared fixtures for the model-layer round tests.

The single ``round.py`` module was split into a ``round/`` subpackage
(the lifecycle orchestrator plus the pure ``scoring`` and ``legality``
transformations), and the round test suite mirrors that split into
``test_round.py`` (lifecycle / belote / bidding), ``test_round_legality.py``
(the legal-play oracle) and ``test_round_scoring.py`` (the scoring grid).
The four positioned players are used across all three, so the fixture
lives here. Each file keeps its own scenario-builder helpers, which are
specific to the state that file exercises.
"""

from __future__ import annotations

import pytest

from contrai_core.team import Team

from contrai_engine.model.player import AiPlayer


@pytest.fixture
def players():
    """Four positioned players wired into N-S and E-W teams."""
    north = AiPlayer("N", "North")
    east = AiPlayer("E", "East")
    south = AiPlayer("S", "South")
    west = AiPlayer("W", "West")
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    for p in (north, south):
        p.team = ns
    for p in (east, west):
        p.team = ew
    return {"N": north, "E": east, "S": south, "W": west}
