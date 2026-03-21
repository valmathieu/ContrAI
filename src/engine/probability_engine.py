"""
Probability Engine for calculating hypergeometric distributions.

This module computes the mathematical probabilities essential for evaluating
partner support and opponent threats based on the known 8-card hand.
"""

import math
from src.models.hand import Hand
from src.models.deck import Suit, Rank

class ProbabilityEngine:
    """
    Calculates probabilities for La Contrée using hypergeometric distributions.
    
    Attributes:
        total_unknown_cards (int): Always 24 (32 cards in deck - 8 in our hand).
        cards_per_player (int): Always 8.
    """
    
    def __init__(self, hand: Hand):
        """
        Initialize the engine with the player's known hand.
        
        Args:
            hand (Hand): The player's 8-card hand.
        """
        self.hand = hand
        self.total_unknown_cards = 24
        self.cards_per_player = 8

    def hypergeometric_prob(self, pop_size: int, successes_in_pop: int, sample_size: int, successes_in_sample: int) -> float:
        """
        Calculate the hypergeometric probability of exactly 'k' successes.
        
        Formula: (comb(K, k) * comb(N - K, n - k)) / comb(N, n)
        Where:
            N = pop_size
            K = successes_in_pop
            n = sample_size
            k = successes_in_sample
            
        Args:
            pop_size (int): Total number of items (N).
            successes_in_pop (int): Total number of successes in population (K).
            sample_size (int): Number of items drawn (n).
            successes_in_sample (int): Target number of successes drawn (k).
            
        Returns:
            float: The probability.
        """
        # Ensure mathematically valid inputs
        if successes_in_sample > successes_in_pop or successes_in_sample > sample_size:
            return 0.0
        if (sample_size - successes_in_sample) > (pop_size - successes_in_pop):
            return 0.0
            
        ways_to_pick_successes = math.comb(successes_in_pop, successes_in_sample)
        ways_to_pick_failures = math.comb(pop_size - successes_in_pop, sample_size - successes_in_sample)
        total_ways = math.comb(pop_size, sample_size)
        
        return (ways_to_pick_successes * ways_to_pick_failures) / total_ways

    def prob_partner_has_specific_card(self) -> float:
        """
        Calculate the probability that the partner holds one specific missing card
        (e.g., a specific Ace or the missing Jack of Trumps).
        
        Since there are 24 unknown cards and the partner holds 8 of them,
        the probability that any specific unknown card is in the partner's hand
        is exactly 8/24 = 1/3 (33.3%).
        
        Returns:
            float: Probability of partner having the specific card.
        """
        return self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=1,
            sample_size=self.cards_per_player,
            successes_in_sample=1
        )

    def prob_partner_has_at_least_one_of(self, num_target_cards: int) -> float:
        """
        Calculate probability that partner holds at least one of 'num_target_cards' specific cards.
        Useful when looking for e.g. either the 9 or Jack of trumps.
        
        P(X >= 1) = 1 - P(X = 0)
        
        Args:
            num_target_cards (int): How many specific target cards we are looking for.
            
        Returns:
            float: The probability.
        """
        if num_target_cards == 0:
            return 0.0
            
        prob_none = self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=num_target_cards,
            sample_size=self.cards_per_player,
            successes_in_sample=0
        )
        return 1.0 - prob_none

    def prob_opponent_threat_third_ace(self, suit: Suit) -> float:
        """
        Calculate the probability that AT LEAST ONE opponent holds exactly a "Third Ace"
        (The Ace of the specified suit + exactly 2 other cards of that suit).
        
        This assumes we do NOT hold the Ace. If we hold the Ace, the threat is 0.
        
        Args:
            suit (Suit): The suit to evaluate.
            
        Returns:
            float: Probability of at least one opponent holding the 3rd Ace.
        """
        # If we have the Ace, there is no threat.
        if self.hand.has_card(Rank.ACE, suit):
            return 0.0
            
        my_cards_in_suit = self.hand.count_suit(suit)
        unknown_cards_in_suit = 8 - my_cards_in_suit
        
        # We need the opponent to hold exactly 3 cards of the suit, and one MUST be the Ace.
        # So they must hold the Ace (1 specific card) + 2 out of the remaining (unknown_cards_in_suit - 1).
        available_other_suit_cards = unknown_cards_in_suit - 1 
        
        # If there aren't at least 2 other cards of the suit remaining, a "Third Ace" is impossible.
        if available_other_suit_cards < 2:
            return 0.0
            
        # Total other cards not of this suit that are unknown
        unknown_cards_other_suits = self.total_unknown_cards - unknown_cards_in_suit
        
        # Probability for ONE specific opponent (e.g., Opponent 1)
        # Ways to pick the Ace: comb(1, 1) = 1
        # Ways to pick 2 other cards of the suit: comb(available_other_suit_cards, 2)
        # Ways to pick the remaining 5 cards from other suits: comb(unknown_cards_other_suits, 5)
        ways_opp1_gets_third_ace = (
            math.comb(1, 1) * 
            math.comb(available_other_suit_cards, 2) * 
            math.comb(unknown_cards_other_suits, 5)
        )
        total_ways = math.comb(self.total_unknown_cards, self.cards_per_player)
        
        prob_opp1 = ways_opp1_gets_third_ace / total_ways
        
        # Since there is only 1 Ace, Opponent 1 and Opponent 2 cannot BOTH have the Third Ace.
        # Thus, the events are mutually exclusive. P(Opp1 OR Opp2) = P(Opp1) + P(Opp2)
        # By symmetry, P(Opp1) == P(Opp2).
        return 2 * prob_opp1
