"""Pins the profile loader: strict keys, typed sections, env indirection."""

import pathlib

import pytest
from contrai_core import Position

import contrai_scraper
from contrai_scraper import ProfileError, Translator, load_profile

#: The committed schema, three levels up from the installed package.
EXAMPLE = (
    pathlib.Path(contrai_scraper.__path__[0]).parents[1] / "profile.example.toml"
)


class TestLoading:
    def test_reads_every_section(self, profile_path):
        profile = load_profile(profile_path)
        assert profile.site.locale == "xx"
        assert profile.rules.preset == "tournament"
        assert profile.wire.tokens.seats["top"] is Position.NORTH

    def test_an_unknown_key_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("[site]", "[site]\nnonsense = 1"),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="nonsense"):
            load_profile(path)

    def test_an_unknown_section_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text + "\n[nowhere]\nx = 1\n", encoding="utf-8")
        with pytest.raises(ProfileError, match="nowhere"):
            load_profile(path)

    def test_a_missing_key_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('locale = "xx"', ""), encoding="utf-8")
        with pytest.raises(ProfileError, match="locale"):
            load_profile(path)


class TestTokens:
    def test_an_incomplete_rank_map_is_refused(self, tmp_path, profile_text):
        # The separating comma goes with the entry: TOML forbids a trailing
        # comma in an inline table, and a document that does not parse would
        # exercise the decoder rather than the completeness check.
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace(', "9" = "A"', ""), encoding="utf-8")
        with pytest.raises(ProfileError, match="ranks"):
            load_profile(path)

    def test_an_unknown_rank_token_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('"9" = "A"', '"9" = "Z"'), encoding="utf-8")
        with pytest.raises(ProfileError):
            load_profile(path)


    def test_an_incomplete_suit_map_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace(', z = "C"', ""), encoding="utf-8")
        with pytest.raises(ProfileError, match="suits"):
            load_profile(path)

    def test_an_incomplete_seat_map_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace(', left = "W"', ""), encoding="utf-8")
        with pytest.raises(ProfileError, match="seats"):
            load_profile(path)

    def test_a_rotation_naming_another_seat_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('"bottom", "left"]', '"bottom", "other"]'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="seat_rotation"):
            load_profile(path)

    def test_a_third_team_letter_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('team_letters = ["X", "Y"]',
                                             'team_letters = ["X", "Y", "Z"]'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="team_letters"):
            load_profile(path)


class TestTypes:
    def test_a_wrong_type_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("slow_mo_ms = 0", 'slow_mo_ms = "fast"'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="slow_mo_ms"):
            load_profile(path)

    def test_a_string_list_must_hold_strings(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('"verb", "player"]', '"verb", 7]'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="key_fields"):
            load_profile(path)

    def test_a_table_option_must_be_a_boolean(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("opt_beta = false", 'opt_beta = "no"'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="opt_beta"):
            load_profile(path)


class TestFields:
    def test_an_unknown_logical_name_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("[wire.fields]", '[wire.fields]\nnowhere = "x"'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="nowhere"):
            load_profile(path)

    def test_a_missing_logical_name_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('left = "gone"', ""), encoding="utf-8")
        with pytest.raises(ProfileError, match="left"):
            load_profile(path)

    def test_a_path_must_be_a_string(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('left = "gone"', "left = 7"), encoding="utf-8")
        with pytest.raises(ProfileError, match="left"):
            load_profile(path)


class TestDocument:
    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ProfileError, match="cannot read"):
            load_profile(tmp_path / "absent.toml")

    def test_a_malformed_document_is_refused(self, tmp_path):
        path = tmp_path / "p.toml"
        path.write_text("[site\n", encoding="utf-8")
        with pytest.raises(ProfileError, match="not valid TOML"):
            load_profile(path)

    def test_a_bad_socket_pattern_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(
            "\n".join(
                'socket_url_pattern = "(unclosed"'
                if line.startswith("socket_url_pattern")
                else line
                for line in profile_text.splitlines()
            ),
            encoding="utf-8")
        with pytest.raises(ProfileError, match="socket_url_pattern"):
            load_profile(path)


class TestSecrets:
    def test_a_code_can_come_from_the_environment(self, tmp_path, profile_text, monkeypatch):
        monkeypatch.setenv("CONTRAI_SCRAPER_CODE", "4242")
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('verification_code = "0000"',
                                 'verification_code = "env:CONTRAI_SCRAPER_CODE"'),
            encoding="utf-8")
        assert load_profile(path).account.verification_code == "4242"

    def test_an_unset_environment_variable_is_refused(self, tmp_path, profile_text, monkeypatch):
        monkeypatch.delenv("CONTRAI_SCRAPER_CODE", raising=False)
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('verification_code = "0000"',
                                 'verification_code = "env:CONTRAI_SCRAPER_CODE"'),
            encoding="utf-8")
        with pytest.raises(ProfileError, match="CONTRAI_SCRAPER_CODE"):
            load_profile(path)


