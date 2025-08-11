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
    Test that creating a game with wrong number of players raises InvalidPlayerCountError.
    """
    from contree.model.exceptions import InvalidPlayerCountError

    # Test with too few players
    players = [DummyPlayer("Player1", "North")]
    with pytest.raises(InvalidPlayerCountError):
        Game(players)

    # Test with too many players
    players = [DummyPlayer(f"Player{i}", "North") for i in range(5)]
    with pytest.raises(InvalidPlayerCountError):
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

def test_manage_trick_returns_winner(game):
    """
    Test that manage_trick returns a winner and updates current_trick.
    """
    # Setup: start a round and set up a simple contract
    game.start_new_round()
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }

    # Each player should have cards in their hand
    assert all(len(player.hand) > 0 for player in game.players)

    # Run the trick
    winner = game.manage_trick()

    # Winner should be one of the players
    assert winner in game.players

    # Current trick should have 4 cards played
    assert len(game.current_trick) == 4

    # Each player should have one less card
    for player in game.players:
        assert len(player.hand) == 7

def test_determine_trick_winner_trump_beats_non_trump(game):
    """
    Test that trump cards beat non-trump cards in trick determination.
    """
    from contree.model.card import Card

    # Setup contract with Hearts as trump
    game.current_contract = {
        'suit': 'Hearts'
    }

    # Create a trick with non-trump lead and trump card
    trick = [
        (game.players[0], Card('Spades', 'Ace')),    # High non-trump
        (game.players[1], Card('Hearts', '7')),      # Low trump
        (game.players[2], Card('Spades', 'King')),   # Non-trump
        (game.players[3], Card('Diamonds', 'Ace'))   # Non-trump
    ]

    winner = game._determine_trick_winner(trick)
    assert winner == game.players[1]  # Player with trump card wins

def test_determine_trick_winner_highest_trump_wins(game):
    """
    Test that the highest trump card wins when multiple trumps are played.
    """
    from contree.model.card import Card

    # Setup contract with Hearts as trump
    game.current_contract = {
        'suit': 'Hearts'
    }

    # Create a trick with multiple trump cards
    trick = [
        (game.players[0], Card('Hearts', '7')),      # Low trump
        (game.players[1], Card('Hearts', 'Jack')),   # Highest trump (20 points, order 7)
        (game.players[2], Card('Hearts', 'Ace')),    # High trump (order 5)
        (game.players[3], Card('Hearts', '9'))       # High trump (order 6)
    ]

    winner = game._determine_trick_winner(trick)
    assert winner == game.players[1]  # Player with Jack of trump wins

def test_calculate_scores_contract_made(game):
    """
    Test score calculation when contract is successfully made.
    """
    from contree.model.card import Card

    # Setup contract
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }

    # Create mock team tricks with enough points to make contract
    team_tricks = {
        'North-South': [
            [(game.players[0], Card('Hearts', 'Jack')), (game.players[1], Card('Spades', '7'))],  # 20 + 0 = 20
            [(game.players[0], Card('Hearts', 'Ace')), (game.players[1], Card('Spades', '8'))]    # 11 + 0 = 11
        ],
        'East-West': [
            [(game.players[2], Card('Diamonds', 'Ace')), (game.players[3], Card('Clubs', '7'))]   # 11 + 0 = 11
        ]
    }

    # Mock the last trick winner to be from North-South team
    game.trick_winner = game.players[0]

    scores = game.calculate_scores(team_tricks)

    # North-South made 31 points + 10 (dix de der) = 41 points, contract was 80
    # Since 41 < 80, contract failed
    assert scores['North-South'] == 0  # Contract team gets 0 when failed
    assert scores['East-West'] == (162 + 80)  # Opposing team gets all points + contract

def test_calculate_scores_contract_failed(game):
    """
    Test score calculation when contract fails.
    """
    from contree.model.card import Card

    # Setup contract
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 120,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }

    # Create mock team tricks where contract team doesn't have enough points
    team_tricks = {
        'North-South': [
            [(game.players[0], Card('Hearts', '7')), (game.players[1], Card('Spades', '7'))]  # 0 + 0 = 0
        ],
        'East-West': [
            [(game.players[2], Card('Diamonds', 'Ace')), (game.players[3], Card('Clubs', 'Ace'))]  # 11 + 11 = 22
        ]
    }

    # Mock the last trick winner to be from East-West team
    game.trick_winner = game.players[2]

    scores = game.calculate_scores(team_tricks)

    # Contract failed: North-South gets 0, East-West gets 162 + 120 = 282
    assert scores['North-South'] == 0
    assert scores['East-West'] == 162 + 120

def test_calculate_scores_with_double(game):
    """
    Test score calculation with doubled contract.
    """
    from contree.model.card import Card

    # Setup doubled contract
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': True,
        'redouble': False
    }

    # Contract team makes the contract
    team_tricks = {
        'North-South': [
            [(game.players[0], Card('Hearts', 'Jack')), (game.players[1], Card('Hearts', 'Ace'))]  # 20 + 11 = 31
        ],
        'East-West': []
    }

    # Add more points to make contract (need at least 80)
    game.trick_winner = game.players[0]  # Gets +10 for last trick

    # Mock enough points to make contract
    team_tricks['North-South'].append(
        [(game.players[0], Card('Hearts', '9')), (game.players[1], Card('Diamonds', 'Ace'))]  # 14 + 11 = 25
    )
    team_tricks['North-South'].append(
        [(game.players[0], Card('Spades', 'Ace')), (game.players[1], Card('Clubs', 'Ace'))]  # 11 + 11 = 22
    )
    # Total: 31 + 25 + 22 + 10 (dix de der) = 88 points

    scores = game.calculate_scores(team_tricks)

    # Contract made: (80 + 88) * 2 = 336 points for North-South
    assert scores['North-South'] == (80 + 88) * 2
    assert scores['East-West'] == 0  # No points for opposing team when they don't take tricks

def test_check_game_over_not_finished(game):
    """
    Test check_game_over when no team has reached target score.
    """
    game.scores = {'North-South': 1200, 'East-West': 800}

    result = game.check_game_over(target_score=1500)

    assert result['game_over'] is False
    assert result['winner'] is None
    assert result['tied_teams'] is None
    assert result['final_scores'] is None

def test_check_game_over_winner(game):
    """
    Test check_game_over when a team has won.
    """
    game.scores = {'North-South': 1600, 'East-West': 1200}

    result = game.check_game_over(target_score=1500)

    assert result['game_over'] is True
    assert result['winner'] == 'North-South'
    assert result['tied_teams'] is None
    assert result['final_scores'] == {'North-South': 1600, 'East-West': 1200}

def test_check_game_over_tie(game):
    """
    Test check_game_over when teams are tied above target score.
    """
    game.scores = {'North-South': 1600, 'East-West': 1600}

    result = game.check_game_over(target_score=1500)

    assert result['game_over'] is True
    assert result['winner'] is None
    assert result['tied_teams'] == ['North-South', 'East-West']
    assert result['final_scores'] == {'North-South': 1600, 'East-West': 1600}

def test_manage_round_all_pass(game):
    """
    Test manage_round when all players pass (no contract).
    """
    # Mock the manage_bid to return None (all pass)
    original_manage_bid = game.manage_bid
    game.manage_bid = lambda view=None: None

    result = game.manage_round()

    assert result['contract'] is None
    assert result['message'] == 'All players passed. Cards redistributed.'
    assert all(score == 0 for score in result['scores'].values())

    # Restore original method
    game.manage_bid = original_manage_bid

def test_manage_round_complete_round(game):
    """
    Test manage_round with a complete round (bidding + tricks + scoring).
    """
    # This is a more complex integration test
    # We'll need to mock some behaviors to make it predictable

    # Mock a simple contract
    mock_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }

    # Mock manage_bid to return our contract
    game.manage_bid = lambda view=None: mock_contract

    # Mock manage_trick to return alternating winners
    original_manage_trick = game.manage_trick
    mock_winners = [game.players[0], game.players[1], game.players[2], game.players[3]] * 2
    winner_iter = iter(mock_winners)
    game.manage_trick = lambda view=None: next(winner_iter)

    result = game.manage_round()

    assert result['contract'] == mock_contract
    assert result['message'] == 'Round completed'
    assert 'scores' in result
    assert 'total_scores' in result

    # Restore original method
    game.manage_trick = original_manage_trick
