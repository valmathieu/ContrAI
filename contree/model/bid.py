# Bid classes for the "contree" card game.
# This module contains the bid system with polymorphic bid types.

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .player import Player

class Bid(ABC):
    """
    Abstract base class for all bid types in contree.

    Provides common interface for bid validation, comparison, and precedence rules.
    """

    def __init__(self, player: 'Player'):
        """
        Initialize a bid with the player who made it.

        Args:
            player: The player making this bid
        """
        self.player = player

    @abstractmethod
    def is_valid_after(self, previous_bids: list) -> bool:
        """
        Check if this bid is valid given the previous bids.

        Args:
            previous_bids: List of previous Bid objects

        Returns:
            True if this bid is valid, False otherwise
        """
        pass

    @abstractmethod
    def can_be_doubled(self) -> bool:
        """
        Check if this bid can be doubled by opponents.

        Returns:
            True if this bid can be doubled, False otherwise
        """
        pass

    @abstractmethod
    def __str__(self) -> str:
        """String representation of the bid."""
        pass

    @abstractmethod
    def __eq__(self, other) -> bool:
        """Equality comparison between bids."""
        pass

class PassBid(Bid):
    """Represents a pass bid."""

    def is_valid_after(self, previous_bids: list) -> bool:
        """Pass is always valid."""
        return True

    def can_be_doubled(self) -> bool:
        """Pass cannot be doubled."""
        return False

    def __str__(self) -> str:
        return "Pass"

    def __eq__(self, other) -> bool:
        return isinstance(other, PassBid)

class ContractBid(Bid):
    """Represents a contract bid with value and trump suit."""

    # Valid contract values in contree
    VALID_VALUES = [80, 90, 100, 110, 120, 130, 140, 150, 160, 'Capot']
    VALID_SUITS = ['Spades', 'Hearts', 'Diamonds', 'Clubs', 'NoTrump']

    def __init__(self, player: 'Player', value: int or str, suit: str):
        """
        Initialize a contract bid.

        Args:
            player: The player making this bid
            value: Contract value (80-160 or 'Capot')
            suit: Trump suit

        Raises:
            ValueError: If value or suit is invalid
        """
        super().__init__(player)

        if value not in self.VALID_VALUES:
            raise ValueError(f"Invalid contract value: {value}. Must be one of {self.VALID_VALUES}")

        if suit not in self.VALID_SUITS:
            raise ValueError(f"Invalid trump suit: {suit}. Must be one of {self.VALID_SUITS}")

        self.value = value
        self.suit = suit

    def is_valid_after(self, previous_bids: list) -> bool:
        """
        Contract bid must be higher than the last contract bid.

        Args:
            previous_bids: List of previous Bid objects

        Returns:
            True if this bid is higher than previous contract bids
        """
        # Find the last contract bid
        last_contract = None
        for bid in reversed(previous_bids):
            if isinstance(bid, ContractBid):
                last_contract = bid
                break

        if last_contract is None:
            return True  # First contract bid is always valid

        # Capot is always higher than any numeric bid
        if self.value == 'Capot':
            return True

        if last_contract.value == 'Capot':
            return False  # Cannot bid higher than Capot

        # Compare numeric values
        return self.value > last_contract.value

    def can_be_doubled(self) -> bool:
        """Contract bids can be doubled."""
        return True

    def get_numeric_value(self) -> int:
        """
        Get the numeric value for comparison purposes.

        Returns:
            Numeric value (Capot = 250 for comparison)
        """
        return 250 if self.value == 'Capot' else self.value

    def __str__(self) -> str:
        return f"{self.value} {self.suit}"

    def __eq__(self, other) -> bool:
        return (isinstance(other, ContractBid) and
                self.value == other.value and
                self.suit == other.suit)

    def __gt__(self, other) -> bool:
        """Greater than comparison for contract bids."""
        if not isinstance(other, ContractBid):
            return False
        return self.get_numeric_value() > other.get_numeric_value()