class TestSelectors:
    def test_a_single_string_and_a_candidate_list_both_load(self, profile):
        assert profile.selectors.dismiss_tutorial == "#no-thanks"
        assert profile.selectors.login_continue == ("#go", "#go-icon")

    def test_the_seat_selector_must_carry_a_placeholder(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('"#seat-{seat}"', '"#seat"'), encoding="utf-8")
        with pytest.raises(ProfileError, match="seat"):
            load_profile(path)

    def test_the_pledge_selectors_are_read(self, profile):
        assert (profile.selectors.pledge_dialog, profile.selectors.pledge_accept) == (
            "#pledge",
            "#pledge-ok",
        )

    def test_the_scoreboard_selectors_are_read(self, profile):
        assert profile.selectors.scoreboard_row == ".score-row"

    def test_the_retired_selectors_are_refused(self, tmp_path, profile_text):
        # A profile still naming the v1 table list or the exit control is out
        # of date in a way that matters: the server seats you, and the exit
        # control is unrecoverable. Refusing is how the operator finds out.
        path = tmp_path / "fixture-profile.toml"
        path.write_text(
            profile_text.replace("variant = ", 'table_row = ".t"\nvariant = '),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="table_row"):
            load_profile(path)


class TestWire:
    def test_the_resume_vocabulary_is_read(self, profile):
        assert (
            profile.wire.resume_action,
            profile.wire.resume_room_prefix,
            profile.wire.resume_param,
        ) == ("resume", "room-", "lastSeen")


class TestRecorder:
    def test_the_thresholds_are_read(self, profile):
        assert (
            profile.recorder.hop_after_rows,
            profile.recorder.stale_after_s,
            profile.recorder.health_interval_s,
            profile.recorder.snapshot_timeout_s,
        ) == (8, 180, 60, 30)

    def test_a_missing_threshold_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "fixture-profile.toml"
        path.write_text(
            profile_text.replace("stale_after_s = 180\n", ""), encoding="utf-8"
        )
        with pytest.raises(ProfileError, match="stale_after_s"):
            load_profile(path)

    def test_both_roots_name_one_directory(self, profile):
        # game_path appends games/ and raw_path appends raw/, so two roots
        # that differ put a session's log and its record in unrelated trees.
        assert profile.output.raw_root == profile.output.root


class TestSchedule:
    def test_the_window_is_read(self, profile):
        assert (profile.schedule.timezone.key, profile.schedule.active[0].end,
                profile.schedule.idle_poll_minutes) == ("Europe/Paris", 1440, 5)

    def test_a_missing_schedule_is_refused(self, tmp_path, profile_text):
        # A shift with no window would watch around the clock, which is the
        # one thing an unattended deployment must never decide for itself.
        path = tmp_path / "p.toml"
        path.write_text(_without_schedule(profile_text), encoding="utf-8")
        with pytest.raises(ProfileError, match="schedule"):
            load_profile(path)

    def test_an_empty_range_list_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('active = ["00:00-24:00"]', "active = []"),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="active"):
            load_profile(path)

    def test_a_non_positive_poll_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("idle_poll_minutes = 5",
                                             "idle_poll_minutes = 0"), encoding="utf-8")
        with pytest.raises(ProfileError, match="idle_poll_minutes"):
            load_profile(path)

    def test_a_negative_overrun_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace("max_overrun_minutes = 30",
                                             "max_overrun_minutes = -1"), encoding="utf-8")
        with pytest.raises(ProfileError, match="max_overrun_minutes"):
            load_profile(path)


def _without_schedule(text: str) -> str:
    """The fixture profile with its whole ``[schedule]`` section cut out."""

    start = text.index("[schedule]")
    return text[:start] + text[text.index("\n\n", start) + 2:]


class TestTheCommittedExample:
    def test_it_loads(self, monkeypatch):
        # ``profile.example.toml`` is the schema a user copies. A key the
        # loader has since renamed would turn every fresh profile into a load
        # error, and nothing else in the suite reads that file.
        monkeypatch.setenv("CONTRAI_SCRAPER_CODE", "0000")
        monkeypatch.setenv("CONTRAI_SCRAPER_SALT", "pepper")
        assert load_profile(EXAMPLE).rules.preset in {"classic", "tournament"}

    def test_its_seat_map_walks_the_table_the_presets_way(self, monkeypatch):
        # A user copies the seat map along with everything else, so the
        # example has to pass the rotation check its own preset implies.
        monkeypatch.setenv("CONTRAI_SCRAPER_CODE", "0000")
        monkeypatch.setenv("CONTRAI_SCRAPER_SALT", "pepper")
        assert Translator(load_profile(EXAMPLE)).rotation == (
            Position.NORTH, Position.EAST, Position.SOUTH, Position.WEST)


class TestPrivacy:
    def test_the_salt_may_be_absent(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('pseudonym_salt = "unused-in-4a"', ""),
                        encoding="utf-8")
        assert load_profile(path).privacy.pseudonym_salt is None


class TestPaths:
    def test_output_roots_resolve_against_the_profile(self, profile_path, profile):
        assert profile.output.root == (profile_path.parent / "out").resolve()


class TestPreset:
    def test_an_unknown_preset_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('preset = "tournament"', 'preset = "nope"'),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="nope"):
            load_profile(path)
