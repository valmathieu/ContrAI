"""Pins the TOML setup loader/writer and the --rules/--preset resolution."""

import dataclasses
import tomllib

import pytest

from contrai_core import (AllTrumpBelote, InvalidRuleConfigError, PRESETS,
                          Rounding, RuleConfig, TARGET_SCORES, TurnDirection)
from contrai_engine.options import TableAids
from contrai_engine.ruleset import (AID_SECTION, KNOB_LABELS, SECTIONS,
                                    RulesetError, TableSetup, cycle_knob,
                                    dump_ruleset, dump_setup, load_ruleset,
                                    load_setup, non_default_knobs,
                                    knob_value, parse_ruleset, parse_setup,
                                    resolve_rules,
                                    resolve_setup, save_setup, setup_path)

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


#: The seventh, optional section a full setup document carries.
AIDS_TOML = "[table_aids]\nlive_round_score = true\n"


class TestAidSection:
    def test_aid_section_is_named_and_covers_every_field(self):
        name, keys = AID_SECTION
        assert name == "table_aids"
        assert sorted(keys) == sorted(f.name for f in dataclasses.fields(TableAids))

    def test_aid_section_is_not_a_rule_section(self):
        """Aids are presentation: they must not leak into ``SECTIONS``."""
        assert AID_SECTION[0] not in SECTIONS


class TestParseSetup:
    def test_empty_document_is_the_defaults(self):
        assert parse_setup("") == TableSetup()

    def test_seven_section_round_trip(self):
        setup = TableSetup(
            rules=RuleConfig(target_score=500, rounding=Rounding.NEAREST_5),
            aids=TableAids(live_round_score=False),
        )
        assert parse_setup(dump_setup(setup)) == setup

    def test_six_section_file_still_parses(self):
        """A pre-existing ``--rules`` file has no ``[table_aids]`` and must
        keep working, taking the aid defaults."""
        setup = parse_setup(CLASSIC_TOML)
        assert setup.rules == RuleConfig()
        assert setup.aids == TableAids()

    def test_aid_section_alone_leaves_the_rules_at_the_defaults(self):
        setup = parse_setup("[table_aids]\nlive_round_score = false\n")
        assert setup.rules == RuleConfig()
        assert setup.aids == TableAids(live_round_score=False)

    def test_unknown_key_in_the_aid_section_raises(self):
        with pytest.raises(
            RulesetError, match=r"unknown key 'live_score' in \[table_aids\]"
        ):
            parse_setup("[table_aids]\nlive_score = true\n")

    def test_wrong_type_in_the_aid_section_raises(self):
        with pytest.raises(RulesetError, match="live_round_score.*bool"):
            parse_setup("[table_aids]\nlive_round_score = 1\n")

    def test_unknown_section_names_the_aid_section_too(self):
        with pytest.raises(RulesetError, match="table_aids"):
            parse_setup("[aids]\nlive_round_score = true\n")

    def test_origin_defaults_to_classic(self):
        assert parse_setup("").origin == "classic"


class TestDumpSetup:
    def test_dump_is_the_ruleset_plus_the_aid_section(self):
        assert dump_setup(TableSetup()) == CLASSIC_TOML + "\n" + AIDS_TOML

    def test_dump_is_valid_toml(self):
        tomllib.loads(dump_setup(TableSetup()))

    def test_a_dumped_setup_is_a_valid_rules_file(self):
        """The trap this avoids: feeding a saved setup back through
        ``--rules`` must not be an unknown-section error."""
        assert parse_ruleset(dump_setup(TableSetup())) == RuleConfig()


