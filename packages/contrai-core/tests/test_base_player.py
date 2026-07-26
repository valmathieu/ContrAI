"""Tests for BasePlayer data class."""

from contrai_core import BasePlayer, Hand, Position, Team


def test_base_player_initialization():
    """A BasePlayer is created with name and position; hand and team start empty."""
    player = BasePlayer("Corentin", Position.NORTH)
    assert player.name == "Corentin"
    assert player.position is Position.NORTH
    assert isinstance(player.hand, Hand)
    assert len(player.hand) == 0
    assert player.team is None


def test_base_player_hand_is_mutable():
    """The hand attribute can be appended to and cleared in place."""
    player = BasePlayer("Samuel", Position.SOUTH)
    player.hand.append("placeholder_card")
    assert len(player.hand) == 1
    player.hand.clear()
    assert len(player.hand) == 0


def test_base_player_team_settable():
    """The team attribute can be assigned after init."""
    player = BasePlayer("Nabil", Position.EAST)
    partner = BasePlayer("Alexandre", Position.WEST)
    team = Team("EW", [player, partner])
    player.team = team
    assert player.team is team


def test_two_players_have_independent_hands():
    """Each BasePlayer instance gets its own Hand (no shared mutable default)."""
    p1 = BasePlayer("P1", Position.NORTH)
    p2 = BasePlayer("P2", Position.SOUTH)
    p1.hand.append("card_for_p1")
    assert len(p2.hand) == 0


def test_all_table_positions_construct():
    """Every Position member is a valid construction argument.

    BasePlayer places no narrower restriction on the seat than "is a
    Position member" — this pins that all four are equally constructible.
    """
    for position in Position:
        player = BasePlayer("Hugo", position)
        assert player.position is position
