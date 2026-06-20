"""
Probability Engine for calculating hypergeometric distributions.

This module computes the mathematical probabilities essential for evaluating
partner support and opponent threats based on the known 8-card hand.
All calculations are suit-agnostic: what matters is whether a slot is trump
and how many cards of that slot remain unknown.
"""

import math
from typing import Literal
from src.models.hand import Hand
from src.models.deck import SuitSlot, Rank


class ProbabilityEngine:
    """
    Calculates probabilities for contrée using hypergeometric distributions.

    Attributes:
        total_unknown_cards (int): Always 24 (32 − 8 in hand).
        cards_per_player (int): Always 8.
    """

    def __init__(self, hand: Hand) -> None:
        """
        Initialize the engine with the player's known hand.

        Args:
            hand (Hand): The player's 8-card hand.
        """
        self.hand = hand
        self.total_unknown_cards: int = 24  # 32 − 8 held
        self.cards_per_player: int = 8

    # ------------------------------------------------------------------
    # Core hypergeometric primitive
    # ------------------------------------------------------------------

    def hypergeometric_prob(
        self,
        pop_size: int,
        successes_in_pop: int,
        sample_size: int,
        successes_in_sample: int,
    ) -> float:
        """
        Calculate the hypergeometric probability of exactly k successes.

        Formula: C(K, k) * C(N−K, n−k) / C(N, n)

        Args:
            pop_size (int): Total population size (N).
            successes_in_pop (int): Successes in population (K).
            sample_size (int): Draw size (n).
            successes_in_sample (int): Target successes drawn (k).

        Returns:
            float: Exact probability for this outcome.
        """
        # Guard against mathematically impossible configurations
        if successes_in_sample > successes_in_pop or successes_in_sample > sample_size:
            return 0.0
        if (sample_size - successes_in_sample) > (pop_size - successes_in_pop):
            return 0.0

        ways_success = math.comb(successes_in_pop, successes_in_sample)
        ways_failure = math.comb(
            pop_size - successes_in_pop, sample_size - successes_in_sample
        )
        total_ways = math.comb(pop_size, sample_size)

        return (ways_success * ways_failure) / total_ways

    # ------------------------------------------------------------------
    # Generic partner helpers
    # ------------------------------------------------------------------

    def prob_partner_has_specific_card(self) -> float:
        """
        P(partner holds one specific unknown card) = 8/24 = 1/3.

        Returns:
            float: Always 0.333…
        """
        return self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=1,
            sample_size=self.cards_per_player,
            successes_in_sample=1,
        )

    def prob_partner_has_at_least_one_of(self, num_target_cards: int) -> float:
        """
        P(partner holds at least one of `num_target_cards` specific unknown cards).

        Uses P(X ≥ 1) = 1 − P(X = 0).

        Args:
            num_target_cards (int): How many target cards exist among the 24 unknown.

        Returns:
            float: Probability (0–1).
        """
        if num_target_cards <= 0:
            return 0.0

        prob_none = self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=num_target_cards,
            sample_size=self.cards_per_player,
            successes_in_sample=0,
        )
        return 1.0 - prob_none

    # ------------------------------------------------------------------
    # Partner — trump support
    # ------------------------------------------------------------------

    def prob_partner_has_at_least_n_trumps(self, n: int) -> float:
        """
        P(partner holds at least n trump cards).

        Args:
            n (int): Minimum number of trump cards.

        Returns:
            float: Probability (0–1).
        """
        unknown_trumps = 8 - self.hand.count_suit(SuitSlot.TRUMP)
        # Sum over all outcomes ≥ n
        prob_fewer = sum(
            self.hypergeometric_prob(
                pop_size=self.total_unknown_cards,
                successes_in_pop=unknown_trumps,
                sample_size=self.cards_per_player,
                successes_in_sample=k,
            )
            for k in range(n)
        )
        return max(0.0, 1.0 - prob_fewer)

    def prob_partner_has_trump_ace(self) -> float:
        """
        P(partner holds the Trump Ace), given I don't hold it.

        Returns:
            float: Probability, or 0.0 if I already hold the Trump Ace.
        """
        if self.hand.has_card(Rank.ACE, SuitSlot.TRUMP):
            return 0.0
        # Exactly 1 Trump Ace is among the 24 unknown cards
        return self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=1,
            sample_size=self.cards_per_player,
            successes_in_sample=1,
        )

    # ------------------------------------------------------------------
    # Partner — non-trump aces
    # ------------------------------------------------------------------

    def prob_partner_has_ace(self, slot: SuitSlot) -> float:
        """
        P(partner holds the Ace of `slot`), given I don't hold it.

        Args:
            slot (SuitSlot): The non-trump slot to evaluate.

        Returns:
            float: Probability, or 0.0 if I already hold that Ace.
        """
        if self.hand.has_card(Rank.ACE, slot):
            return 0.0
        return self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=1,
            sample_size=self.cards_per_player,
            successes_in_sample=1,
        )

    # ------------------------------------------------------------------
    # Opponent threats
    # ------------------------------------------------------------------

    def prob_opponent_has_ace(self, slot: SuitSlot) -> float:
        """
        P(at least one opponent holds the Ace of `slot`), given I don't hold it.

        Both opponents share 16 of the 24 unknown cards.  Because only one Ace
        exists, the events for Opp1 and Opp2 are mutually exclusive, so we
        can add their individual probabilities.

        Args:
            slot (SuitSlot): The slot to evaluate.

        Returns:
            float: Probability (0–1).
        """
        if self.hand.has_card(Rank.ACE, slot):
            return 0.0

        # P(one specific opponent draws the Ace) — by symmetry both are equal
        prob_single_opp = self.hypergeometric_prob(
            pop_size=self.total_unknown_cards,
            successes_in_pop=1,
            sample_size=self.cards_per_player,
            successes_in_sample=1,
        )
        # Mutually exclusive (only one Ace): P(opp1 OR opp2) = P(opp1) + P(opp2)
        return min(1.0, 2 * prob_single_opp)

    def prob_opponent_has_both_j_and_9(self) -> float:
        """
        P(at least one opponent holds BOTH the trump Jack AND the trump Nine).

        If I hold one or both of them the probability is 0.

        Returns:
            float: Probability (0–1).
        """
        has_j = self.hand.has_card(Rank.JACK, SuitSlot.TRUMP)
        has_9 = self.hand.has_card(Rank.NINE, SuitSlot.TRUMP)

        if has_j and has_9:
            return 0.0
        if has_j or has_9:
            # Only one top trump is missing — opponent can't hold "both"
            return 0.0

        # Both J and 9 are unknown.  P(one specific opponent holds both):
        # C(2,2)*C(22,6) / C(24,8)
        ways_opp1 = math.comb(2, 2) * math.comb(22, 6)
        total_ways = math.comb(self.total_unknown_cards, self.cards_per_player)
        prob_single_opp = ways_opp1 / total_ways

        # Mutually exclusive (can't both hold the same two cards)
        return min(1.0, 2 * prob_single_opp)

    def prob_opponent_can_bid_slot(self, slot: SuitSlot) -> float:
        """
        Estimate P(at least one opponent can open ≥80 in `slot` as trump).

        Minimum condition for 80: hold J (or 9) of slot + ≥2 other cards of slot + 1 ace.
        We approximate by computing P(opponent holds the J of slot AND ≥2 other slot cards).

        Args:
            slot (SuitSlot): A non-trump slot to evaluate.

        Returns:
            float: Probability estimate (0–1).
        """
        if slot == SuitSlot.TRUMP:
            return 0.0  # Not applicable for trump slot

        my_count_in_slot = self.hand.count_suit(slot)
        unknown_in_slot = 8 - my_count_in_slot  # cards of `slot` still unseen

        has_j_slot = self.hand.has_card(Rank.JACK, slot)

        # If I hold the Jack, opponent can't use it as a trump anchor
        if has_j_slot:
            return 0.0

        # opponent needs: J (1 specific) + at least 2 others of slot
        # = at least 3 cards of `slot` total, one being the Jack
        other_slot_cards = unknown_in_slot - 1  # exclude the Jack
        unknown_other = self.total_unknown_cards - unknown_in_slot

        if other_slot_cards < 2:
            return 0.0

        # P(one opponent has J + exactly 2 other slot cards + 5 from elsewhere)
        ways_opp1 = (
            math.comb(1, 1)               # must draw the Jack
            * math.comb(other_slot_cards, 2)  # 2 other slot cards
            * math.comb(unknown_other, 5)     # 5 cards from other slots
        )
        total_ways = math.comb(self.total_unknown_cards, self.cards_per_player)
        prob_single = ways_opp1 / total_ways

        # Mutually exclusive (only one J per slot): add for two opponents
        return min(1.0, 2 * prob_single)

    def prob_opponent_threat_third_ace(self, slot: SuitSlot) -> float:
        """
        P(at least one opponent holds the Ace of `slot` + ≥2 other cards of that slot).

        Args:
            slot (SuitSlot): The slot to evaluate.

        Returns:
            float: Probability (0–1).
        """
        if self.hand.has_card(Rank.ACE, slot):
            return 0.0

        my_count = self.hand.count_suit(slot)
        unknown_in_slot = 8 - my_count
        other_slot_cards = unknown_in_slot - 1  # exclude the Ace

        if other_slot_cards < 2:
            return 0.0

        unknown_other = self.total_unknown_cards - unknown_in_slot

        ways_opp1 = (
            math.comb(1, 1)
            * math.comb(other_slot_cards, 2)
            * math.comb(unknown_other, 5)
        )
        total_ways = math.comb(self.total_unknown_cards, self.cards_per_player)
        prob_single = ways_opp1 / total_ways

        # Mutually exclusive: only one Ace per slot
        return min(1.0, 2 * prob_single)

    # ------------------------------------------------------------------
    # Point distribution
    # ------------------------------------------------------------------

    def expected_points_by_slot(
        self, player: Literal["partner", "opponents"]
    ) -> dict[SuitSlot, float]:
        """
        Expected contrée points per slot for either the partner or both opponents combined.

        Each unknown card contributes its point value weighted by the fraction of
        unknown cards that go to the player(s):
        - partner receives 8/24 = 1/3 of unknown cards on average
        - opponents combined receive 16/24 = 2/3

        Args:
            player: "partner" for the single partner, "opponents" for Opp1+Opp2 combined.

        Returns:
            dict[SuitSlot, float]: Expected points per slot.
        """
        # Share fraction: partner gets 8/24, opponents get 16/24
        share = 8 / 24 if player == "partner" else 16 / 24

        result: dict[SuitSlot, float] = {slot: 0.0 for slot in SuitSlot}

        for slot in SuitSlot:
            for rank in Rank:
                from src.models.deck import Card
                card = Card(rank, slot)
                # Only unknown cards contribute — skip cards in my hand
                if card not in self.hand.cards:
                    result[slot] += card.point_value * share

        return result
