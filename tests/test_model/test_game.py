import pytest
from contree.model.game import Game
from contree.model.deck import Deck
#TODO : add test for trick number in game
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
    return Game(players) # type: ignore

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
    assert len(game.last_trick) == 0
    assert game.last_trick_winner is None
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
        Game(players) # type: ignore
    
    # Test with too many players
    players = [DummyPlayer(f"Player{i}", "North") for i in range(5)]
    with pytest.raises(InvalidPlayerCountError):
        Game(players) # type: ignore

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
        Game(players) # type: ignore

def test_players_are_sorted_by_position(players):
    """
    Test that players are sorted in the correct position order: North, West, South, East.
    """
    # Shuffle players to test sorting
    shuffled_players = [players[2], players[0], players[3], players[1]]  # Different order
    game = Game(shuffled_players) # type: ignore
    
    expected_positions = ["North", "West", "South", "East"]
    actual_positions = [player.position for player in game.players]
    assert actual_positions == expected_positions

def test_teams_are_created_correctly(game):
    """
    Test that teams are correctly formed with North-South and East-West partnerships.
    """
    ns_team = next(team for team in game.teams if team.name == "North-South")
    ew_team = next(team for team in game.teams if team.name == "East-West")

    # Check North-South team
    ns_positions = {player.position for player in ns_team.players}
    assert ns_positions == {"North", "South"}

    # Check East-West team
    ew_positions = {player.position for player in ew_team.players}
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

    # Reset deck for next round
    game.deck = Deck()
    
    game.start_new_round()
    assert game.round_number == 2

def test_start_new_round_sets_dealer_if_none(game):
    """
    Test that starting the first round sets a dealer if none exists.
    """
    assert game.dealer is None
    
    game.start_new_round()
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

def test_get_playable_cards_first_trick(game):
    """Test playable cards when no cards have been played yet."""
    game.start_new_round()
    player = game.players[0]

    playable = game.get_playable_cards(player)

    # Should be able to play any card from hand
    assert len(playable) == len(player.hand)
    assert set(playable) == set(player.hand)

def test_get_playable_cards_follow_suit(game):
    """Test playable cards when player must follow suit."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where first card is Spades
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    game.current_trick = [(game.players[0], first_card)]

    player = game.players[1]
    # Give player some spades cards
    spades_cards = [Card('8', 'Spades'), Card('9', 'Spades')]
    other_cards = [Card('10', 'Hearts'), Card('Jack', 'Diamonds')]
    player.hand = spades_cards + other_cards

    playable = game.get_playable_cards(player)

    # Should only be able to play spades cards
    assert len(playable) == 2
    assert set(playable) == set(spades_cards)

def test_get_playable_cards_must_trump(game):
    """Test playable cards when player must trump."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where first card is Spades
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    game.current_trick = [(game.players[0], first_card)]

    player = game.players[1]
    # Player has no spades but has trumps (hearts)
    trump_cards = [Card('8', 'Hearts'), Card('9', 'Hearts')]
    other_cards = [Card('10', 'Diamonds'), Card('Jack', 'Clubs')]
    player.hand = trump_cards + other_cards

    playable = game.get_playable_cards(player)

    # Should only be able to play trump cards
    assert len(playable) == 2
    assert set(playable) == set(trump_cards)

def test_get_playable_cards_must_overtrump(game):
    """Test playable cards when player must play higher trump."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where opponent has already trumped
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    opponent_trump = Card('8', 'Hearts')  # Opponent played low trump
    game.current_trick = [
        (game.players[0], first_card),
        (game.players[2], opponent_trump)  # Player[2] is opponent (different team)
    ]

    player = game.players[1]  # Same team as players[0]
    # Player has higher and lower trumps
    higher_trump = Card('Jack', 'Hearts')  # Higher trump
    lower_trump = Card('7', 'Hearts')     # Lower trump
    other_cards = [Card('10', 'Diamonds')]
    player.hand = [higher_trump, lower_trump] + other_cards

    playable = game.get_playable_cards(player)

    # Should only be able to play higher trump
    assert len(playable) == 1
    assert playable[0] == higher_trump

def test_get_playable_cards_no_overtrump_possible(game):
    """Test playable cards when player can't overtrump."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where opponent played high trump
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    opponent_trump = Card('Jack', 'Hearts')  # High trump
    game.current_trick = [
        (game.players[0], first_card),
        (game.players[2], opponent_trump)  # Opponent played high trump
    ]

    player = game.players[1]
    # Player has only lower trumps
    lower_trumps = [Card('7', 'Hearts'), Card('8', 'Hearts')]
    other_cards = [Card('10', 'Diamonds')]
    player.hand = lower_trumps + other_cards

    playable = game.get_playable_cards(player)

    # Should still play trumps even if player can't go higher
    assert len(playable) == 2
    assert set(playable) == set(lower_trumps)

def test_get_playable_cards_discard(game):
    """Test playable cards when player can only discard."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where lead suit is spades
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    game.current_trick = [(game.players[0], first_card)]

    player = game.players[1]
    # Player has no spades and no trumps
    discard_cards = [Card('10', 'Diamonds'), Card('Jack', 'Clubs')]
    player.hand = discard_cards

    playable = game.get_playable_cards(player)

    # Should be able to play any card (discard)
    assert len(playable) == 2
    assert set(playable) == set(discard_cards)

def test_get_playable_cards_partner_leading_no_trump_obligation(game):
    """Test playable cards when partner is leading and player can't follow suit - no trump obligation."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where partner (same team) is leading with Spades
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    partner = game.players[0]  # North (same team as South)
    game.current_trick = [(partner, first_card)]

    player = game.players[2]  # South (same team as North)
    # Player has no spades but has trumps and other cards
    trump_cards = [Card('8', 'Hearts'), Card('9', 'Hearts')]
    other_cards = [Card('10', 'Diamonds'), Card('Jack', 'Clubs')]
    player.hand = trump_cards + other_cards

    playable = game.get_playable_cards(player)

    # Should be able to play any card (no trump obligation when partner leads)
    assert len(playable) == 4
    assert set(playable) == set(player.hand)

