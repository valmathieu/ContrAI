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

    def test_an_email_can_come_from_the_environment(self, tmp_path, profile_text,
                                                    monkeypatch):
        # One site profile for every account: the account itself lives in
        # the environment, beside its code.
        monkeypatch.setenv("CONTRAI_SCRAPER_EMAIL", "watcher-2@example.invalid")
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('email = "watcher@example.invalid"',
                                 'email = "env:CONTRAI_SCRAPER_EMAIL"'),
            encoding="utf-8")
        assert load_profile(path).account.email == "watcher-2@example.invalid"

    def test_an_unset_email_variable_is_refused(self, tmp_path, profile_text,
                                                monkeypatch):
        monkeypatch.delenv("CONTRAI_SCRAPER_EMAIL", raising=False)
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('email = "watcher@example.invalid"',
                                 'email = "env:CONTRAI_SCRAPER_EMAIL"'),
            encoding="utf-8")
        with pytest.raises(ProfileError, match="CONTRAI_SCRAPER_EMAIL"):
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

    def test_the_optional_rail_toggle_is_read(self, profile):
        assert profile.selectors.rail_show == "#rail-in:visible"

    def test_a_profile_naming_no_rail_toggle_loads(self, tmp_path, profile_text):
        # Optional: a site whose table controls never move away needs none.
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('rail_show = "#rail-in:visible"\n', ""),
            encoding="utf-8",
        )

        assert load_profile(path).selectors.rail_show is None

    def test_the_scoreboard_selectors_are_read(self, profile):
        assert profile.selectors.scoreboard_row == ".score-row"

    def test_the_retired_selectors_are_refused(self, tmp_path, profile_text):
        # A profile still naming the v1 table list or the exit control is out
        # of date in a way that matters: the server seats you, and the exit
        # control is unrecoverable. Refusing is how the operator finds out.
        path = tmp_path / "fixture-profile.toml"
        path.write_text(
            profile_text.replace("\nvariant = ", '\ntable_row = ".t"\nvariant = '),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="table_row"):
            load_profile(path)


#: The lobby's seven selector lines in the fixture profile, removable as one.
LOBBY_LINES = (
    'mode_new_games = "#new-games"\n'
    'lobby_variant = "#new-variant"\n'
    'lobby_tables = ".slot-list"\n'
    'lobby_back = ["#tool-back", "#tool-close"]\n'
    'lobby_layer = ".screen"\n'
    'lobby_row_tournament_class = "cup-row"\n'
    'lobby_row_hash_attr = "data-key"\n'
)


class TestLobbySelectors:
    def test_the_lobby_is_read(self, profile):
        selectors = profile.selectors
        assert (selectors.has_lobby, selectors.mode_new_games, selectors.lobby_back,
                selectors.lobby_row_hash_attr) == (
            True, "#new-games", ("#tool-back", "#tool-close"), "data-key")

    def test_a_profile_naming_no_lobby_still_loads(self, tmp_path, profile_text):
        # `run` never goes to the lobby, so today's profiles must keep loading.
        assert LOBBY_LINES in profile_text
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace(LOBBY_LINES, ""), encoding="utf-8")
        selectors = load_profile(path).selectors
        assert (selectors.has_lobby, selectors.lobby_back) == (False, None)

    def test_a_lobby_described_in_part_is_refused(self, tmp_path, profile_text):
        # Half a lobby loads cleanly and fails hours into a shift.
        path = tmp_path / "p.toml"
        path.write_text(profile_text.replace('lobby_layer = ".screen"\n', ""),
                        encoding="utf-8")
        with pytest.raises(ProfileError, match="the lobby only in part; missing: lobby_layer"):
            load_profile(path)

    def test_a_single_back_control_is_a_ranking_of_one(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('["#tool-back", "#tool-close"]', '"#tool-back"'),
            encoding="utf-8",
        )
        assert load_profile(path).selectors.lobby_back == ("#tool-back",)


#: The lobby's four field paths in the fixture profile, removable as one.
LOBBY_FIELD_LINES = (
    'lobby_hash = "key"\n'
    'lobby_seats = "chairs"\n'
    'lobby_seat_account = "acct"\n'
    'lobby_full = "ready"\n'
)
LOBBY_EVENT_LINE = 'lobby_table = "slot"\n'


