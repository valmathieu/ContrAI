# Card class: represents a playing card

class Card:
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
