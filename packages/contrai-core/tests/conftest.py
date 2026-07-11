"""Shared fixtures for the ``contrai-core`` test suite.

The play-phase tests (``test_play_legality.py`` and ``test_play_state.py``)
both need four positioned players wired into the two contrée teams, so the
fixture lives here. Plain :class:`BasePlayer` instances are used — the core
package has no game-flow player subclasses, and the play-legality rules only
read a player's ``team`` and identity.
"""

from __future__ import annotations

import pytest

from contrai_core import BasePlayer, Team


@pytest.fixture
def players() -> dict[str, BasePlayer]:
    """Four positioned players wired into N-S and E-W teams.

    Returns:
        A mapping of seat letter (``"N"``/``"E"``/``"S"``/``"W"``) to the
        :class:`BasePlayer` seated there. North-South and East-West are the
        two teams, matching the physical seating where partners sit opposite.
    """
    north = BasePlayer("N", "North")
    east = BasePlayer("E", "East")
    south = BasePlayer("S", "South")
    west = BasePlayer("W", "West")
    ns = Team("North-South", [north, south])
    ew = Team("East-West", [east, west])
    for p in (north, south):
        p.team = ns
    for p in (east, west):
        p.team = ew
    return {"N": north, "E": east, "S": south, "W": west}