class TestSetupFileIO:
    def test_save_then_load_round_trips(self, tmp_path):
        setup = TableSetup(
            rules=RuleConfig(target_score=3000),
            aids=TableAids(live_round_score=False),
        )
        path = tmp_path / "nested" / "last-setup.toml"

        save_setup(path, setup)

        assert load_setup(path).rules == setup.rules
        assert load_setup(path).aids == setup.aids

    def test_save_creates_the_parent_directory(self, tmp_path):
        path = tmp_path / "a" / "b" / "last-setup.toml"
        save_setup(path, TableSetup())
        assert path.is_file()

    def test_load_names_the_file_as_the_origin(self, tmp_path):
        path = tmp_path / "house.toml"
        save_setup(path, TableSetup())
        assert load_setup(path).origin == "house.toml"

    def test_missing_file_raises_os_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_setup(tmp_path / "nope.toml")

    def test_corrupt_file_raises_a_value_error(self, tmp_path):
        path = tmp_path / "broken.toml"
        path.write_text("[general\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_setup(path)


class TestSetupPath:
    def test_contrai_home_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONTRAI_HOME", str(tmp_path))
        assert setup_path() == tmp_path / "last-setup.toml"

    def test_defaults_under_a_dot_contrai_in_the_home_directory(self, monkeypatch):
        monkeypatch.delenv("CONTRAI_HOME", raising=False)
        path = setup_path()
        assert path.name == "last-setup.toml"
        assert path.parent.name == ".contrai"


class TestResolveSetup:
    def test_nothing_is_the_defaults(self):
        assert resolve_setup(preset=None, rules_path=None) == TableSetup()

    def test_preset_names_itself_as_the_origin(self):
        setup = resolve_setup(preset="classic", rules_path=None)
        assert setup.rules == PRESETS["classic"]
        assert setup.origin == "classic"

    def test_file_names_itself_as_the_origin(self, tmp_path):
        path = tmp_path / "house.toml"
        path.write_text("[general]\ntarget_score = 1000\n", encoding="utf-8")

        setup = resolve_setup(preset=None, rules_path=path)

        assert setup.rules == RuleConfig(target_score=1000)
        assert setup.origin == "house.toml"

    def test_a_files_aid_section_is_honoured(self, tmp_path):
        path = tmp_path / "house.toml"
        path.write_text("[table_aids]\nlive_round_score = false\n", encoding="utf-8")

        assert resolve_setup(preset=None, rules_path=path).aids == TableAids(
            live_round_score=False
        )

    def test_an_explicit_aid_overrides_the_files(self, tmp_path):
        """An explicitly typed ``--no-live-score`` is the operator's last word."""
        path = tmp_path / "house.toml"
        path.write_text("[table_aids]\nlive_round_score = true\n", encoding="utf-8")

        setup = resolve_setup(
            preset=None,
            rules_path=path,
            aids=TableAids(live_round_score=False),
        )

        assert setup.aids == TableAids(live_round_score=False)

    def test_both_is_an_error(self, tmp_path):
        with pytest.raises(RulesetError, match="mutually exclusive"):
            resolve_setup(preset="classic", rules_path=tmp_path / "x.toml")

    def test_unknown_preset_raises(self):
        with pytest.raises(RulesetError, match="unknown preset 'house'.*classic"):
            resolve_setup(preset="house", rules_path=None)


class TestCycleKnob:
    def test_bool_toggles(self):
        on = cycle_knob(RuleConfig(), "extended_trump_choices")
        assert on.extended_trump_choices is True
        assert cycle_knob(on, "extended_trump_choices").extended_trump_choices is False

    def test_enum_steps_to_the_next_member(self):
        stepped = cycle_knob(RuleConfig(), "all_trump_belote")
        assert stepped.all_trump_belote is AllTrumpBelote.FOUR

    def test_enum_wraps_at_the_end(self):
        wrapped = cycle_knob(
            RuleConfig(all_trump_belote=AllTrumpBelote.FOUR), "all_trump_belote"
        )
        assert wrapped.all_trump_belote is AllTrumpBelote.NONE

    def test_two_member_enum_wraps_in_two_steps(self):
        once = cycle_knob(RuleConfig(), "turn_direction")
        assert once.turn_direction is TurnDirection.CLOCKWISE
        twice = cycle_knob(once, "turn_direction")
        assert twice.turn_direction is TurnDirection.ANTICLOCKWISE

    def test_target_score_steps_the_ladder(self):
        stepped = cycle_knob(RuleConfig(target_score=2000), "target_score")
        assert stepped.target_score == 3000

    def test_target_score_wraps_off_the_top_rung(self):
        wrapped = cycle_knob(
            RuleConfig(target_score=TARGET_SCORES[-1]), "target_score"
        )
        assert wrapped.target_score == TARGET_SCORES[0]

    def test_every_field_is_cyclable(self):
        """No knob may be un-editable: the screen offers all 22."""
        for field in (n for fields in SECTIONS.values() for n in fields):
            assert isinstance(cycle_knob(RuleConfig(), field), RuleConfig)

    def test_the_source_config_is_untouched(self):
        rules = RuleConfig()
        cycle_knob(rules, "reshuffle_every_round")
        assert rules.reshuffle_every_round is False

    def test_unknown_knob_raises(self):
        with pytest.raises(RulesetError, match="unknown knob 'nope'"):
            cycle_knob(RuleConfig(), "nope")

    def test_an_impossible_toggle_propagates_the_core_error(self):
        """Marking neither component is the one combination the catalogue forbids."""
        one_left = RuleConfig(mark_made_points=False)
        with pytest.raises(InvalidRuleConfigError, match="mark_made_points"):
            cycle_knob(one_left, "mark_announced_points")


class TestKnobValue:
    def test_bools_render_as_on_and_off(self):
        assert knob_value(RuleConfig(), "solo_slam_available") == "on"
        assert knob_value(RuleConfig(), "extended_trump_choices") == "off"

    def test_enums_render_as_their_toml_token(self):
        assert knob_value(RuleConfig(), "turn_direction") == "anticlockwise"
        assert knob_value(RuleConfig(rounding=Rounding.NEAREST_5), "rounding") == (
            "nearest_5"
        )

    def test_target_score_renders_as_its_number(self):
        assert knob_value(RuleConfig(target_score=500), "target_score") == "500"

    def test_every_value_fits_the_panel_column(self):
        """The grid right-aligns values in a fixed column; nothing may
        outgrow ``anticlockwise``, the longest a knob can take."""
        for field in (n for fields in SECTIONS.values() for n in fields):
            rules = RuleConfig()
            for _ in range(8):
                assert len(knob_value(rules, field)) <= len("anticlockwise")
                rules = cycle_knob(rules, field)

    def test_unknown_knob_raises(self):
        with pytest.raises(RulesetError, match="unknown knob 'nope'"):
            knob_value(RuleConfig(), "nope")


class TestNonDefaultKnobs:
    def test_classic_has_none(self):
        assert non_default_knobs(RuleConfig()) == ()

    def test_reports_the_fields_that_differ(self):
        changed = non_default_knobs(
            RuleConfig(target_score=1000, turn_direction=TurnDirection.CLOCKWISE)
        )
        assert changed == (
            ("target_score", "1000"),
            ("turn_direction", "clockwise"),
        )

    def test_bools_render_as_on_and_off(self):
        assert non_default_knobs(RuleConfig(extended_trump_choices=True)) == (
            ("extended_trump_choices", "on"),
        )

    def test_order_is_the_catalogue_order(self):
        changed = non_default_knobs(
            RuleConfig(rounding=Rounding.NEAREST_10, reshuffle_every_round=True)
        )
        assert [name for name, _ in changed] == ["reshuffle_every_round", "rounding"]


class TestKnobLabels:
    def test_every_field_has_a_heading(self):
        assert sorted(KNOB_LABELS) == sorted(
            f.name for f in dataclasses.fields(RuleConfig)
        )

    def test_headings_cite_the_catalogue_subsection(self):
        assert KNOB_LABELS["target_score"] == "General (§9.1)"
        assert KNOB_LABELS["rounding"] == "Scoring (§9.6)"

    def test_fields_of_one_section_share_a_heading(self):
        for fields in SECTIONS.values():
            assert len({KNOB_LABELS[name] for name in fields}) == 1


class TestBackwardCompatibleFacade:
    """The four pre-existing functions keep their exact contract."""

    def test_parse_ruleset_still_returns_a_rule_config(self):
        assert parse_ruleset(CLASSIC_TOML) == RuleConfig()

    def test_dump_ruleset_is_still_six_sections(self):
        assert dump_ruleset(RuleConfig()) == CLASSIC_TOML
        assert "table_aids" not in dump_ruleset(RuleConfig())

    def test_load_ruleset_still_returns_a_rule_config(self, tmp_path):
        path = tmp_path / "t.toml"
        path.write_text(CLASSIC_TOML, encoding="utf-8")
        assert load_ruleset(path) == RuleConfig()

    def test_resolve_rules_still_returns_a_rule_config(self):
        assert resolve_rules(preset="classic", rules_path=None) == RuleConfig()
