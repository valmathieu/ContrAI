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

import random

import pytest

from contrai_core.position import Position
from contrai_core.team import Team

from contrai_engine.model.player import AiPlayer


@pytest.fixture(autouse=True)
def pinned_rng():
    """Pin the global RNG for the duration of each model test.

    ``RuleBasedCardPlayStrategy`` draws from the global ``random`` module
    when nothing separates two equally cheap cards, which is the same
    module the CLI's ``--seed`` flag seeds. Tests asserting a concrete
    card — or a concrete score across a full stacked round — need that
    draw pinned, so the seed is applied here rather than repeated per
    scenario. The prior state is restored afterwards so the seed never
    leaks into whatever runs next.
    """

    state = random.getstate()
    random.seed(20260803)
    yield
    random.setstate(state)


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
