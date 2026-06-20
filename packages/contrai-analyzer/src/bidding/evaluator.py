"""
Bidding Decision Matrix Evaluator.

This module evaluates a player's hand against a truth table of contrée bidding
rules to suggest the optimal opening bid for the trump slot, and estimates the
probability that an opponent can open in each non-trump slot.
"""

from typing import Optional
from dataclasses import dataclass
from src.models.hand import Hand
from src.models.deck import SuitSlot, Rank
from src.engine.probability_engine import ProbabilityEngine


@dataclass
class BidSuggestion:
    """
    Represents a suggested bid.

    Attributes:
        value (int): The bid amount (80–160).
        suit (SuitSlot): The proposed trump slot.
        reasoning (str): Human-readable explanation of the conditions met.
    """

    value: int
    suit: SuitSlot
    reasoning: str


class BiddingEvaluator:
    """Evaluates a hand to determine the best possible bid and opponent risks."""

    def __init__(self, hand: Hand) -> None:
        """
        Initialize the evaluator.

        Args:
            hand (Hand): The player's 8-card hand.
        """
        self.hand = hand
        self._engine = ProbabilityEngine(hand)

    def evaluate(self) -> list[BidSuggestion]:
        """
        Evaluate the hand for SuitSlot.TRUMP and return valid bid suggestions.

        Trump is always the TRUMP slot — the user has already declared it.
        Returns a list with at most one suggestion (the highest matching bid level).

        Returns:
            list[BidSuggestion]: Suggested bids sorted by value (highest first).
        """
        suggestion = self._evaluate_suit(SuitSlot.TRUMP)
        return [suggestion] if suggestion else []

    def _evaluate_suit(self, trump_suit: SuitSlot) -> Optional[BidSuggestion]:
        """
        Evaluate the hand assuming `trump_suit` is trump.

        Applies the truth table from 160 down to 80, returning the first match.

        Args:
            trump_suit (SuitSlot): The slot to treat as trump.

        Returns:
            Optional[BidSuggestion]: Highest valid bid for this slot, or None.
        """
        trumps = self.hand.count_suit(trump_suit)
        total_aces = self.hand.count_rank(Rank.ACE)

        has_j      = self.hand.has_card(Rank.JACK,  trump_suit)
        has_9      = self.hand.has_card(Rank.NINE,  trump_suit)
        has_a_trump = self.hand.has_card(Rank.ACE,   trump_suit)
        has_k_trump = self.hand.has_card(Rank.KING,  trump_suit)
        has_q_trump = self.hand.has_card(Rank.QUEEN, trump_suit)

        j_xor_9 = has_j ^ has_9
        j_and_9 = has_j and has_9
        belote   = has_k_trump and has_q_trump

        # Count tens in non-singleton suits (protected tens)
        tens_not_singleton = sum(
            1
            for s in SuitSlot
            if self.hand.has_card(Rank.TEN, s) and self.hand.count_suit(s) >= 2
        )

        # 160: J ∧ 9 ∧ Trump Ace | ≥5 Trumps | ≥3 Aces | ≥2 Protected Tens | Belote
        if j_and_9 and has_a_trump and trumps >= 5 and total_aces >= 3 and tens_not_singleton >= 2 and belote:
            return BidSuggestion(
                value=160, suit=trump_suit,
                reasoning="J ∧ 9 ∧ Trump Ace, 5+ Trumps, 3+ Aces, 2+ Protected Tens, Belote."
            )

        # 150: J ∧ 9 | ≥4 Trumps | ≥3 Aces | ≥1 Protected Ten | Belote
        if j_and_9 and trumps >= 4 and total_aces >= 3 and tens_not_singleton >= 1 and belote:
            return BidSuggestion(
                value=150, suit=trump_suit,
                reasoning="J ∧ 9, 4+ Trumps, 3+ Aces, 1+ Protected Ten, Belote."
            )

        # 140: J ⊕ 9 | ≥4 Trumps | ≥3 Aces | ≥1 Protected Ten | Belote
        if j_xor_9 and trumps >= 4 and total_aces >= 3 and tens_not_singleton >= 1 and belote:
            return BidSuggestion(
                value=140, suit=trump_suit,
                reasoning="J ⊕ 9, 4+ Trumps, 3+ Aces, 1+ Protected Ten, Belote."
            )

        # 130: J ∧ 9 | ≥3 Trumps | ≥3 Aces
        if j_and_9 and trumps >= 3 and total_aces >= 3:
            return BidSuggestion(
                value=130, suit=trump_suit,
                reasoning="J ∧ 9, 3+ Trumps, 3+ Aces."
            )

        # 120: J ⊕ 9 | ≥3 Trumps | ≥3 Aces
        if j_xor_9 and trumps >= 3 and total_aces >= 3:
            return BidSuggestion(
                value=120, suit=trump_suit,
                reasoning="J ⊕ 9, 3+ Trumps, 3+ Aces."
            )

        # 110: J ∧ 9 | ≥3 Trumps | ≥2 Aces
        if j_and_9 and trumps >= 3 and total_aces >= 2:
            return BidSuggestion(
                value=110, suit=trump_suit,
                reasoning="J ∧ 9, 3+ Trumps, 2+ Aces."
            )

        # 100: J ⊕ 9 | ≥3 Trumps | ≥2 Aces
        if j_xor_9 and trumps >= 3 and total_aces >= 2:
            return BidSuggestion(
                value=100, suit=trump_suit,
                reasoning="J ⊕ 9, 3+ Trumps, 2+ Aces."
            )

        # 90: J ∧ 9 | ≥3 Trumps | ≥1 Ace
        if j_and_9 and trumps >= 3 and total_aces >= 1:
            return BidSuggestion(
                value=90, suit=trump_suit,
                reasoning="J ∧ 9, 3+ Trumps, 1+ Ace."
            )

        # 80: J ⊕ 9 | ≥3 Trumps | ≥1 Ace
        if j_xor_9 and trumps >= 3 and total_aces >= 1:
            return BidSuggestion(
                value=80, suit=trump_suit,
                reasoning="J ⊕ 9, 3+ Trumps, 1+ Ace."
            )

        return None

    def opponent_bidding_risk(self) -> tuple[SuitSlot, float]:
        """
        Identify the non-trump slot where opponents are most likely to open.

        Applies prob_opponent_can_bid_slot() to the three non-trump slots and
        returns the slot with the highest risk probability.

        Returns:
            tuple[SuitSlot, float]: (riskiest slot, probability estimate).
        """
        non_trump_slots = [s for s in SuitSlot if s != SuitSlot.TRUMP]
        risks = {
            slot: self._engine.prob_opponent_can_bid_slot(slot)
            for slot in non_trump_slots
        }
        riskiest = max(risks, key=lambda s: risks[s])
        return riskiest, risks[riskiest]
