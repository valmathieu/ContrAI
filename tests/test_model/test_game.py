import pytest
from contree.model.game import Game
from contree.model.player import HumanPlayer, AiPlayer
from contree.model.card import Card

class DummyPlayer:
    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.hand = []

@pytest.fixture
def players():
    """
    Fixture that returns 4 positioned players for testing.
    """
    return [
        DummyPlayer("North Player", "North"),
        DummyPlayer("East Player", "East"),
        DummyPlayer("South Player", "South"),
        DummyPlayer("West Player", "West")
    ]

@pytest.fixture
def game(players):
    """
    Fixture that returns a Game instance with 4 players.
    """
    return Game(players)

def test_game_initialization(game, players):
    """
    Test that a game is correctly initialized with players, teams, and initial state.
    """
    assert len(game.players) == 4
    assert len(game.teams) == 2
    assert game.round_number == 0
    assert game.dealer is None
    assert game.current_contract is None
    assert len(game.current_trick) == 0
    assert game.deck is not None

    # Check team formation
    team_names = {team.name for team in game.teams}
    assert team_names == {"North-South", "East-West"}

def test_game_requires_exactly_four_players():
    """
    Test that creating a game with wrong number of players raises ValueError.
    """
    # Test with too few players
    players = [DummyPlayer("Player1", "North")]
    with pytest.raises(ValueError, match="Game requires exactly 4 players"):
        Game(players)

    # Test with too many players
    players = [DummyPlayer(f"Player{i}", "North") for i in range(5)]
    with pytest.raises(ValueError, match="Game requires exactly 4 players"):
        Game(players)

def test_game_requires_correct_positions():
    """
    Test that creating a game without all required positions raises ValueError.
    """
    # Missing West position
    players = [
        DummyPlayer("Player1", "North"),
        DummyPlayer("Player2", "East"),
        DummyPlayer("Player3", "South"),
        DummyPlayer("Player4", "South")  # Duplicate South, missing West
    ]

    with pytest.raises(ValueError, match="Players must have positions"):
        Game(players)

def test_players_are_sorted_by_position(players):
    """
    Test that players are sorted in the correct position order: North, West, South, East.
    """
    # Shuffle players to test sorting
    shuffled_players = [players[2], players[0], players[3], players[1]]  # Different order
    game = Game(shuffled_players)

    expected_positions = ["North", "West", "South", "East"]
    actual_positions = [player.position for player in game.players]
    assert actual_positions == expected_positions

def test_teams_are_created_correctly(game):
    """
    Test that teams are correctly formed with North-South and East-West partnerships.
    """
    north_south_team = next(team for team in game.teams if team.name == "North-South")
    east_west_team = next(team for team in game.teams if team.name == "East-West")

    # Check North-South team
    ns_positions = {player.position for player in north_south_team.players}
    assert ns_positions == {"North", "South"}

    # Check East-West team
    ew_positions = {player.position for player in east_west_team.players}
    assert ew_positions == {"East", "West"}

def test_next_dealer_anticlockwise_rotation(game):
    """
    Test that dealer rotation follows anticlockwise order: North → West → South → East.
    """
    # Set initial dealer manually to North
    game.dealer = game.players[0]  # North (index 0)
    assert game.dealer.position == "North"

    # Test the rotation sequence
    expected_sequence = ["West", "South", "East", "North"]

    for expected_position in expected_sequence:
        game.next_dealer()
        assert game.dealer.position == expected_position

def test_start_new_round_increments_round_number(game):
    """
    Test that starting a new round increments the round number.
    """
    assert game.round_number == 0

    game.start_new_round()
    assert game.round_number == 1

    game.start_new_round()
    assert game.round_number == 2

def test_start_new_round_sets_dealer_if_none(game):
    """
    Test that starting the first round sets a dealer if none exists.
    """
    assert game.dealer is None

    game.start_new_round()
    assert game.dealer is not None
    assert game.dealer in game.players

def test_start_new_round_deals_cards(game):
    """
    Test that starting a new round deals cards to all players.
    """
    game.start_new_round()

    # Each player should have 8 cards
    for player in game.players:
        assert len(player.hand) == 8

    # Deck should be empty after dealing
    assert game.deck.is_empty()

def test_start_new_round_resets_game_state(game):
    """
    Test that starting a new round resets contract and trick.
    """
    # Set some initial state
    game.current_contract = {"test": "contract"}
    game.current_trick = [Card("Hearts", "Ace")]

    game.start_new_round()

    assert game.current_contract is None
    assert len(game.current_trick) == 0

def test_check_game_over_default_target(game):
    """
    Test game over check with default target score of 1500.
    """
    assert game.check_game_over() is False

    # Set one team's score to reach target
    game.scores[game.teams[0].name] = 1500
    assert game.check_game_over() is True

def test_check_game_over_custom_target(game):
    """
    Test game over check with custom target score.
    """
    assert game.check_game_over(1000) is False

    # Set score just under target
    game.scores[game.teams[0].name] = 999
    assert game.check_game_over(1000) is False

    # Set score to reach target
    game.scores[game.teams[0].name] = 1000
    assert game.check_game_over(1000) is True
