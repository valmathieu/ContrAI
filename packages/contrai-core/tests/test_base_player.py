"""Tests for BasePlayer data class."""

from contrai_core import Hand
from contrai_core.player import BasePlayer


def test_base_player_initialization():
    """A BasePlayer is created with name and position; hand and team start empty."""
    player = BasePlayer("Alice", "North")
    assert player.name == "Alice"
    assert player.position == "North"
    assert isinstance(player.hand, Hand)
    assert len(player.hand) == 0
    assert player.team is None


def test_base_player_hand_is_mutable():
    """The hand attribute can be appended to and cleared in place."""
    player = BasePlayer("Bob", "South")
    player.hand.append("placeholder_card")
    assert len(player.hand) == 1
    player.hand.clear()
    assert len(player.hand) == 0


def test_base_player_team_settable():
    """The team attribute can be assigned after init."""
    player = BasePlayer("Carol", "East")
    player.team = "team_obj"  # type: ignore[assignment]
    assert player.team == "team_obj"


def test_two_players_have_independent_hands():
    """Each BasePlayer instance gets its own Hand (no shared mutable default)."""
    p1 = BasePlayer("P1", "North")
    p2 = BasePlayer("P2", "South")
    p1.hand.append("card_for_p1")
    assert len(p2.hand) == 0
