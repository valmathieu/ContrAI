"""Tests for the setup screen in :mod:`contrai_engine.view.screens.setup`.

Covers the pre-deal builders: the four-row table summary (origin and its
change count, target annotation, trump wording, live-score state), the
numbered preset radio, and the three prompt lines the input loops print
under them.

The screen is laid out against the same fixed 70-column width as the rest
of the landing screen, so the width assertions compare against the
module's own constant rather than a magic number.
"""

from __future__ import annotations

import pytest

from contrai_core import RuleConfig, TurnDirection
from contrai_engine.options import TableAids
from contrai_engine.ruleset import TableSetup
from contrai_engine.view.screens.setup import (
    WIDTH,
    _file_prompt_text,
    _panel_preset_list,
    _panel_table_setup,
    _preset_prompt_text,
    _setup_prompt_text,
    _target_annotation,
)
from contrai_engine.view.theme import TARGET_OPTIONS


def _rows(panel) -> str:
    """Plain text of a panel whose renderable is a ``Text``."""
    return panel.renderable.plain


def test_target_options_cover_the_catalogue():
    """The annotations describe core's own §9.1 ladder, and the default is
    the ruleset's — not a view opinion."""
    from contrai_core import TARGET_SCORES
    from contrai_engine.view.theme import DEFAULT_TARGET

    assert tuple(value for value, _, _ in TARGET_OPTIONS) == TARGET_SCORES
    assert DEFAULT_TARGET == RuleConfig().target_score == 2000


class TestTargetAnnotation:
    """The label + estimate a target score is described with."""

    @pytest.mark.parametrize("value, label, estimate", TARGET_OPTIONS)
    def test_every_offered_target_is_described(self, value, label, estimate):
        assert _target_annotation(value) == (label, estimate)

    def test_an_undescribed_target_yields_empty_strings(self):
        """Presentation must not raise over a value it has no row for."""
        assert _target_annotation(1234) == ("", "")


class TestPanelTableSetup:
    """The four-row summary of the table the next deal is played at."""

    def test_names_the_origin(self):
        text = _rows(_panel_table_setup(TableSetup(origin="house.toml")))
        assert "house.toml" in text

    def test_classic_shows_no_change_count(self):
        """The §9 defaults are zero changes, and say nothing about it."""
        assert "change" not in _rows(_panel_table_setup(TableSetup()))

    def test_one_edited_knob_counts_in_the_singular(self):
        setup = TableSetup(rules=RuleConfig(reshuffle_every_round=True))
        assert "+1 change from classic" in _rows(_panel_table_setup(setup))

    def test_several_edited_knobs_count_in_the_plural(self):
        setup = TableSetup(
            rules=RuleConfig(
                reshuffle_every_round=True,
                turn_direction=TurnDirection.CLOCKWISE,
            )
        )
        assert "+2 changes from classic" in _rows(_panel_table_setup(setup))

    def test_target_row_carries_its_annotation(self):
        setup = TableSetup(rules=RuleConfig(target_score=500))
        text = _rows(_panel_table_setup(setup))
        assert "500" in text
        assert "Quick game" in text
        assert "~10 min" in text

    def test_trump_row_says_suits_only_by_default(self):
        text = _rows(_panel_table_setup(TableSetup()))
        assert "suits only" in text

    def test_trump_row_names_the_variants_when_they_are_biddable(self):
        setup = TableSetup(rules=RuleConfig(extended_trump_choices=True))
        text = _rows(_panel_table_setup(setup))
        assert "no trump" in text
        assert "all trump" in text

    def test_live_score_row_tracks_the_aid(self):
        on = _rows(_panel_table_setup(TableSetup()))
        off = _rows(
            _panel_table_setup(
                TableSetup(aids=TableAids(live_round_score=False))
            )
        )
        assert "Live round score" in on
        assert on.rstrip().endswith("on")
        assert off.rstrip().endswith("off")

    def test_panel_is_the_landing_width(self):
        assert _panel_table_setup(TableSetup()).width == WIDTH


class TestPanelPresetList:
    """The numbered ruleset radio."""

    def test_numbers_every_offer(self):
        text = _rows(_panel_preset_list(["classic", "last used"], "classic"))
        assert "1." in text
        assert "2." in text
        assert "last used" in text

    def test_exactly_one_row_is_filled(self):
        text = _rows(_panel_preset_list(["classic", "last used"], "last used"))
        assert text.count("(●)") == 1
        assert text.count("( )") == 1

    def test_the_filled_radio_sits_on_the_selected_row(self):
        for line in _rows(
            _panel_preset_list(["classic", "last used"], "last used")
        ).splitlines():
            if "(●)" in line:
                assert "last used" in line
                break
        else:
            pytest.fail("no selected row rendered")

    def test_an_unlisted_origin_fills_no_row(self):
        """A loaded file or an edited table is none of the offers, and the
        radio must not claim otherwise."""
        text = _rows(_panel_preset_list(["classic"], "custom"))
        assert "(●)" not in text
        assert text.count("( )") == 1

    def test_panel_is_the_landing_width(self):
        assert _panel_preset_list(["classic"], "classic").width == WIDTH


class TestSetupPromptText:
    """The landing dispatcher's key list."""

    def test_offers_every_key(self):
        plain = _setup_prompt_text(TableSetup()).plain
        for key in ("[Enter]", "[p]", "[f]", "[l]"):
            assert key in plain

    def test_live_key_names_the_state_it_moves_to(self):
        """With the aid on, the key offers to turn it off, and vice versa."""
        on = _setup_prompt_text(TableSetup()).plain
        off = _setup_prompt_text(
            TableSetup(aids=TableAids(live_round_score=False))
        ).plain
        assert "live score off" in on
        assert "live score on" in off


class TestPresetPromptText:
    """The preset picker's prompt."""

    def test_offers_the_number_range_and_the_names(self):
        plain = _preset_prompt_text(["classic", "last used"]).plain
        assert "[1-2]" in plain
        assert "classic" in plain
        assert "last used" in plain

    def test_enter_is_advertised_as_the_no_op(self):
        assert "[Enter]" in _preset_prompt_text(["classic"]).plain


class TestFilePromptText:
    """The file loader's prompt."""

    def test_asks_for_a_path_and_offers_to_cancel(self):
        plain = _file_prompt_text().plain
        assert "TOML" in plain
        assert "[Enter]" in plain
        assert "cancels" in plain
