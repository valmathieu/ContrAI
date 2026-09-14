"""Pins the vocabulary layer, including the rotation the seat map must preserve."""

import pytest
from contrai_core import PRESETS, Card, Position, Rank, Suit, TeamSide, TurnDirection

from contrai_scraper import ParseError, ProfileError, Translator, load_profile


class TestCards:
    def test_a_card_glyph_becomes_a_card(self, profile):
        assert Translator(profile).card("5w") == Card(Suit.SPADES, Rank.TEN)

    def test_an_unknown_glyph_is_refused(self, profile):
        # The map was validated complete at load, so an unrecognised glyph
        # means the *site* said something new — a parse failure, not a
        # profile defect. The two have different fixes (decision J).
        with pytest.raises(ParseError):
            Translator(profile).card("1q")

    def test_a_two_character_rank_is_not_mis_split(self, profile):
        # The rank is everything but the last character; a fixed offset
        # mis-parses every two-character rank in the deck.
        assert Translator(profile).card("5z") == Card(Suit.CLUBS, Rank.TEN)

    def test_an_empty_glyph_is_refused(self, profile):
        with pytest.raises(ParseError):
            Translator(profile).card("")


class TestRotation:
    def test_the_seat_map_walks_the_table_the_presets_way(self, profile):
        # The invariant, not the names: walking the site's own rotation
        # through the seat map must walk core's seats one step at a time, in
        # the direction the profile's preset plays. A map with its side seats
        # crossed passes every spot check and still turns the table
        # backwards — every trick winner and every legality check silently
        # wrong.
        translator = Translator(profile)
        seats = translator.rotation
        direction = PRESETS[profile.rules.preset].turn_direction
        assert direction is TurnDirection.CLOCKWISE
        assert all(
            a.next_in(direction) is b for a, b in zip(seats, seats[1:] + seats[:1])
        )

    def test_a_crossed_seat_map_is_refused(self, tmp_path, profile_text):
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('right = "E"', 'right = "W"')
                        .replace('left = "W"', 'left = "E"'),
            encoding="utf-8")
        with pytest.raises(ProfileError, match="rotation"):
            Translator(load_profile(path))

    def test_the_direction_comes_from_the_preset(self, tmp_path, profile_text):
        # The same crossed map is right for a table that plays anticlockwise:
        # the check follows the ruleset rather than one fixed way round.
        path = tmp_path / "p.toml"
        path.write_text(
            profile_text.replace('right = "E"', 'right = "W"')
                        .replace('left = "W"', 'left = "E"')
                        .replace('preset = "tournament"', 'preset = "classic"'),
            encoding="utf-8")
        assert Translator(load_profile(path)).rotation == (
            Position.NORTH, Position.WEST, Position.SOUTH, Position.EAST)

    def test_a_seat_name_reads_back(self, profile):
        translator = Translator(profile)
        assert translator.seat_name(Position.NORTH) == "top"

    def test_an_unknown_seat_name_is_refused(self, profile):
        with pytest.raises(ParseError):
            Translator(profile).position("nowhere")


class TestSides:
    def test_a_team_letter_resolves_through_the_seat_that_holds_it(self, profile):
        translator = Translator(profile)
        seat_of_letter = {"X": Position.SOUTH, "Y": Position.WEST}
        assert translator.side("X", seat_of_letter) is TeamSide.NS
        assert translator.side("Y", seat_of_letter) is TeamSide.EW

    def test_an_unheld_letter_is_refused(self, profile):
        with pytest.raises(ParseError):
            Translator(profile).side("Z", {"X": Position.SOUTH})


class TestBids:
    def test_a_numeric_value_passes_through(self, profile):
        assert Translator(profile).contract_value(110) == 110

    def test_a_numeric_string_reads_as_a_number(self, profile):
        assert Translator(profile).contract_value("110") == 110

    def test_the_slam_tokens_become_slam_levels(self, profile):
        from contrai_core import SlamLevel
        translator = Translator(profile)
        assert translator.contract_value("BIG") is SlamLevel.SLAM
        assert translator.contract_value("BIGGER") is SlamLevel.SOLO_SLAM

    def test_an_unknown_value_token_is_refused(self, profile):
        with pytest.raises(ParseError):
            Translator(profile).contract_value("HUGE")

    def test_a_suit_word_and_a_suit_glyph_both_read(self, profile):
        # The live events spell a trump as a glyph, the score rows as a word.
        translator = Translator(profile)
        assert translator.contract_suit("w") is Suit.SPADES
        assert translator.contract_suit("wood") is Suit.SPADES

    def test_an_unknown_trump_token_is_refused(self, profile):
        # The observed tables bid neither no trump nor all trump, so the
        # fixture profile names no token for them and one would be news.
        with pytest.raises(ParseError):
            Translator(profile).contract_suit("q")


class TestFields:
    def test_a_dotted_path_walks_the_payload(self, profile):
        translator = Translator(profile)
        payload = {"table": {"id": "t1", "cup": True}}
        assert translator.field(payload, "table_id") == "t1"

    def test_a_missing_path_reads_as_none(self, profile):
        assert Translator(profile).field({}, "table_id") is None

    def test_an_unknown_logical_name_is_a_programming_error(self, profile):
        with pytest.raises(KeyError):
            Translator(profile).field({}, "not_a_field")
