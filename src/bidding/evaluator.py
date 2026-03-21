"""
Bidding Decision Matrix Evaluator.

This module evaluates a player's hand against a truth table of Contrée bidding
rules to suggest the optimal opening bid.
"""

from typing import Optional
from dataclasses import dataclass
from src.models.hand import Hand
from src.models.deck import Suit, Rank

@dataclass
class BidSuggestion:
    """
    Represents a suggested bid.
    
    Attributes:
        value (int): The bid amount (e.g., 80, 90, 160).
        suit (Suit): The proposed trump suit.
        reasoning (str): Textual explanation of why the bid was chosen.
    """
    value: int
    suit: Suit
    reasoning: str

class BiddingEvaluator:
    """Evaluates a hand to determine the best possible bid."""
    
    def __init__(self, hand: Hand):
        self.hand = hand

    def evaluate(self) -> list[BidSuggestion]:
        """
        Evaluate the hand for all possible trump suits and return the valid bids.
        
        Returns:
            list[BidSuggestion]: A list of suggested bids, sorted by highest value.
        """
        suggestions = []
        
        for suit in Suit:
            suggestion = self._evaluate_suit(suit)
            if suggestion:
                suggestions.append(suggestion)
                
        # Sort by highest bid value first
        suggestions.sort(key=lambda x: x.value, reverse=True)
        return suggestions

    def _evaluate_suit(self, trump_suit: Suit) -> Optional[BidSuggestion]:
        """
        Evaluate the hand assuming a specific suit is trump.
        
        Args:
            trump_suit (Suit): The assumed trump suit.
            
        Returns:
            Optional[BidSuggestion]: The highest valid bid for this suit, or None.
        """
        trumps = self.hand.count_suit(trump_suit)
        total_aces = self.hand.count_rank(Rank.ACE)
        
        has_j = self.hand.has_card(Rank.JACK, trump_suit)
        has_9 = self.hand.has_card(Rank.NINE, trump_suit)
        has_a_trump = self.hand.has_card(Rank.ACE, trump_suit)
        has_k_trump = self.hand.has_card(Rank.KING, trump_suit)
        has_q_trump = self.hand.has_card(Rank.QUEEN, trump_suit)
        
        j_xor_9 = has_j ^ has_9
        j_and_9 = has_j and has_9
        belote = has_k_trump and has_q_trump
        
        # Calculate '10s not singleton'
        tens_not_singleton = 0
        for s in Suit:
            if self.hand.has_card(Rank.TEN, s) and self.hand.count_suit(s) >= 2:
                tens_not_singleton += 1

        # Evaluate from highest (160) to lowest (80)
        
        # 160: V ∧ 9 ∧ Trump Ace | Min 5 Trumps | 3 Aces | 2 Tens (not singleton) | Belote
        if j_and_9 and has_a_trump and trumps >= 5 and total_aces >= 3 and tens_not_singleton >= 2 and belote:
            return BidSuggestion(
                value=160, suit=trump_suit,
                reasoning="V ∧ 9 ∧ Trump Ace, 5+ Trumps, 3+ Aces, 2+ Protected Tens, Belote."
            )
            
        # 150: V ∧ 9 | Min 4 Trumps | 3 Aces | 1 Ten (not singleton) | Belote
        if j_and_9 and trumps >= 4 and total_aces >= 3 and tens_not_singleton >= 1 and belote:
            return BidSuggestion(
                value=150, suit=trump_suit,
                reasoning="V ∧ 9, 4+ Trumps, 3+ Aces, 1+ Protected Ten, Belote."
            )

        # 140: V ⊕ 9 | Min 4 Trumps | 3 Aces | 1 Ten (not singleton) | Belote
        if j_xor_9 and trumps >= 4 and total_aces >= 3 and tens_not_singleton >= 1 and belote:
            return BidSuggestion(
                value=140, suit=trump_suit,
                reasoning="V ⊕ 9, 4+ Trumps, 3+ Aces, 1+ Protected Ten, Belote."
            )

        # 130: V ∧ 9 | Min 3 Trumps | 3 Aces
        if j_and_9 and trumps >= 3 and total_aces >= 3:
            return BidSuggestion(
                value=130, suit=trump_suit,
                reasoning="V ∧ 9, 3+ Trumps, 3+ Aces."
            )

        # 120: V ⊕ 9 | Min 3 Trumps | 3 Aces
        if j_xor_9 and trumps >= 3 and total_aces >= 3:
            return BidSuggestion(
                value=120, suit=trump_suit,
                reasoning="V ⊕ 9, 3+ Trumps, 3+ Aces."
            )

        # 110: V ∧ 9 | Min 3 Trumps | 2 Aces
        if j_and_9 and trumps >= 3 and total_aces >= 2:
            return BidSuggestion(
                value=110, suit=trump_suit,
                reasoning="V ∧ 9, 3+ Trumps, 2+ Aces."
            )

        # 100: V ⊕ 9 | Min 3 Trumps | 2 Aces
        if j_xor_9 and trumps >= 3 and total_aces >= 2:
            return BidSuggestion(
                value=100, suit=trump_suit,
                reasoning="V ⊕ 9, 3+ Trumps, 2+ Aces."
            )

        # 90: V ∧ 9 | Min 3 Trumps | 1 Ace
        if j_and_9 and trumps >= 3 and total_aces >= 1:
            return BidSuggestion(
                value=90, suit=trump_suit,
                reasoning="V ∧ 9, 3+ Trumps, 1+ Ace."
            )

        # 80: V ⊕ 9 | Min 3 Trumps | 1 Ace
        if j_xor_9 and trumps >= 3 and total_aces >= 1:
            return BidSuggestion(
                value=80, suit=trump_suit,
                reasoning="V ⊕ 9, 3+ Trumps, 1+ Ace."
            )

        return None