def _write(tmp_path, text):
    path = tmp_path / "p.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLobbyWire:
    def test_the_lobbys_event_and_paths_are_read(self, profile):
        wire = profile.wire
        assert (wire.has_lobby, wire.events.lobby_table, wire.fields["lobby_full"],
                wire.draw_verb) == (True, "slot", "ready", "lots")

    def test_a_profile_that_reads_no_lobby_still_loads(self, tmp_path, profile_text):
        assert LOBBY_FIELD_LINES in profile_text and LOBBY_EVENT_LINE in profile_text
        text = profile_text.replace(LOBBY_FIELD_LINES, "").replace(LOBBY_EVENT_LINE, "")
        wire = load_profile(_write(tmp_path, text)).wire
        assert (wire.has_lobby, "lobby_full" in wire.fields) == (False, False)

    def test_paths_named_in_part_are_refused(self, tmp_path, profile_text):
        text = profile_text.replace('lobby_full = "ready"\n', "")
        with pytest.raises(ProfileError, match="lobby only in part; missing: lobby_full"):
            load_profile(_write(tmp_path, text))

    def test_an_event_nothing_can_read_is_refused(self, tmp_path, profile_text):
        text = profile_text.replace(LOBBY_FIELD_LINES, "")
        with pytest.raises(ProfileError, match="go together"):
            load_profile(_write(tmp_path, text))

    def test_paths_for_an_event_nobody_names_are_refused(self, tmp_path, profile_text):
        text = profile_text.replace(LOBBY_EVENT_LINE, "")
        with pytest.raises(ProfileError, match="go together"):
            load_profile(_write(tmp_path, text))

    def test_the_draw_verb_is_optional(self, tmp_path, profile_text):
        text = profile_text.replace('draw_verb = "lots"\n', "")
        assert load_profile(_write(tmp_path, text)).wire.draw_verb is None


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

    def test_a_negative_retention_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "fixture-profile.toml"
        path.write_text(
            profile_text.replace("raw_retention_days = 30", "raw_retention_days = -1"),
            encoding="utf-8",
        )
        with pytest.raises(ProfileError, match="raw_retention_days"):
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

    return _without_section(text, "[schedule]")


def _without_section(text: str, header: str) -> str:
    """The fixture profile with one whole section cut out."""

    start = text.index(header)
    return text[:start] + text[text.index("\n\n", start) + 2:]


class TestEgress:
    def test_the_gate_is_read(self, profile):
        assert (profile.egress.home_ip, profile.egress.expected_country,
                profile.egress.tunnel_interface) == ("198.51.100.1", "XX", None)

    def test_the_tunnel_interface_is_read_when_present(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('probe_country_field = "land"',
                                 'probe_country_field = "land"\ntunnel_interface = "tun0"'),
            encoding="utf-8")
        assert load_profile(path).egress.tunnel_interface == "tun0"

    def test_the_home_ip_can_come_from_the_environment(self, tmp_path, profile_text,
                                                       monkeypatch):
        # The address is the operator's own; it belongs in the box's env file,
        # never in a document that travels.
        monkeypatch.setenv("CONTRAI_HOME_IP", "198.51.100.2")
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('home_ip = "198.51.100.1"',
                                 'home_ip = "env:CONTRAI_HOME_IP"'), encoding="utf-8")
        assert load_profile(path).egress.home_ip == "198.51.100.2"

    def test_a_home_ip_that_is_not_an_address_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('home_ip = "198.51.100.1"', 'home_ip = "home"'),
            encoding="utf-8")
        with pytest.raises(ProfileError, match="home_ip"):
            load_profile(path)

    def test_a_country_that_is_not_two_letters_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('expected_country = "XX"',
                                 'expected_country = "France"'), encoding="utf-8")
        with pytest.raises(ProfileError, match="expected_country"):
            load_profile(path)

    def test_a_missing_egress_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(_without_section(profile_text, "[egress]"), encoding="utf-8")
        with pytest.raises(ProfileError, match="egress"):
            load_profile(path)


class TestTheCommittedExample:
    def test_it_loads(self, monkeypatch):
        # ``profile.example.toml`` is the schema a user copies. A key the
        # loader has since renamed would turn every fresh profile into a load
        # error, and nothing else in the suite reads that file.
        monkeypatch.setenv("CONTRAI_SCRAPER_CODE", "0000")
        monkeypatch.setenv("CONTRAI_SCRAPER_SALT", "pepper")
        monkeypatch.setenv("CONTRAI_HOME_IP", "198.51.100.1")
        assert load_profile(EXAMPLE).rules.preset in {"classic", "tournament"}

    def test_its_seat_map_walks_the_table_the_presets_way(self, monkeypatch):
        # A user copies the seat map along with everything else, so the
        # example has to pass the rotation check its own preset implies.
        monkeypatch.setenv("CONTRAI_SCRAPER_CODE", "0000")
        monkeypatch.setenv("CONTRAI_SCRAPER_SALT", "pepper")
        monkeypatch.setenv("CONTRAI_HOME_IP", "198.51.100.1")
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