def test_get_playable_cards_opponent_leading_must_trump(game):
    """Test playable cards when opponent is leading and player can't follow suit - must trump."""
    game.start_new_round()
    game.current_contract = {'suit': 'Hearts', 'value': 80}

    # Mock a trick where opponent (different team) is leading with Spades
    from contree.model.card import Card
    first_card = Card('7', 'Spades')
    opponent = game.players[1]  # East (different team from South)
    game.current_trick = [(opponent, first_card)]

    player = game.players[2]  # South
    # Player has no spades but has trumps and other cards
    trump_cards = [Card('8', 'Hearts'), Card('9', 'Hearts')]
    other_cards = [Card('10', 'Diamonds'), Card('Jack', 'Clubs')]
    player.hand = trump_cards + other_cards

    playable = game.get_playable_cards(player)

    # Should only be able to play trump cards (must trump against opponent)
    assert len(playable) == 2
    assert set(playable) == set(trump_cards)

def test_cards_returned_to_deck_after_trick(game):
    """Test that cards are returned to deck after each trick in reverse order."""
    from contree.model.card import Card
    
    # Start a round and set up a contract
    game.start_new_round()
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }
    
    # Remember initial deck size (should be 0 after dealing)
    initial_deck_size = len(game.deck.cards)
    assert initial_deck_size == 0
    
    # Mock specific cards for players to make the test predictable
    game.players[0].hand = [Card('Ace', 'Spades')]
    game.players[1].hand = [Card('King', 'Spades')]
    game.players[2].hand = [Card('Queen', 'Spades')]
    game.players[3].hand = [Card('Jack', 'Spades')]
    
    # Play one trick
    _ = game.manage_trick()
    
    # Check that deck now has 4 cards (the trick cards)
    assert len(game.deck.cards) == 4
    
    # Check that cards are added in reverse order (last played first)
    # Trick order should be: Ace, King, Queen, Jack (in that playing order)
    # Deck should have: Jack, Queen, King, Ace (reverse order)
    expected_cards = ['Jack', 'Queen', 'King', 'Ace']
    actual_cards = [card.rank for card in game.deck.cards]
    assert actual_cards == expected_cards

def test_cards_returned_to_deck_all_players_pass(game):
    """
    Test that all players' cards are returned to deck when everyone passes.
    """

    # Start a round
    game.start_new_round()
    
    # Remember initial deck size (should be 0 after dealing)
    initial_deck_size = len(game.deck.cards)
    assert initial_deck_size == 0
    
    # Verify each player has 8 cards
    for player in game.players:
        assert len(player.hand) == 8
    
    # Mock manage_bid to return None (all pass)
    original_manage_bid = game.manage_bid
    game.manage_bid = lambda view=None: None
    
    # Run the round (which will trigger the all-pass scenario)
    result = game.manage_round()
    
    # Restore original method
    game.manage_bid = original_manage_bid
    
    # Check that contract is None and message indicates all passed
    assert result['contract'] is None
    assert result['message'] == 'All players passed. Cards redistributed.'
    
    # Check that all players' hands are now empty
    for player in game.players:
        assert len(player.hand) == 0
    
    # Check that deck has all 32 cards back
    assert len(game.deck.cards) == 32


def test_multiple_tricks_card_accumulation(game):
    """
    Test that cards accumulate in deck over multiple tricks.
    """
    
    # Start a round and set up a contract
    game.start_new_round()
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }
    
    # Ensure each player has at least 2 cards for 2 tricks
    for i, player in enumerate(game.players):
        player.hand = player.hand[:2]  # Keep only first 2 cards
    
    # Play first trick
    game.manage_trick()
    assert len(game.deck.cards) == 4
    
    # Play second trick
    game.manage_trick()
    assert len(game.deck.cards) == 8
    
    # Verify no players have cards left
    for player in game.players:
        assert len(player.hand) == 0

def test_card_order_preserved_in_deck(game):
    """Test that the specific order of cards added to deck is correct."""
    from contree.model.card import Card
    
    # Start a round and set up a contract
    game.start_new_round()
    game.current_contract = {
        'player': game.players[0],
        'team': game.players[0].team,
        'value': 80,
        'suit': 'Hearts',
        'double': False,
        'redouble': False
    }
    
    # Set specific cards for each player to control the test
    specific_cards = [
        Card('7', 'Spades'),   # Player 0 (first to play)
        Card('8', 'Spades'),   # Player 1 (second to play) 
        Card('9', 'Spades'),   # Player 2 (third to play)
        Card('10', 'Spades')   # Player 3 (last to play)
    ]
    
    for i, player in enumerate(game.players):
        player.hand = [specific_cards[i]]
    
    # Clear deck to start fresh
    game.deck.cards = []
    
    # Play the trick
    game.manage_trick()
    
    # Cards should be added in reverse order: last played (10♠) first
    expected_order = ['10', '9', '8', '7']
    actual_order = [card.rank for card in game.deck.cards]
    assert actual_order == expected_order
