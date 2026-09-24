"""Pins the table-rule catalogue value: RuleConfig, its enums, presets."""

import dataclasses

import pytest

from contrai_core import (
    PRESETS,
    TARGET_SCORES,
    AllTrumpBelote,
    DisputeResolution,
    InvalidRuleConfigError,
    Rounding,
    RuleConfig,
    TurnDirection,
)


class TestEnums:
    @pytest.mark.parametrize("enum_cls, expected", [
        (TurnDirection, {"ANTICLOCKWISE": "anticlockwise", "CLOCKWISE": "clockwise"}),
        (AllTrumpBelote, {"NONE": "none", "SINGLE": "single", "FOUR": "four"}),
        (Rounding, {"EXACT": "exact", "NEAREST_10": "nearest_10", "NEAREST_5": "nearest_5"}),
    ])
    def test_members_and_values(self, enum_cls, expected):
        assert {m.name: m.value for m in enum_cls} == expected

    @pytest.mark.parametrize("member", [*TurnDirection, *AllTrumpBelote, *Rounding])
    def test_str_is_the_toml_token(self, member):
        # House style (types.py): f-strings render the token, not Enum.NAME.
        assert str(member) == member.value
        assert f"{member}" == member.value

    def test_round_trips_from_token(self):
        assert Rounding("nearest_10") is Rounding.NEAREST_10


class TestDefaults:
    def test_default_is_the_section_9_catalogue(self):
        cfg = RuleConfig()
        assert cfg.target_score == 2000
        assert cfg.turn_direction is TurnDirection.ANTICLOCKWISE
        assert cfg.extended_trump_choices is False
        assert cfg.all_trump_belote is AllTrumpBelote.SINGLE
        assert cfg.reshuffle_every_round is False
        assert (cfg.solo_slam_available, cfg.slam_can_be_doubled,
                cfg.solo_slam_can_be_doubled) == (True, True, True)
        assert cfg.under_trump_exemption is True
        assert cfg.solo_slam_gives_the_lead is False
        assert cfg.belote_counts_toward_contract is True
        assert cfg.belote_lost_when_contract_fails is False
        assert (cfg.mark_made_points, cfg.mark_announced_points) == (True, True)
        assert cfg.only_announced_points_multiplied is True
        assert cfg.any_failure_marks_160 is False
        assert cfg.unannounced_slam_substitute is True
        # §7.2 "Un-doubled only", and the personal-sweep premium the
        # same section pays — both catalogue defaults.
        assert cfg.substitute_survives_doubling is False
        assert cfg.personal_sweep_marks_solo_slam is True
        # §7.5: a dispute fails the contract unless the table says otherwise.
        assert cfg.dispute_resolution is DisputeResolution.FAILED
        assert (cfg.failed_slam_marks_made_points,
                cfg.failed_slam_marks_announced_points) == (True, True)
        assert cfg.attack_must_outscore_defense is True
        assert cfg.rounding is Rounding.EXACT
        assert cfg.win_on_belote_points_alone is True

    def test_has_exactly_25_fields(self):
        assert len(dataclasses.fields(RuleConfig)) == 25

    def test_target_scores_constant(self):
        assert TARGET_SCORES == (500, 1000, 1500, 2000, 3000, 4000, 5000)


class TestImmutability:
    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            RuleConfig().target_score = 1000  # type: ignore[misc]

    def test_slotted(self):
        with pytest.raises((AttributeError, TypeError)):
            RuleConfig().extra = 1  # type: ignore[attr-defined]

    def test_hashable_and_value_equal(self):
        assert RuleConfig() == RuleConfig()
        assert hash(RuleConfig()) == hash(RuleConfig())
        assert RuleConfig(target_score=1000) != RuleConfig()


class TestValidation:
    def test_both_marking_conventions_off_is_rejected(self):
        with pytest.raises(InvalidRuleConfigError,
                           match="mark_made_points.*mark_announced_points"):
            RuleConfig(mark_made_points=False, mark_announced_points=False)

    @pytest.mark.parametrize("made, announced", [(True, False), (False, True)])
    def test_one_marking_convention_is_enough(self, made, announced):
        RuleConfig(mark_made_points=made, mark_announced_points=announced)

    @pytest.mark.parametrize("bad", [0, 1499, 2500, 10000, -500])
    def test_target_score_off_ladder_is_rejected(self, bad):
        with pytest.raises(InvalidRuleConfigError, match="target_score"):
            RuleConfig(target_score=bad)

    @pytest.mark.parametrize("ok", TARGET_SCORES)
    def test_every_ladder_value_is_accepted(self, ok):
        assert RuleConfig(target_score=ok).target_score == ok

    def test_error_is_a_value_error(self):
        with pytest.raises(ValueError):
            RuleConfig(mark_made_points=False, mark_announced_points=False)

    def test_inert_combinations_are_not_rejected(self):
        # Spec §2.8: documented as inert, never an error.
        RuleConfig(failed_slam_marks_announced_points=True, any_failure_marks_160=False)
        RuleConfig(all_trump_belote=AllTrumpBelote.FOUR, extended_trump_choices=False)


class TestPresets:
    def test_classic_is_the_defaults(self):
        assert RuleConfig.classic() == RuleConfig()

    def test_presets_name_classic_and_tournament(self):
        assert set(PRESETS) == {"classic", "tournament"}
        assert PRESETS["classic"] == RuleConfig()

    def test_tournament_moves_exactly_seven_knobs_off_classic(self):
        tournament = RuleConfig.tournament()
        assert PRESETS["tournament"] == tournament
        moved = {
            field.name
            for field in dataclasses.fields(RuleConfig)
            if getattr(tournament, field.name) != getattr(RuleConfig(), field.name)
        }
        assert moved == {
            "turn_direction",
            "any_failure_marks_160",
            "only_announced_points_multiplied",
            "solo_slam_gives_the_lead",
            "substitute_survives_doubling",
            "personal_sweep_marks_solo_slam",
            "dispute_resolution",
        }
        assert tournament.turn_direction is TurnDirection.CLOCKWISE
        assert tournament.any_failure_marks_160 is True
        assert tournament.only_announced_points_multiplied is False
        assert tournament.solo_slam_gives_the_lead is True
        # Both read off the V5 corpus's 59 sweeps: the site marks the
        # substitute on a doubled contract, and marks 250 for a
        # declarer's personal sweep rather than the Solo Slam's 500.
        assert tournament.substitute_survives_doubling is True
        assert tournament.personal_sweep_marks_solo_slam is False
        # obs-f3c28d3b round 3: an 81/81 on an 80 marked the defense 81,
        # the declarer nothing, and paid its 161 into round 4's winner.
        assert tournament.dispute_resolution is DisputeResolution.HELD


class TestDisputeResolution:
    def test_renders_its_toml_token(self):
        assert str(DisputeResolution.HELD) == "held"

    def test_has_exactly_the_three_catalogue_values(self):
        assert [m.value for m in DisputeResolution] == ["failed", "shared", "held"]
