# Game, Team, Deck classes

class Game:
    def __init__(self, teams, deck):
        self.teams = teams
        self.deck = deck

class Team:
    def __init__(self, name, players):
        self.name = name
        self.players = players

class Deck:
    def __init__(self, cards):
        self.cards = cards
