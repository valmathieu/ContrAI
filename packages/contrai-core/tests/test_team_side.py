"""Tests for the :class:`TeamSide` enum.

Covers membership, the ``positions`` / ``opponent`` derivations and their
agreement with :class:`Position`'s seating order, the strict
no-string-equality guarantee (mirroring ``test_position.py``'s pin — a
leftover ``"North-South"`` key must miss, not resolve), and the ``str()``
rendering that serialization and log lines rely on.
"""

import pytest

from contrai_core import Position, TeamSide


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


class TestMembership:
    def test_two_members(self):
        assert len(TeamSide) == 2

    def test_members_are_ns_and_ew(self):
        assert list(TeamSide) == [TeamSide.NS, TeamSide.EW]

    def test_unique_values(self):
        values = [side.value for side in TeamSide]
        assert len(set(values)) == 2

    def test_values_are_the_short_tokens(self):
        assert TeamSide.NS.value == "NS"
        assert TeamSide.EW.value == "EW"


# ---------------------------------------------------------------------------
# positions — the seats on each side
# ---------------------------------------------------------------------------


class TestPositions:
    def test_ns_holds_north_and_south(self):
        assert TeamSide.NS.positions == (Position.NORTH, Position.SOUTH)

    def test_ew_holds_west_and_east(self):
        # Anticlockwise order (N -> W -> S -> E) puts West before East.
        assert TeamSide.EW.positions == (Position.WEST, Position.EAST)

    @pytest.mark.parametrize("side", list(TeamSide))
    def test_is_a_pair(self, side):
        assert len(side.positions) == 2

    def test_the_two_sides_partition_the_table(self):
        seated = TeamSide.NS.positions + TeamSide.EW.positions
        assert set(seated) == set(Position)
        assert len(seated) == len(Position)

    @pytest.mark.parametrize("side", list(TeamSide))
    def test_agrees_with_position_team_side(self, side):
        # The property is derived *from* Position, so the round trip is
        # the check that nothing re-encodes the pairing separately.
        for position in side.positions:
            assert position.team_side is side

    @pytest.mark.parametrize("side", list(TeamSide))
    def test_the_two_seats_are_partners(self, side):
        first, second = side.positions
        assert first.partner is second
        assert second.partner is first


# ---------------------------------------------------------------------------
# opponent — the other side
# ---------------------------------------------------------------------------


class TestOpponent:
    def test_ns_opposes_ew(self):
        assert TeamSide.NS.opponent is TeamSide.EW
        assert TeamSide.EW.opponent is TeamSide.NS

    @pytest.mark.parametrize("side", list(TeamSide))
    def test_never_self(self, side):
        assert side.opponent is not side

    @pytest.mark.parametrize("side", list(TeamSide))
    def test_is_an_involution(self, side):
        assert side.opponent.opponent is side

    @pytest.mark.parametrize("side", list(TeamSide))
    def test_opponent_seats_are_this_side_opponents(self, side):
        for position in side.positions:
            assert set(position.opponents) == set(side.opponent.positions)


# ---------------------------------------------------------------------------
# Strict parsing — the whole point is that old string keys stop matching
# ---------------------------------------------------------------------------


class TestStrictParsing:
    def test_exact_value_round_trips(self):
        assert TeamSide("NS") is TeamSide.NS
        assert TeamSide("EW") is TeamSide.EW

    def test_lowercase_raises(self):
        with pytest.raises(ValueError):
            TeamSide("ns")

    def test_legacy_display_name_raises(self):
        # The strings that used to *be* the identity are no longer it.
        with pytest.raises(ValueError):
            TeamSide("North-South")
        with pytest.raises(ValueError):
            TeamSide("East-West")

    def test_member_never_equals_its_string_value(self):
        # Not a StrEnum, on purpose: a stray string comparison has to be
        # a testable False rather than silently truthy.
        for side in TeamSide:
            assert side != side.value
            assert side.value != side

    def test_member_never_equals_a_legacy_display_name(self):
        # The pin that makes the refactor safe: a leftover
        # scores["North-South"] must miss loudly, not resolve.
        assert TeamSide.NS != "North-South"
        assert TeamSide.EW != "East-West"
        assert TeamSide.NS != "N-S"
        assert TeamSide.EW != "E-W"

    def test_is_usable_as_a_dict_key_distinct_from_strings(self):
        scores = {TeamSide.NS: 120, TeamSide.EW: 40}
        assert scores[TeamSide.NS] == 120
        assert "North-South" not in scores
        assert "NS" not in scores


# ---------------------------------------------------------------------------
# str() rendering
# ---------------------------------------------------------------------------


class TestStringRendering:
    def test_str_returns_value(self):
        assert str(TeamSide.NS) == "NS"
        assert str(TeamSide.EW) == "EW"

    def test_fstring_renders_the_bare_token(self):
        assert f"{TeamSide.EW} scores" == "EW scores"
