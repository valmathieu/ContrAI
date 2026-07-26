"""Tests for the :class:`Position` seat enum.

Covers membership and the canonical seating order, the ``next`` /
``partner`` / ``opponents`` turn-order derivations, strict value parsing
(the plain constructor accepts only the exact display strings — no
case-folding, no French fallback), the French seat-name bijection the
scraper's DOM ids need, the absence of ordering support, and the
``str()`` rendering that f-strings and error contexts rely on to read as
plain seat names.
"""

import pytest

from contrai_core import Position


# ---------------------------------------------------------------------------
# Membership & canonical seating
# ---------------------------------------------------------------------------


class TestMembership:
    def test_four_members(self):
        assert len(Position) == 4

    def test_unique_values(self):
        values = [position.value for position in Position]
        assert len(set(values)) == 4

    def test_canonical_seating_order(self):
        # Definition order IS the anticlockwise turn order the auction and
        # play phases speak: N -> W -> S -> E.
        assert list(Position) == [
            Position.NORTH,
            Position.WEST,
            Position.SOUTH,
            Position.EAST,
        ]


# ---------------------------------------------------------------------------
# next — anticlockwise successor
# ---------------------------------------------------------------------------


class TestNext:
    def test_north_next_is_west(self):
        assert Position.NORTH.next is Position.WEST

    def test_full_cycle_matches_canonical_order(self):
        assert Position.NORTH.next is Position.WEST
        assert Position.WEST.next is Position.SOUTH
        assert Position.SOUTH.next is Position.EAST
        assert Position.EAST.next is Position.NORTH

    @pytest.mark.parametrize("start", list(Position))
    def test_period_is_exactly_four(self, start):
        # Four steps return to the start...
        position = start
        for _ in range(4):
            position = position.next
        assert position is start

        # ...but three steps must not (rules out a shorter sub-cycle).
        one_short = start
        for _ in range(3):
            one_short = one_short.next
        assert one_short is not start


# ---------------------------------------------------------------------------
# partner — involution, opposite seat
# ---------------------------------------------------------------------------


class TestPartner:
    @pytest.mark.parametrize("position", list(Position))
    def test_never_self(self, position):
        assert position.partner is not position

    @pytest.mark.parametrize("position", list(Position))
    def test_is_involution(self, position):
        # Partnering twice returns to the original seat.
        assert position.partner.partner is position

    def test_north_south_are_partners(self):
        assert Position.NORTH.partner is Position.SOUTH
        assert Position.SOUTH.partner is Position.NORTH

    def test_west_east_are_partners(self):
        assert Position.WEST.partner is Position.EAST
        assert Position.EAST.partner is Position.WEST

    @pytest.mark.parametrize("position", list(Position))
    def test_partner_is_next_next(self, position):
        assert position.partner is position.next.next


# ---------------------------------------------------------------------------
# opponents — the other team, completing the table
# ---------------------------------------------------------------------------


class TestOpponents:
    @pytest.mark.parametrize("position", list(Position))
    def test_is_a_pair(self, position):
        assert len(position.opponents) == 2

    @pytest.mark.parametrize("position", list(Position))
    def test_disjoint_from_self_and_partner(self, position):
        opponents = position.opponents
        assert position not in opponents
        assert position.partner not in opponents

    @pytest.mark.parametrize("position", list(Position))
    def test_completes_the_table(self, position):
        # self + partner + opponents == all four seats, each exactly once.
        assert {position, position.partner, *position.opponents} == set(Position)

    def test_north_opponents_are_west_and_east(self):
        assert Position.NORTH.opponents == (Position.WEST, Position.EAST)


# ---------------------------------------------------------------------------
# Strict value parsing — plain Enum, no case folding, no from_str helper
# ---------------------------------------------------------------------------


class TestStrictParsing:
    def test_exact_value_round_trips(self):
        assert Position("North") is Position.NORTH
        assert Position("West") is Position.WEST
        assert Position("South") is Position.SOUTH
        assert Position("East") is Position.EAST

    def test_lowercase_raises(self):
        with pytest.raises(ValueError):
            Position("north")

    def test_french_raises(self):
        with pytest.raises(ValueError):
            Position("Nord")

    def test_member_never_equals_its_string_value(self):
        # A member and its display string are different things entirely.
        for position in Position:
            assert position != position.value
            assert position.value != position


# ---------------------------------------------------------------------------
# French seat names — scraper DOM-id vocabulary
# ---------------------------------------------------------------------------


class TestFrenchNames:
    def test_french_name_values(self):
        assert Position.NORTH.french_name == "nord"
        assert Position.WEST.french_name == "ouest"
        assert Position.SOUTH.french_name == "sud"
        assert Position.EAST.french_name == "est"

    def test_french_names_are_unique(self):
        names = {position.french_name for position in Position}
        assert len(names) == 4

    @pytest.mark.parametrize("position", list(Position))
    def test_from_french_round_trips(self, position):
        assert Position.from_french(position.french_name) is position

    def test_from_french_rejects_english_lowercase(self):
        with pytest.raises(ValueError):
            Position.from_french("north")

    def test_from_french_rejects_english_titlecase(self):
        with pytest.raises(ValueError):
            Position.from_french("North")


# ---------------------------------------------------------------------------
# No ordering support — sorting is the caller's business, not the enum's
# ---------------------------------------------------------------------------


class TestNoOrdering:
    def test_positions_are_not_sortable(self):
        with pytest.raises(TypeError):
            sorted(Position)


# ---------------------------------------------------------------------------
# str() / f-string rendering
# ---------------------------------------------------------------------------


class TestStringRendering:
    def test_str_returns_value(self):
        assert str(Position.EAST) == "East"

    def test_fstring_renders_bare_seat_name(self):
        # Pinned: PlayState.apply's IllegalPlayError context — built as
        # f"{player.position} card play" in play.py — must render as the
        # plain seat name, e.g. "East card play".
        assert f"{Position.EAST} card play" == "East card play"
