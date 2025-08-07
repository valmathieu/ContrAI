# Game, Team, Deck classes

from .card import Card

class Game:
    def __init__(self, teams, dealer=None):
        self.teams = teams  # list of Team
        self.players = [player for team in teams for player in team.players]  # list of Player
        self.deck = Deck()  # Deck instance
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
        """
        Cuts the deck at a random position (excluding the first and last 3 cards).
        Modifies the order of the cards in the deck.
        """
        import random
        cut_index = random.randint(3, len(self.cards) - 4)
        self.cards = self.cards[cut_index:] + self.cards[:cut_index]

    def deal(self, players: list):
        """
        Deals 8 cards to each player in a 3-2-3 distribution.
        Each player receives 3 cards, then 2 cards, then 3 cards, for a total of 8 cards per player.
        The function expects exactly 4 players, each with a 'hand' attribute (list).
        Raises:
            ValueError: If the number of players is not exactly 4.
        Args:
            players (list): List of 4 player objects to deal cards to.
        """
        # Deal 8 cards to each player (3-2-3 distribution)
        if len(players) != 4:
            raise ValueError("The number of players has to be exactly 4.")
        for i, player in enumerate(players):
            player.hand.extend(self.cards[i * 3:(i * 3) + 3])
            player.hand.extend(self.cards[(i * 2) + 12:(i * 2) + 14])
            player.hand.extend(self.cards[(i * 3) + 20:(i * 3) + 23])
        self.cards.clear()
