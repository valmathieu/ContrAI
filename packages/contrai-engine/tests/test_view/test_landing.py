"""Tests for the landing screen in :mod:`contrai_engine.view.screens.landing`.

Covers the splash's fixed furniture: the block-ASCII title (with and
without ``pyfiglet``), the subtitle and suit ribbon, and the players
block — including the four-AI rendering an unattended autoplay run must
show instead of claiming a human seat. The editable half of the screen
lives in ``test_setup.py``.

The screen is laid out against a fixed 70-column width, so the centering
assertions below compare against that constant rather than a magic number.
"""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.panel import Panel

from contrai_core import Suit
from contrai_engine.view.formatting import _suit_glyph
from contrai_engine.view.screens import landing as landing_module
from contrai_engine.view.screens.landing import (
    _landing_subtitle,
    _landing_suit_ribbon,
    _landing_title,
    _panel_players,
)

WIDTH = 70
"""The landing screen's fixed layout width, shared by every builder here."""


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


class TestLandingTitle:
    """The block-ASCII CONTRAI title and its ``pyfiglet`` import guard."""

    def test_with_pyfiglet_renders_multi_line_block_art(self, monkeypatch):
        monkeypatch.setattr(landing_module, "_HAS_PYFIGLET", True)
        lines = _landing_title().plain.splitlines()

        # Block art is by definition taller than the single-line fallback.
        assert len(lines) > 1

    def test_without_pyfiglet_falls_back_to_the_plain_word(self, monkeypatch):
        """A missing optional dependency must still produce a title."""

        monkeypatch.setattr(landing_module, "_HAS_PYFIGLET", False)
        lines = _landing_title().plain.splitlines()

        assert len(lines) == 1
        assert lines[0].strip() == "CONTRAI"

    @pytest.mark.parametrize("has_pyfiglet", [True, False])
    def test_every_line_is_centered_to_the_layout_width(
        self, monkeypatch, has_pyfiglet
    ):
        """Both branches centre through the same ``str.center`` call."""

        monkeypatch.setattr(landing_module, "_HAS_PYFIGLET", has_pyfiglet)

        for line in _landing_title().plain.splitlines():
            assert len(line) == WIDTH


class TestLandingSubtitle:
    """The dim tagline under the block title."""

    def test_names_the_game_and_the_edition(self):
        plain = _landing_subtitle().plain
        assert "Contrée" in plain
        assert "CLI edition" in plain

    def test_is_centered_to_the_layout_width(self):
        assert len(_landing_subtitle().plain) == WIDTH


class TestLandingSuitRibbon:
    """The decorative four-glyph suit ribbon."""

    def test_shows_every_suit_glyph_in_suit_order(self):
        """The ribbon is generated from ``Suit`` itself, not a literal."""

        plain = _landing_suit_ribbon().plain
        glyphs = [_suit_glyph(suit) for suit in Suit]

        assert all(glyph in plain for glyph in glyphs)
        # Order matters: the ribbon iterates ``Suit`` and must not sort.
        positions = [plain.index(glyph) for glyph in glyphs]
        assert positions == sorted(positions)

    def test_is_centered_within_the_layout_width(self):
        """Leading pad centres the glyph run; trailing pad is not emitted."""

        ribbon = _landing_suit_ribbon()
        plain = ribbon.plain
        leading = len(plain) - len(plain.lstrip(" "))
        inner_width = ribbon.cell_len - leading

        assert leading == (WIDTH - inner_width) // 2
