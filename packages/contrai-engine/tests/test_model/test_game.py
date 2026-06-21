import pytest
from contrai_engine.model import game as game_module
from contrai_engine.model.game import Game
from contrai_core.deck import Deck
from contrai_core.exceptions import InvalidPlayerCountError
from contrai_core.card import Card

class DummyPlayer:
    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.hand = []


class FakeRound:
    """Test double standing in for :class:`Round` in ``manage_round`` tests.

    The real ``Round`` runs a full bidding/trick/scoring lifecycle that needs
    AI players with hands. ``Game.manage_round`` only orchestrates those calls,
    so we swap in this double to drive the orchestration deterministically and
    record which lifecycle hooks fired.
    """

    # Per-test configuration: what bidding resolves to and the scores each
    # outcome reports back to the Game.
    bidding_contract = None
    play_scores: dict[str, int] = {}
    failed_scores: dict[str, int] = {}

    def __init__(self, players_order, dealer, deck, round_number):
        self.players_order = players_order
        self.dealer = dealer
        self.deck = deck
        self.round_number = round_number
        self.calls: list[str] = []

    def deal_cards(self):
        self.calls.append("deal_cards")

    def manage_bidding(self, view=None):
        self.calls.append("manage_bidding")
        return self.bidding_contract

    def play_all_tricks(self, view=None):
        self.calls.append("play_all_tricks")
        return {}

    def calculate_round_scores(self):
        self.calls.append("calculate_round_scores")
        return dict(self.play_scores)

    def handle_failed_contract(self):
        self.calls.append("handle_failed_contract")
        return dict(self.failed_scores)


class RecordingView:
    """View double recording the lifecycle callbacks ``manage_round`` fires."""

    def __init__(self):
        self.dealt = []
        self.redealt = []

    def on_round_dealt(self, round_obj):
        self.dealt.append(round_obj)

    def on_all_pass_redeal(self, round_obj):
        self.redealt.append(round_obj)

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
    assert game.deck is not None
    
    # Check team formation
    team_names = {team.name for team in game.teams}
    assert team_names == {"East-West","North-South"}

def test_game_requires_exactly_four_players():
    """
    Test that creating a game with wrong number of players raises InvalidPlayerCountError.
    """
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

def test_check_game_over_not_finished(game):
    """
    Test check_game_over when no team has reached target score.
    """
    game.scores = {'North-South': 1200, 'East-West': 800}

    result = game.check_game_over(target_score=1500)

    assert result['game_over'] is False
    assert result['winner'] is None
    assert result['tied_teams'] is None
    assert result['final_scores'] == {'North-South': 1200, 'East-West': 800}

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


def test_check_game_over_default_target_score(game):
    """
    Test that check_game_over uses 1500 as the default target score.
    """
    game.scores = {'North-South': 1500, 'East-West': 900}

    result = game.check_game_over()

    assert result['game_over'] is True
    assert result['winner'] == 'North-South'


def test_next_dealer_picks_random_when_none(game, monkeypatch):
    """
    Test that the first call to next_dealer picks a player at random.
    """
    assert game.dealer is None

    # Force the "random" choice to be deterministic for the assertion.
    monkeypatch.setattr(game_module.random, 'choice', lambda seq: seq[2])

    game.next_dealer()

    assert game.dealer is game.players[2]


def test_set_players_order_starts_after_dealer(game):
    """
    Test that the playing order begins with the player after the dealer and
    proceeds anticlockwise (North, West, South, East).
    """
    # Players are sorted as [North, West, South, East]; dealer is North.
    game.dealer = game.players[0]

    game.set_players_order()

    positions = [player.position for player in game.players_order]
    assert positions == ["West", "South", "East", "North"]


def test_set_players_order_wraps_around(game):
    """
    Test that the playing order wraps past the end of the player list when the
    dealer sits last in position order.
    """
    # Dealer is East (last in [North, West, South, East]).
    game.dealer = game.players[3]

    game.set_players_order()

    positions = [player.position for player in game.players_order]
    assert positions == ["North", "West", "South", "East"]


def test_start_new_round_shuffles_first_round_then_cuts(game, monkeypatch):
    """
    Test that the deck is shuffled on the first round and cut on later rounds.
    """
    calls = []
    monkeypatch.setattr(game.deck, 'shuffle', lambda: calls.append('shuffle'))
    monkeypatch.setattr(game.deck, 'cut', lambda: calls.append('cut'))
    # Swap in the round double so dealing does not exhaust the (un-shuffled) deck.
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    game.start_new_round()
    assert calls == ['shuffle']

    game.start_new_round()
    assert calls == ['shuffle', 'cut']


def test_manage_round_completed(game, monkeypatch):
    """
    Test the happy path of manage_round: a contract is won, per-round scores are
    accumulated into the totals, and the deal callback fires on the view.
    """
    contract = object()
    FakeRound.bidding_contract = contract
    FakeRound.play_scores = {'North-South': 160, 'East-West': 0}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    view = RecordingView()
    result = game.manage_round(view)

    assert result['contract'] is contract
    assert result['scores'] == {'North-South': 160, 'East-West': 0}
    assert result['total_scores'] == {'North-South': 160, 'East-West': 0}
    assert result['message'] == 'Round completed'
    assert game.current_contract is contract

    # The full lifecycle ran, in order.
    assert game.current_round.calls == [
        'deal_cards', 'manage_bidding', 'play_all_tricks', 'calculate_round_scores'
    ]
    # The view was told a fresh round was dealt, and never asked to redeal.
    assert view.dealt == [game.current_round]
    assert view.redealt == []


def test_manage_round_accumulates_scores_across_rounds(game, monkeypatch):
    """
    Test that manage_round adds each round's scores onto the running totals.
    """
    FakeRound.bidding_contract = object()
    FakeRound.play_scores = {'North-South': 90, 'East-West': 70}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    first = game.manage_round()
    second = game.manage_round()

    assert first['total_scores'] == {'North-South': 90, 'East-West': 70}
    assert second['total_scores'] == {'North-South': 180, 'East-West': 140}
    # The returned per-round scores are the round's own, not the running total.
    assert second['scores'] == {'North-South': 90, 'East-West': 70}


def test_manage_round_all_pass_redeals(game, monkeypatch):
    """
    Test the all-pass path of manage_round: with no contract, tricks are never
    played, the failed-contract scores are returned, and the redeal callback
    fires on the view.
    """
    FakeRound.bidding_contract = None
    FakeRound.failed_scores = {'North-South': 0, 'East-West': 0}
    monkeypatch.setattr(game_module, 'Round', FakeRound)

    view = RecordingView()
    result = game.manage_round(view)

    assert result['contract'] is None
    assert result['scores'] == {'North-South': 0, 'East-West': 0}
    assert result['message'] == 'All players passed. Cards redistributed.'

    # No trick play or scoring happened; the failed-contract branch ran instead.
    assert 'play_all_tricks' not in game.current_round.calls
    assert 'handle_failed_contract' in game.current_round.calls
    # The view was asked to redeal, and the totals were left untouched.
    assert view.redealt == [game.current_round]
    assert game.scores == {'North-South': 0, 'East-West': 0}
