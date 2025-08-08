import pytest
from contree.model.team import Team
from contree.model.player import Player

class DummyPlayer:
    """Dummy player class for testing purposes."""
    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.hand = []
    
    def __eq__(self, other):
        return isinstance(other, DummyPlayer) and self.name == other.name

@pytest.fixture
def players():
    """Fixture that returns two dummy players."""
    return [
        DummyPlayer("Player1", "North"),
        DummyPlayer("Player2", "South")
    ]

@pytest.fixture
def team(players):
    """Fixture that returns a team with two players."""
    return Team("North-South", players)

def test_team_initialization(players):
    """
    Test that a team is correctly initialized with name and players.
    """
    team = Team("Test Team", players)
    assert team.name == "Test Team"
    assert team.players == players
    assert team.total_score == 0

def test_team_requires_exactly_two_players():
    """
    Test that creating a team with wrong number of players raises ValueError.
    """
    # Test with one player
    with pytest.raises(ValueError, match="A team must have exactly 2 players"):
        Team("Invalid Team", [DummyPlayer("Player1", "North")])
    
    # Test with three players
    with pytest.raises(ValueError, match="A team must have exactly 2 players"):
        Team("Invalid Team", [
            DummyPlayer("Player1", "North"),
            DummyPlayer("Player2", "South"),
            DummyPlayer("Player3", "East")
        ])
    
    # Test with empty list
    with pytest.raises(ValueError, match="A team must have exactly 2 players"):
        Team("Invalid Team", [])

def test_add_points(team):
    """
    Test that adding points updates the team's total score.
    """
    assert team.total_score == 0
    
    team.add_points(50)
    assert team.total_score == 50
    
    team.add_points(30)
    assert team.total_score == 80
    
    # Test negative points
    team.add_points(-10)
    assert team.total_score == 70

def test_get_partner(team, players):
    """
    Test that get_partner returns the correct partner.
    """
    player1, player2 = players
    
    # Test getting partner of first player
    partner = team.get_partner(player1)
    assert partner == player2
    
    # Test getting partner of second player
    partner = team.get_partner(player2)
    assert partner == player1
    
    # Test with player not in team
    outside_player = DummyPlayer("Outside Player", "East")
    partner = team.get_partner(outside_player)
    assert partner is None

def test_contains_player(team, players):
    """
    Test that contains_player correctly identifies team membership.
    """
    player1, player2 = players
    
    # Test with players in the team
    assert team.contains_player(player1) is True
    assert team.contains_player(player2) is True
    
    # Test with player not in team
    outside_player = DummyPlayer("Outside Player", "East")
    assert team.contains_player(outside_player) is False

def test_team_string_representation(team):
    """
    Test that string representations work correctly.
    """
    expected_str = "North-South: Player1 & Player2 (0 pts)"
    assert str(team) == expected_str
    
    # Test after adding points
    team.add_points(120)
    expected_str = "North-South: Player1 & Player2 (120 pts)"
    assert str(team) == expected_str

def test_team_repr(team):
    """
    Test that developer representation works correctly.
    """
    expected_repr = "Team('North-South', 2 players, 0 pts)"
    assert repr(team) == expected_repr
    
    # Test after adding points
    team.add_points(75)
    expected_repr = "Team('North-South', 2 players, 75 pts)"
    assert repr(team) == expected_repr

def test_team_with_different_names():
    """
    Test team creation with different team names.
    """
    players = [
        DummyPlayer("Alice", "East"),
        DummyPlayer("Bob", "West")
    ]
    
    team = Team("East-West", players)
    assert team.name == "East-West"
    assert str(team) == "East-West: Alice & Bob (0 pts)"

def test_team_score_accumulation():
    """
    Test that team scores accumulate correctly over multiple rounds.
    """
    players = [
        DummyPlayer("Player A", "North"),
        DummyPlayer("Player B", "South")
    ]
    team = Team("Team Test", players)
    
    # Simulate multiple rounds of scoring
    round_scores = [80, 120, 60, 100, 90]
    expected_total = 0
    
    for score in round_scores:
        team.add_points(score)
        expected_total += score
        assert team.total_score == expected_total
    
    assert team.total_score == sum(round_scores)
