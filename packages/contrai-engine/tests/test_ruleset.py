"""Pins the TOML ruleset loader/writer and the --rules/--preset resolution."""

import dataclasses
import tomllib

import pytest

from contrai_core import (AllTrumpBelote, InvalidRuleConfigError, PRESETS,
                          Rounding, RuleConfig, TurnDirection)
from contrai_engine.ruleset import (SECTIONS, RulesetError, dump_ruleset,
                                    load_ruleset, parse_ruleset, resolve_rules)

#: Spec §3.1 under the uniform "= aligned at longest key" rule (the spec's
#: [card_play] block carries one stray extra space; we do not reproduce it).
CLASSIC_TOML = (
    "[general]\n"
    "target_score   = 2000\n"
    'turn_direction = "anticlockwise"\n'
    "\n"
    "[trump]\n"
    "extended_trump_choices = false\n"
    'all_trump_belote       = "single"\n'
    "\n"
    "[deal]\n"
    "reshuffle_every_round = false\n"
    "\n"
    "[bidding]\n"
    "solo_slam_available      = true\n"
    "slam_can_be_doubled      = true\n"
    "solo_slam_can_be_doubled = true\n"
    "\n"
    "[card_play]\n"
    "under_trump_exemption           = true\n"
    "solo_slam_gives_the_lead        = false\n"
    "belote_counts_toward_contract   = true\n"
    "belote_lost_when_contract_fails = false\n"
    "\n"
    "[scoring]\n"
    "mark_made_points                   = true\n"
    "mark_announced_points              = true\n"
    "only_announced_points_multiplied   = true\n"
    "any_failure_marks_160              = false\n"
    "unannounced_slam_substitute        = true\n"
    "failed_slam_marks_made_points      = true\n"
    "failed_slam_marks_announced_points = true\n"
    "attack_must_outscore_defense       = true\n"
    'rounding                           = "exact"\n'
    "win_on_belote_points_alone         = true\n"
)


class TestSections:
    def test_sections_cover_every_field_exactly_once(self):
        names = [n for fields in SECTIONS.values() for n in fields]
        assert sorted(names) == sorted(f.name for f in dataclasses.fields(RuleConfig))
        assert len(names) == len(set(names))

    def test_section_order_is_the_catalogue_order(self):
        assert list(SECTIONS) == ["general", "trump", "deal", "bidding", "card_play", "scoring"]


class TestParse:
    def test_empty_document_is_the_defaults(self):
        assert parse_ruleset("") == RuleConfig()

    def test_missing_keys_keep_defaults(self):
        assert parse_ruleset("[general]\ntarget_score = 1000\n") == RuleConfig(target_score=1000)

    def test_full_classic_document(self):
        assert parse_ruleset(CLASSIC_TOML) == RuleConfig()

    def test_enum_tokens_map_to_members(self):
        cfg = parse_ruleset('[general]\nturn_direction = "clockwise"\n'
                            '[trump]\nall_trump_belote = "four"\n'
                            '[scoring]\nrounding = "nearest_5"\n')
        assert cfg.turn_direction is TurnDirection.CLOCKWISE
        assert cfg.all_trump_belote is AllTrumpBelote.FOUR
        assert cfg.rounding is Rounding.NEAREST_5

    def test_unknown_section_raises(self):
        with pytest.raises(RulesetError, match="unknown section 'scorring'"):
            parse_ruleset("[scorring]\nrounding = \"exact\"\n")

    def test_unknown_key_raises(self):
        with pytest.raises(RulesetError, match=r"unknown key 'target_scor' in \[general\]"):
            parse_ruleset("[general]\ntarget_scor = 2000\n")

    def test_top_level_key_raises(self):
        with pytest.raises(RulesetError, match="section"):
            parse_ruleset("target_score = 2000\n")

    def test_section_name_used_as_a_bare_value_raises(self):
        # A top-level key that happens to be spelled like a section: the
        # name is known, so the unknown-section guard lets it through and
        # the shape check has to catch it.
        with pytest.raises(RulesetError, match=r"'general' must be a \[section\] table"):
            parse_ruleset("general = 2000\n")

    @pytest.mark.parametrize("text, match", [
        ('[general]\ntarget_score = "2000"\n', "target_score.*int"),
        ("[general]\ntarget_score = true\n", "target_score.*int"),
        ("[trump]\nextended_trump_choices = 1\n", "extended_trump_choices.*bool"),
        ("[general]\nturn_direction = 1\n", "turn_direction.*str"),
    ])
    def test_wrong_type_raises(self, text, match):
        with pytest.raises(RulesetError, match=match):
            parse_ruleset(text)

    def test_unknown_enum_token_lists_the_valid_ones(self):
        with pytest.raises(RulesetError, match="anticlockwise, clockwise"):
            parse_ruleset('[general]\nturn_direction = "widdershins"\n')

    def test_malformed_toml_is_a_ruleset_error(self):
        with pytest.raises(RulesetError, match="TOML"):
            parse_ruleset("[general\n")

    def test_domain_validation_propagates_as_is(self):
        with pytest.raises(InvalidRuleConfigError):
            parse_ruleset("[scoring]\nmark_made_points = false\nmark_announced_points = false\n")

    def test_ruleset_error_is_a_value_error(self):
        assert issubclass(RulesetError, ValueError)


class TestDump:
    def test_classic_dump_is_the_section_3_1_layout(self):
        assert dump_ruleset(RuleConfig()) == CLASSIC_TOML

    def test_dump_is_valid_toml(self):
        tomllib.loads(dump_ruleset(RuleConfig()))

    @pytest.mark.parametrize("cfg", [
        RuleConfig(),
        RuleConfig(target_score=500, turn_direction=TurnDirection.CLOCKWISE,
                   all_trump_belote=AllTrumpBelote.FOUR, rounding=Rounding.NEAREST_10,
                   mark_made_points=False),
    ])
    def test_round_trip(self, cfg):
        assert parse_ruleset(dump_ruleset(cfg)) == cfg

    def test_bools_are_lowercase_and_enums_quoted(self):
        text = dump_ruleset(RuleConfig())
        assert "True" not in text and 'rounding                           = "exact"' in text


class TestLoad:
    def test_reads_utf8_file(self, tmp_path):
        p = tmp_path / "table.toml"
        p.write_text(CLASSIC_TOML, encoding="utf-8")
        assert load_ruleset(p) == RuleConfig()

    def test_missing_file_raises_os_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_ruleset(tmp_path / "nope.toml")


class TestResolve:
    def test_nothing_is_the_defaults(self):
        assert resolve_rules(preset=None, rules_path=None) == RuleConfig()

    def test_preset_by_name(self):
        assert resolve_rules(preset="classic", rules_path=None) == PRESETS["classic"]

    def test_unknown_preset_raises(self):
        with pytest.raises(RulesetError, match="unknown preset 'house'.*classic"):
            resolve_rules(preset="house", rules_path=None)

    def test_file_path(self, tmp_path):
        p = tmp_path / "t.toml"
        p.write_text("[general]\ntarget_score = 1000\n", encoding="utf-8")
        assert resolve_rules(preset=None, rules_path=p) == RuleConfig(target_score=1000)

    def test_both_is_an_error(self, tmp_path):
        with pytest.raises(RulesetError, match="mutually exclusive"):
            resolve_rules(preset="classic", rules_path=tmp_path / "x.toml")
