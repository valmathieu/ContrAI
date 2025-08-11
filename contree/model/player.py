# Player, HumanPlayer, AiPlayer classes

from abc import ABC, abstractmethod

class Player(ABC):
    def __init__(self, name, position):
        self.name = name
        self.position = position  # 'North', 'South', 'East', 'West'
        self.hand = []  # list of Card
        self.team = None  # Will be set by Game when teams are created

    @property
    def is_human(self):
        """Returns True if this is a human player."""
        return isinstance(self, HumanPlayer)

    @abstractmethod
    def choose_bid(self, current_contract):
        pass

    @abstractmethod
    def choose_card(self, trick, contract):
        pass

class HumanPlayer(Player):
    def choose_bid(self, current_contract):
        # This method should be called by the controller via the view
        # Example: return ('Pass') or (value, suit)
        return None  # To be implemented in controller/view

    def choose_card(self, trick, contract):
        # This method should be called by the controller via the view
        return None  # To be implemented in controller/view

class AiPlayer(Player):
    def choose_bid(self, current_contract):
        # Implement AI bidding logic based on specs
        # Return ('Pass') or (value, suit)
        return 'Pass'  # Placeholder

    def choose_card(self, trick, contract):
        # Implement AI card playing logic based on specs
        # Return a Card from self.hand
        if self.hand:
            return self.hand[0]  # Placeholder: play first card
        return None
