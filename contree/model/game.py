# Game, Team, Deck classes

from .card import Card

class Game:
    def __init__(self, teams, players, deck, dealer=None):
        self.teams = teams  # list of Team
        self.players = players  # list of Player
        self.deck = deck  # Deck instance
        self.dealer = dealer  # Player instance
        self.current_contract = None
        self.current_trick = []
        self.round_number = 0
        self.scores = {team.name: 0 for team in teams}

    def start_new_round(self):
        self.round_number += 1
        self.deck.shuffle()
        self.deck.deal(self.players)
        self.current_trick = []
        self.current_contract = None

    def calculate_scores(self):
        # Placeholder: implement score calculation logic
        pass

    def check_game_over(self, target_score=1500):
        return any(score >= target_score for score in self.scores.values())

class Team:
    def __init__(self, name, players):
        self.name = name
        self.players = players  # list of Player
        self.total_score = 0

    def add_points(self, points):
        self.total_score += points

class Deck:
    def __init__(self):
        self.cards = [Card(suit, rank) for suit in Card.SUITS for rank in Card.RANKS]

    def shuffle(self):
        import random
        random.shuffle(self.cards)

    def cut(self):
        import random
        cut_index = random.randint(1, len(self.cards) - 1)
        self.cards = self.cards[cut_index:] + self.cards[:cut_index]

    def deal(self, players):
        # Deal 8 cards to each player (3-2-3 distribution)
        for i, player in enumerate(players):
            player.hand = self.cards[i*8:(i+1)*8]