class DoubleBid(Bid):
    """Represents a double bid."""

    def is_valid_after(self, previous_bids: list) -> bool:
        """
        Double is valid if:
        1. There's a contract bid that hasn't been doubled yet
        2. The doubling player is not from the contracting team
        3. No passes have occurred since the last contract bid

        Args:
            previous_bids: List of previous Bid objects

        Returns:
            True if double is valid, False otherwise
        """
        if not previous_bids:
            return False

        # Find the last contract bid and check if there have been doubles/redoubles
        last_contract = None
        has_double = False
        passes_since_contract = 0

        for bid in reversed(previous_bids):
            if isinstance(bid, ContractBid):
                last_contract = bid
                break
            elif isinstance(bid, DoubleBid):
                has_double = True
            elif isinstance(bid, RedoubleBid):
                return False  # Cannot double after redouble
            elif isinstance(bid, PassBid):
                passes_since_contract += 1

        if last_contract is None or has_double:
            return False

        # Cannot double if from the same team as the contractor
        if last_contract.player.team == self.player.team:
            return False

        # Cannot double if there have been passes since the contract
        if passes_since_contract > 0:
            return False

        return True

    def can_be_doubled(self) -> bool:
        """Double cannot be doubled (but can be redoubled)."""
        return False

    def __str__(self) -> str:
        return "Double"

    def __eq__(self, other) -> bool:
        return isinstance(other, DoubleBid)

class RedoubleBid(Bid):
    """Represents a redouble bid."""

    def is_valid_after(self, previous_bids: list) -> bool:
        """
        Redouble is valid if:
        1. There's a double bid that hasn't been redoubled yet
        2. The redoubling player is from the contracting team
        3. No passes have occurred since the double

        Args:
            previous_bids: List of previous Bid objects

        Returns:
            True if redouble is valid, False otherwise
        """
        if not previous_bids:
            return False

        # Find the contract and double bids
        contract_player = None
        has_double = False
        has_redouble = False
        passes_since_double = 0

        for bid in reversed(previous_bids):
            if isinstance(bid, RedoubleBid):
                has_redouble = True
                break
            elif isinstance(bid, DoubleBid):
                has_double = True
                break
            elif isinstance(bid, PassBid):
                passes_since_double += 1
            elif isinstance(bid, ContractBid):
                contract_player = bid.player

        if not has_double or has_redouble or contract_player is None:
            return False

        # Can only redouble if from the same team as the contractor
        if contract_player.team != self.player.team:
            return False

        # Cannot redouble if there have been passes since the double
        if passes_since_double > 0:
            return False

        return True

    def can_be_doubled(self) -> bool:
        """Redouble cannot be doubled."""
        return False

    def __str__(self) -> str:
        return "Redouble"

    def __eq__(self, other) -> bool:
        return isinstance(other, RedoubleBid)

class BidValidator:
    """
    Utility class for validating bids and managing bid sequences.
    """

    @staticmethod
    def is_bid_valid(bid: Bid, previous_bids: list) -> bool:
        """
        Validate if a bid is valid given the previous bids.

        Args:
            bid: The bid to validate
            previous_bids: List of previous Bid objects

        Returns:
            True if the bid is valid, False otherwise
        """
        return bid.is_valid_after(previous_bids)

    @staticmethod
    def get_last_contract(bids: list) -> Optional[ContractBid]:
        """
        Get the last contract bid from a list of bids.

        Args:
            bids: List of Bid objects

        Returns:
            Last ContractBid or None if no contract exists
        """
        for bid in reversed(bids):
            if isinstance(bid, ContractBid):
                return bid
        return None

    @staticmethod
    def has_double(bids: list) -> bool:
        """
        Check if there's a double in the bid sequence after the last contract.

        Args:
            bids: List of Bid objects

        Returns:
            True if there's an active double, False otherwise
        """
        contract_found = False
        for bid in reversed(bids):
            if isinstance(bid, ContractBid):
                contract_found = True
                break
            elif isinstance(bid, DoubleBid):
                return True
        return False

    @staticmethod
    def has_redouble(bids: list) -> bool:
        """
        Check if there's a redouble in the bid sequence after the last double.

        Args:
            bids: List of Bid objects

        Returns:
            True if there's an active redouble, False otherwise
        """
        double_found = False
        for bid in reversed(bids):
            if isinstance(bid, DoubleBid):
                double_found = True
                break
            elif isinstance(bid, RedoubleBid):
                return True
        return False

    @staticmethod
    def count_passes_after_last_action(bids: list) -> int:
        """
        Count the number of passes since the last non-pass bid.

        Args:
            bids: List of Bid objects

        Returns:
            Number of consecutive passes at the end of the sequence
        """
        count = 0
        for bid in reversed(bids):
            if isinstance(bid, PassBid):
                count += 1
            else:
                break
        return count
