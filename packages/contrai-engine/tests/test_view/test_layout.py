"""Tests for the cross-screen layout helpers in
:mod:`contrai_engine.view.layout`.

The Prompt panel folds an optional rejection notice above the question
and grows a row to fit it — that branching is what's worth locking down.
"""

from __future__ import annotations

from rich.text import Text

from contrai_engine.view.layout import _panel_event_log, _panel_prompt
from contrai_engine.view.theme import RED


class TestPanelPromptNotice:
    """The rejection line is rendered inside the Prompt panel itself."""

    def test_notice_appears_above_question(self):
        notice = Text("✗ doubling your own side", style=RED)
        panel = _panel_prompt(Text("Your bid?"), False, notice=notice)
        text = panel.renderable.plain
        # Both the reason and the question share the one panel, reason
        # first — so the player never has to scroll to see why input
        # bounced.
        assert "own side" in text
        assert "Your bid?" in text
        assert text.index("own side") < text.index("Your bid?")
        # Grows a row to fit the extra line.
        assert panel.height == 5

    def test_no_notice_keeps_compact_height(self):
        panel = _panel_prompt(Text("Your bid?"), False)
        assert "own side" not in panel.renderable.plain
        assert panel.height == 4


class TestPanelEventLog:
    """The rolling event-log panel: placeholder when empty, lines when not."""

    def test_renders_lines(self):
        panel = _panel_event_log([Text("alpha"), Text("beta")], log_max=5)
        assert "alpha" in panel.renderable.plain
        assert "beta" in panel.renderable.plain
        assert panel.title.plain == "Log"

    def test_empty_placeholder(self):
        panel = _panel_event_log([], log_max=5)
        assert "(no events yet)" in panel.renderable.plain
