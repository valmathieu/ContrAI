"""Tests for the landing screen in :mod:`contrai_engine.view.screens.landing`.

Covers the players block's per-seat roles, including the four-AI
rendering an unattended autoplay run must show instead of claiming a
human seat.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from contrai_engine.view.screens.landing import _panel_players


def _rendered(panel: Panel) -> str:
    """Plain text of a rendered panel.

    The players block wraps a ``Table.grid``, not a ``Text``, so its
    cells are only reachable once Rich has laid the table out.
    """
    console = Console(width=100, record=True, force_terminal=False)
    console.print(panel)
    return console.export_text()


class TestPanelPlayers:
    """The landing screen's players block."""

    def test_default_seats_a_human_at_south(self):
        text = _rendered(_panel_players())
        assert "You" in text
        assert "(human)" in text
        assert text.count("AI · expert") == 3

    def test_autoplay_seats_four_ai_players(self):
        """An unattended run must not announce a seat nobody is in."""
        text = _rendered(_panel_players(autoplay=True))
        assert "You" not in text
        assert "human" not in text
        assert text.count("AI · expert") == 4

    def test_autoplay_defaults_to_false(self):
        """Back-compat: the no-argument call keeps the human seat."""
        assert _rendered(_panel_players()) == _rendered(
            _panel_players(autoplay=False)
        )
