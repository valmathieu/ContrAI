"""Tests for BasePlayer data class."""

from contrai_core import BasePlayer, Hand, Team


def test_base_player_initialization():
    """A BasePlayer is created with name and position; hand and team start empty."""
    player = BasePlayer("Corentin", "North")
    assert player.name == "Corentin"
    assert player.position == "North"
    assert isinstance(player.hand, Hand)
    assert len(player.hand) == 0
    assert player.team is None


def test_base_player_hand_is_mutable():
    """The hand attribute can be appended to and cleared in place."""
    player = BasePlayer("Samuel", "South")
    player.hand.append("placeholder_card")
    assert len(player.hand) == 1
    player.hand.clear()
    assert len(player.hand) == 0


def test_base_player_team_settable():
    """The team attribute can be assigned after init."""
    player = BasePlayer("Nabil", "East")
    partner = BasePlayer("Alexandre", "West")
    team = Team("EW", [player, partner])
    player.team = team
    assert player.team == "team_obj"


def test_two_players_have_independent_hands():
    """Each BasePlayer instance gets its own Hand (no shared mutable default)."""
    p1 = BasePlayer("P1", "North")
    p2 = BasePlayer("P2", "South")
    p1.hand.append("card_for_p1")
    assert len(p2.hand) == 0


def test_all_table_positions_construct():
    """The four documented positions are all valid construction arguments.

    AiPlayer._get_partner_position relies on exactly these four strings,
    so any drift here would silently break partner lookup.
    """
    for position in ("North", "South", "East", "West"):
        player = BasePlayer("Hugo", position)
        assert player.position == position
