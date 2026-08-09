"""Tests for the cross-screen layout helpers in
:mod:`contrai_engine.view.layout`.

The Prompt panel folds an optional rejection notice above the question
and grows a row to fit it — that branching is what's worth locking down.
The two-column grid is asserted on its column *configuration* rather
than its rendered output: the fixed left width and the no-wrap columns
are what stop a wide panel from reflowing the screen beside it.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from contrai_core import TeamSide
from contrai_engine.view.layout import (
    _panel_event_log,
    _panel_game_score,
    _panel_prompt,
    _two_column,
)
from contrai_engine.view.theme import RED, YELLOW


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

    def test_mandatory_styles_the_question_bold_yellow(self):
        """Input the player must answer now is visually escalated."""

        panel = _panel_prompt(Text("Your bid?"), True)
        body = panel.renderable
        styles = {
            str(span.style)
            for span in body.spans
            if body.plain[span.start:span.end] == "Your bid?"
        }

        assert any("bold" in style and YELLOW in style for style in styles)

    def test_mandatory_does_not_mutate_the_caller_question(self):
        """The styling is applied to a copy — the caller's ``Text`` is reusable."""

        question = Text("Your bid?")
        _panel_prompt(question, True)

        assert question.spans == []


class TestPanelGameScore:
    """The in-game top-left panel: both team totals and the target."""

    def test_renders_scores_and_target(self):
        panel = _panel_game_score(
            {TeamSide.NS: 120, TeamSide.EW: 250}, target_score=1500
        )
        text = panel.renderable.plain
        assert panel.title.plain == "Game score"
        assert "N-S" in text and "120" in text
        assert "E-W" in text and "250" in text
        assert "Target" in text and "1500" in text

    def test_missing_teams_default_to_zero(self):
        # A fresh game hands over an empty dict before any round scores.
        panel = _panel_game_score({}, target_score=1000)
        text = panel.renderable.plain
        assert text.count("0") >= 2
        assert "1000" in text


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


class TestTwoColumn:
    """`_two_column` — the side-by-side grid every in-game screen sits in."""

    @staticmethod
    def _grid(left_width: int = 30):
        return _two_column(
            Panel(Text("left")), Panel(Text("right")), left_width=left_width
        )

    def test_places_both_panels_in_a_single_row(self):
        """One row keeps the grid exactly as tall as the taller panel."""

        grid = self._grid()
        assert grid.row_count == 1
        assert len(grid.columns) == 2

    def test_left_width_is_honored_on_the_first_column_only(self):
        grid = self._grid(left_width=42)
        left, right = grid.columns

        assert left.width == 42
        # The right column is unconstrained so it takes the remainder.
        assert right.width is None

    def test_both_columns_are_no_wrap(self):
        """Wrapping a bordered panel would shear its frame mid-row."""

        assert all(column.no_wrap for column in self._grid().columns)

    def test_the_grid_does_not_expand(self):
        """``expand`` would stretch the row to the console width."""

        assert self._grid().expand is False
