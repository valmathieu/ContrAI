"""§7.2 component arithmetic, over plain ints.

Every table in ``contree-domain.md`` §7.2 is reproduced here as a
parametrize list. The scorer's job is only to feed these functions the
right numbers; the rules themselves are pinned in this file.
"""

from __future__ import annotations

import pytest

from contrai_core.rule_config import RuleConfig

from contrai_engine.model.round.components import (
    FLAT_FAILURE_PILE,
    Mark,
    contract_components,
    marked_total,
)

CLASSIC = RuleConfig()


def _components(**kwargs):
    """Call ``contract_components`` with the §7.2 default shape."""
    defaults = dict(
        contract_value=100,
        slam_family=False,
        made=True,
        multiplier=1,
        attack_pile=102,
        defense_pile=60,
        substitute=None,
        rules=CLASSIC,
    )
    return contract_components(**{**defaults, **kwargs})


class TestFlatPile:
    def test_a_failure_flattens_the_pile_to_160(self):
        # §7.2: "the defense takes the pile whole, so its 162 points are
        # written as a flat 160 instead of being counted out".
        assert FLAT_FAILURE_PILE == 160


class TestNumericUndoubled:
    """§7.2 — the two sides share the pile."""

    def test_made_shares_the_pile_and_gives_the_declarer_the_contract(self):
        att, dfn = _components()
        assert att == Mark(made=102, announced=100)
        assert dfn == Mark(made=60, announced=0)

    def test_the_worked_example_marks_192_and_60(self):
        # §7.2: contract 90 ♥, declarer realizes 102 -> 192 / 60.
        att, dfn = _components(contract_value=90)
        assert marked_total(att, 1, CLASSIC) == 192
        assert marked_total(dfn, 1, CLASSIC) == 60

    def test_failed_gives_the_defense_the_flat_pile_and_the_contract(self):
        att, dfn = _components(made=False, attack_pile=48, defense_pile=114)
        assert att == Mark(made=0, announced=0)
        assert dfn == Mark(made=160, announced=100)

    def test_the_failed_worked_example_marks_260_and_0(self):
        # §7.2: contract 100 ♠, failed -> defense 260, declarer 0.
        att, dfn = _components(made=False, attack_pile=48, defense_pile=114)
        assert marked_total(dfn, 1, CLASSIC) == 260
        assert marked_total(att, 1, CLASSIC) == 0


class TestNumericDoubled:
    """§7.2 — winner takes all, the pile flat at 160."""

    @pytest.mark.parametrize("multiplier, expected", [(2, 360), (4, 560)])
    def test_made_marks_160_plus_c_times_m(self, multiplier, expected):
        att, dfn = _components(multiplier=multiplier)
        assert marked_total(att, multiplier, CLASSIC) == expected
        assert marked_total(dfn, multiplier, CLASSIC) == 0

    @pytest.mark.parametrize("multiplier, expected", [(2, 360), (4, 560)])
    def test_failed_marks_the_same_amount_to_the_defense(
        self, multiplier, expected
    ):
        att, dfn = _components(
            made=False, multiplier=multiplier, attack_pile=48, defense_pile=114
        )
        assert marked_total(dfn, multiplier, CLASSIC) == expected
        assert marked_total(att, multiplier, CLASSIC) == 0

    def test_only_announced_off_multiplies_both_components(self):
        # §7.2 second row: (160 + A) × M = (160 + 100) × 2 = 520.
        rules = RuleConfig(only_announced_points_multiplied=False)
        att, _ = _components(multiplier=2, rules=rules)
        assert marked_total(att, 2, rules) == 520


class TestSlamFamily:
    """§7.2 — the substitute replaces the pile; the grid is symmetric."""

    #: (base, multiplier, only_announced_multiplied, total)
    GRID = [
        (250, 1, True, 500), (250, 2, True, 750), (250, 4, True, 1250),
        (500, 1, True, 1000), (500, 2, True, 1500), (500, 4, True, 2500),
        (250, 1, False, 500), (250, 2, False, 1000), (250, 4, False, 2000),
        (500, 1, False, 1000), (500, 2, False, 2000), (500, 4, False, 4000),
    ]

    @pytest.mark.parametrize("base, multiplier, only_announced, total", GRID)
    def test_the_made_grid(self, base, multiplier, only_announced, total):
        rules = RuleConfig(only_announced_points_multiplied=only_announced)
        att, dfn = _components(
            contract_value=base, slam_family=True, substitute=base,
            multiplier=multiplier, attack_pile=162, defense_pile=0, rules=rules,
        )
        assert marked_total(att, multiplier, rules) == total
        assert marked_total(dfn, multiplier, rules) == 0

    @pytest.mark.parametrize("base, multiplier, only_announced, total", GRID)
    def test_the_failed_grid_is_the_mirror(
        self, base, multiplier, only_announced, total
    ):
        rules = RuleConfig(only_announced_points_multiplied=only_announced)
        att, dfn = _components(
            contract_value=base, slam_family=True, substitute=base, made=False,
            multiplier=multiplier, attack_pile=0, defense_pile=162, rules=rules,
        )
        assert marked_total(dfn, multiplier, rules) == total
        assert marked_total(att, multiplier, rules) == 0


