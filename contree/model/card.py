# Card class: represents a playing card

class Card:
    """
    Represents a playing card for the game of Contree.

    Each card has a suit and a rank, and provides methods to get its point value and order,
    depending on whether it is a trump card or not.

    Attributes:
        suit (str): The suit of the card ('Spades', 'Hearts', 'Diamonds', 'Clubs').
        rank (str): The rank of the card ('7', '8', '9', '10', 'Jack', 'Queen', 'King', 'Ace').
        points_normal (int): The point value of the card in a non-trump suit.
        points_trump (int): The point value of the card in the trump suit.
        order_normal (int): The order of the card in a non-trump suit.
        order_trump (int): The order of the card in the trump suit.

    Methods:
        __str__(): Returns a string representation of the card with suit symbol.
        __repr__(): Returns a string representation for debugging.
        get_points(trump_suit=None): Returns the point value of the card, considering trump.
        get_order(trump_suit=None): Returns the order of the card, considering trump.
    """
    SUITS = ['Spades', 'Hearts', 'Diamonds', 'Clubs']
    RANKS = ['7', '8', '9', 'Jack', 'Queen', 'King', '10', 'Ace']
    # Normal points (non-trump)
    NORMAL_POINTS = {
        '7': 0,
        '8': 0,
        '9': 0,
        'Jack': 2,
        'Queen': 3,
        'King': 4,
        '10': 10,
        'Ace': 11
    }
    # Trump points
    TRUMP_POINTS = {
        '7': 0,
        '8': 0,
        '9': 14,
        'Jack': 20,
        'Queen': 3,
        'King': 4,
        '10': 10,
        'Ace': 11
    }
    # Normal order (for trick-taking)
    NORMAL_ORDER = {
        '7': 0,
        '8': 1,
        '9': 2,
        'Jack': 3,
        'Queen': 4,
        'King': 5,
        '10': 6,
        'Ace': 7
    }
    # Trump order
    TRUMP_ORDER = {
        '7': 0,
        '8': 1,
        'Queen': 2,
        'King': 3,
        '10': 4,
        'Ace': 5,
        '9': 6,
        'Jack': 7
    }

    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
        self.points_normal = Card.NORMAL_POINTS[rank]
        self.points_trump = Card.TRUMP_POINTS[rank]
        self.order_normal = Card.NORMAL_ORDER[rank]
        self.order_trump = Card.TRUMP_ORDER[rank]

    def __str__(self):
        suit_symbols = {
            'Spades': '♠',
            'Hearts': '♥',
            'Diamonds': '♦',
            'Clubs': '♣'
        }
        return f"{self.rank}{suit_symbols[self.suit]}"

    def __repr__(self):
        return f"Card('{self.suit}', '{self.rank}')"

    def get_points(self, trump_suit=None):
        if trump_suit and self.suit == trump_suit:
            return self.points_trump
        return self.points_normal

    def get_order(self, trump_suit=None):
        if trump_suit and self.suit == trump_suit:
            return self.order_trump
        return self.order_normal
