"""Tests for the ``Team`` class.

Covers construction (incl. the two-player guard) and the string
representations. Team identity and same-side questions are not this
class's job — they live in ``test_team_side.py`` and ``test_position.py``
— so the pin here is that ``Team.name`` stays a display label and never
doubles as an identity.
"""

import pytest

from contrai_core import Hand, InvalidPlayerCountError, Position, Team, TeamSide

class DummyPlayer:
    """Dummy player class for testing purposes."""
    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.hand = Hand()

    def __eq__(self, other):
        return isinstance(other, DummyPlayer) and self.name == other.name

@pytest.fixture
def players():
    """Fixture that returns two dummy players."""
    return [
        DummyPlayer("Player1", Position.NORTH),  # type: ignore
        DummyPlayer("Player2", Position.SOUTH)   # type: ignore
    ]

@pytest.fixture
def team(players):
    """Fixture that returns a team with two players."""
    return Team("North-South", players)  # type: ignore

def test_team_initialization(players):
    """
    Test that a team is correctly initialized with name and players.
    """
    team = Team("Test Team", players)  # type: ignore
    assert team.name == "Test Team"
    assert team.players == players

def test_team_requires_exactly_two_players():
    """
    Test that creating a team with wrong number of players raises InvalidPlayerCountError.
    """
    # Test with one player
    with pytest.raises(InvalidPlayerCountError, match="Expected 2 players, got 1"):
        Team("Invalid Team", [DummyPlayer("Player1", Position.NORTH)])  # type: ignore

    # Test with three players
    with pytest.raises(InvalidPlayerCountError, match="Expected 2 players, got 3"):
        Team("Invalid Team", [  # type: ignore
            DummyPlayer("Player1", Position.NORTH),
            DummyPlayer("Player2", Position.SOUTH),
            DummyPlayer("Player3", Position.EAST)
        ])

    # Test with empty list
    with pytest.raises(InvalidPlayerCountError, match="Expected 2 players, got 0"):
        Team("Invalid Team", [])  # type: ignore

def test_name_is_a_label_not_an_identity(team):
    """
    Test that the team's name is display text and nothing more.

    Identity is ``TeamSide``, reached through the seats — the name is
    free to be reworded, so it must never be what a lookup compares.
    """
    assert team.name != TeamSide.NS
    assert {player.position.team_side for player in team.players} == {
        TeamSide.NS
    }

    renamed = Team("Nord-Sud", team.players)  # type: ignore
    assert renamed.name != team.name
    assert {player.position.team_side for player in renamed.players} == {
        TeamSide.NS
    }

def test_team_string_representation(team):
    """
    Test that string representations work correctly.
    """
    assert str(team) == "North-South: Player1 & Player2"

def test_team_repr(team):
    """
    Test that developer representation works correctly.
    """
    assert repr(team) == "Team('North-South', 2 players)"