class TestMarkingConventions:
    """§7.3 — which components are actually written down."""

    BOTH = RuleConfig()
    MADE_ONLY = RuleConfig(mark_announced_points=False)
    ANNOUNCED_ONLY = RuleConfig(mark_made_points=False)

    @pytest.mark.parametrize("rules, att, dfn", [
        (BOTH, 202, 60),            # C + P_attack ; its own points
        (MADE_ONLY, 102, 60),       # P_attack ; its own points
        (ANNOUNCED_ONLY, 100, 0),   # C ; nothing
    ])
    def test_an_undoubled_made_contract(self, rules, att, dfn):
        a, d = _components(rules=rules)
        assert marked_total(a, 1, rules) == att
        assert marked_total(d, 1, rules) == dfn

    @pytest.mark.parametrize("rules, dfn", [
        (BOTH, 260),                # 160 + C
        (MADE_ONLY, 160),           # the flat pile
        (ANNOUNCED_ONLY, 100),      # C
    ])
    def test_an_undoubled_failed_contract(self, rules, dfn):
        a, d = _components(made=False, attack_pile=48, defense_pile=114,
                           rules=rules)
        assert marked_total(d, 1, rules) == dfn
        assert marked_total(a, 1, rules) == 0

    def test_the_multiplier_falls_on_made_points_when_announced_is_off(self):
        # §7.3 override: otherwise a double would change nothing at all.
        rules = self.MADE_ONLY
        a, _ = _components(multiplier=2, rules=rules)
        assert marked_total(a, 2, rules) == 320          # 160 × 2

    @pytest.mark.parametrize("substitute, total", [(250, 500), (500, 1000)])
    def test_made_points_only_marks_the_slam_substitute(
        self, substitute, total
    ):
        # §7.3, "Slam-family: the 250 / 500 substitute" on a failure.
        rules = self.MADE_ONLY
        _, d = _components(contract_value=substitute, slam_family=True,
                           substitute=substitute, made=False, multiplier=2,
                           attack_pile=0, defense_pile=162, rules=rules)
        assert marked_total(d, 2, rules) == total        # substitute × 2

    def test_announced_only_marks_the_contract_times_the_multiplier(self):
        # The announced component is A = C, and A × M is what a table
        # marking only announced points writes — whatever the *only
        # announced points are multiplied* switch says, since there is
        # no other component for the multiplier to spread onto.
        rules = RuleConfig(mark_made_points=False,
                           only_announced_points_multiplied=False)
        a, _ = _components(multiplier=2, rules=rules)
        assert marked_total(a, 2, rules) == 200          # C × M, not 320

    def test_announced_only_is_unaffected_by_the_multiplied_switch(self):
        # Both spellings of the announced-only table agree, which is the
        # point: the switch has nothing to choose between.
        spread = RuleConfig(mark_made_points=False,
                            only_announced_points_multiplied=False)
        announced_alone = RuleConfig(mark_made_points=False)
        a_spread, _ = _components(multiplier=4, rules=spread)
        a_alone, _ = _components(multiplier=4, rules=announced_alone)
        assert marked_total(a_spread, 4, spread) == 400
        assert marked_total(a_alone, 4, announced_alone) == 400


class TestUnannouncedSweep:
    """§7.2 — the substitute absorbs the 152 cards and the 10-point bonus."""

    @pytest.mark.parametrize("substitute, total", [(250, 350), (500, 600)])
    def test_the_substitute_stands_in_for_the_whole_pile(
        self, substitute, total
    ):
        # §7.2 worked example: contract 100 ♠ swept -> 350, or 600 when
        # the declarer took all 8 personally.
        att, dfn = _components(
            substitute=substitute, attack_pile=162, defense_pile=0
        )
        assert att == Mark(made=substitute, announced=100)
        assert marked_total(att, 1, CLASSIC) == total
        assert dfn == Mark(made=0, announced=0)
