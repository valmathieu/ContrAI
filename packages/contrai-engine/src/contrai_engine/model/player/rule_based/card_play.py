"""Expert rule-based card-play strategy (see the package docstring)."""

from contrai_core.card import Card
from contrai_core.play import PlayObservation
from contrai_core.trick import current_winner
from contrai_core.types import Rank, Suit

from ..strategy import CardPlayStrategy, PlayerStateMixin


class RuleBasedCardPlayStrategy(CardPlayStrategy, PlayerStateMixin):
    """Expert card-play policy.

    Stateless between calls: every decision is a pure function of the
    frozen :class:`~contrai_core.PlayObservation` it is handed. The card
    tracking the rules need — which cards have fallen and which seats are
    known void in trump — is *derived* from the observation's public trick
    history on each turn (see :meth:`_derive_tracking`), never carried
    across calls or rounds. Decides which card to play from the trick
    state, the contract, and what has fallen.
    """

    def choose_card(self, observation: PlayObservation) -> Card:
        """Choose a card to play based on the expert card-play rules.

        Args:
            observation: The frozen play-phase view for this seat — its
                hand, legal cards, the contract, and the public trick
                history.

        Returns:
            The chosen :class:`Card`, drawn from
            ``observation.legal_cards``.
        """

        # Rebuild the fallen-card map and the trump-void set from the
        # public history before deciding; the trick-reading helpers below
        # consume them.
        fallen, voids = self._derive_tracking(observation)

        if not observation.current_trick:
            # First to play this trick. Trick 0 with nothing played yet is
            # the opening lead; any later trick is a fresh lead with
            # history behind it.
            if observation.trick_number == 0:
                return self._play_opening_card(observation)
            return self._play_leading_card(observation, fallen, voids)

        # Someone has already played this trick — we are following.
        return self._play_following_card(observation, fallen)

    def _derive_tracking(
        self, observation: PlayObservation
    ) -> tuple[dict[Suit, set], set]:
        """Rebuild fallen-card and trump-void tracking from public history.

        Replays every play in ``(*completed_tricks, current_trick)``
        chronologically and reconstructs, for each play, exactly the
        information a per-card tracker accumulates:

        - **Fallen cards**: every played card is recorded under its suit —
          own plays included — so ``fallen[suit]`` plus the seat's own
          holding plus the still-unseen cards always sum to 8 per suit.
        - **Voids in trump**: a seat that fails to follow the led suit and
          does not trump has proven it holds no trump, but only when it was
          *compelled* to. The compulsion is judged against the trick state
          **before** the play lands — the master among the plays strictly
          earlier in the same trick. A seat discarding while its own
          partner is already master was free to (the partner-master
          exemption), so that discard proves nothing. On a trump lead there
          is no exemption: holding trump forces playing it, so any
          off-trump card there is always a void.

        The pre-play winner must be read from the plays *before* this one,
        not after: a discard whose partner becomes master only through a
        later play in the same trick was still compelled at decision time,
        and evaluating the winner one play too late would silently hide the
        void.

        Args:
            observation: The play-phase view whose public history is
                replayed.

        Returns:
            A ``(fallen, voids)`` pair — ``fallen`` maps each card suit to
            its set of fallen ranks; ``voids`` is the set of players known
            to hold no trump.
        """

        trump_suit = observation.trump_suit
        fallen: dict[Suit, set] = {
            Suit.SPADES: set(),
            Suit.HEARTS: set(),
            Suit.DIAMONDS: set(),
            Suit.CLUBS: set(),
        }
        voids: set = set()

        for trick in (*observation.completed_tricks, observation.current_trick):
            for index, (player, card) in enumerate(trick):
                # Record the fallen card — happens for every play, whatever
                # it proves about voids.
                fallen[card.suit].add(card.rank)

                # Reconstruct the pre-play master: the winner among the
                # plays strictly earlier in this trick — the state the seat
                # decided against.
                prior = current_winner(list(trick[:index]), trump_suit)
                led_suit = trick[0].card.suit
                partner_was_master = (
                    prior is not None and prior.team == player.team
                )

                # Trump led: holding trump forces playing it (no
                # partner-master exemption), so a non-trump card always
                # proves the void.
                if led_suit == trump_suit:
                    if card.suit != trump_suit:
                        voids.add(player)
                    continue
                # Non-trump led: a discard behind a master partner is
                # voluntary and proves nothing.
                if partner_was_master:
                    continue
                if card.suit not in (led_suit, trump_suit):
                    voids.add(player)

        return fallen, voids

    def _play_opening_card(self, observation: PlayObservation) -> Card:
        """Play the very first card of the round."""

        contract = observation.contract
        playable_cards = observation.legal_cards
        hand = observation.hand
        trump_suit = observation.trump_suit

        if contract and contract.player.team == self.team:
            # Our team has the contract - play the strongest trump
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                sorted_trumps = sorted(
                    trump_cards, key=lambda c: c.get_order(trump_suit), reverse=True
                )
                if sorted_trumps[0].rank == Rank.NINE and len(sorted_trumps) > 1:
                    # Avoid playing 9 first
                    return sorted_trumps[1]
                return sorted_trumps[0]
        else:
            # Opponents have contract - play an ace if we have one
            aces = [c for c in playable_cards if c.rank == Rank.ACE]
            if aces:
                # Play ace from the shortest suit
                return min(aces, key=lambda c: self._count_suit(hand, c.suit))

        # Default: play the lowest value card (excluding trump unless only trumps available)
        non_trump_cards = (
            [c for c in playable_cards if c.suit != trump_suit]
            if trump_suit
            else playable_cards
        )

        if not non_trump_cards:
            # Only trump cards available, use all playable cards
            cards_to_consider = playable_cards
        else:
            # Use non-trump cards
            cards_to_consider = non_trump_cards

        # Find cards with minimum points value
        min_points = min(c.get_points(trump_suit) for c in cards_to_consider)
        lowest_value_cards = [
            c for c in cards_to_consider if c.get_points(trump_suit) == min_points
        ]

        # If multiple cards with same lowest value, choose randomly
        return lowest_value_cards[0]

    def _play_leading_card(
        self, observation: PlayObservation, fallen: dict[Suit, set], voids: set
    ) -> Card:
        """Play when leading subsequent tricks."""

        contract = observation.contract
        playable_cards = observation.legal_cards
        hand = observation.hand
        trump_suit = observation.trump_suit

        # If the team has the contract and opponents might still have
        # trump, play the strongest trump
        if (
            contract
            and contract.player.team == self.team
            and self._opponents_might_have_trump(trump_suit, fallen, voids, hand)
        ):
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # TODO: exclude trump from logic if we know opponents have no trump left
        # No trump left with opponents - play ace from the longest suit
        aces = [c for c in playable_cards if c.rank == Rank.ACE]
        if aces:
            return max(aces, key=lambda c: self._count_suit(hand, c.suit))

        # Play master card from the longest suit
        master_cards = [c for c in playable_cards if self._is_master_card(c, trump_suit, fallen)]
        if master_cards:
            return max(master_cards, key=lambda c: self._count_suit(hand, c.suit))

        # Default: play the lowest value card (excluding trump unless only trumps available)
        non_trump_cards = (
            [c for c in playable_cards if c.suit != trump_suit]
            if trump_suit
            else playable_cards
        )

        if not non_trump_cards:
            # Only trump cards available, use all playable cards
            cards_to_consider = playable_cards
        else:
            # Use non-trump cards
            cards_to_consider = non_trump_cards

        # Find cards with minimum points value
        min_points = min(c.get_points(trump_suit) for c in cards_to_consider)
        lowest_value_cards = [
            c for c in cards_to_consider if c.get_points(trump_suit) == min_points
        ]

        # If multiple cards with same lowest value, choose randomly
        return lowest_value_cards[0]

    def _play_following_card(
        self, observation: PlayObservation, fallen: dict[Suit, set]
    ) -> Card:
        """Strategy when not first to play."""

        if self._is_team_winning_trick(observation.current_trick):
            return self._play_when_team_winning(observation, fallen)
        return self._play_when_team_losing(observation, fallen)

    def _play_when_team_winning(
        self, observation: PlayObservation, fallen: dict[Suit, set]
    ) -> Card:
        """Play when our team is currently winning the trick.

        Partner already secures the trick, so the goal is to add value
        (high-points cards) to the pile WITHOUT wasting trumps:

        1. Follow suit if able — pile the highest-points lead-suit card
           on partner's win.
        2. Cannot follow suit → discard a NON-TRUMP card. Don't dump
           trumps onto a trick the partner has already locked down.
           Prefer non-master cards (preserve cards that can still win
           their suit later); within the candidate set, pick the
           highest-points to maximize this trick's value.
        3. Hand has nothing but trumps → forced to play one. Use the
           lowest trump so we don't waste the Jack or 9.
        """
        trump_suit = observation.trump_suit
        led_suit = observation.led_suit
        playable_cards = observation.legal_cards

        # 1. Follow suit if able.
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            return max(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # 2. Discard a non-trump card.
        non_trump_cards = [
            c for c in playable_cards if c.suit != trump_suit
        ]
        if non_trump_cards:
            non_master_non_trump = [
                c for c in non_trump_cards
                if not self._is_master_card(c, trump_suit, fallen)
            ]
            candidates = non_master_non_trump or non_trump_cards
            return max(candidates, key=lambda c: c.get_points(trump_suit))

        # 3. Only trumps in hand — dump the lowest one.
        if playable_cards:
            return min(playable_cards, key=lambda c: c.get_order(trump_suit))
        return playable_cards[0]

    def _play_when_team_losing(
        self, observation: PlayObservation, fallen: dict[Suit, set]
    ) -> Card:
        """Play when opponents are currently winning the trick."""

        trump_suit = observation.trump_suit
        led_suit = observation.led_suit
        playable_cards = observation.legal_cards
        plays = observation.current_trick
        current_best = self._get_strongest_card_in_trick(plays, trump_suit)

        # Try to follow suit
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            # Try to beat the current best card
            stronger_cards = [c for c in same_suit_cards
                             if self._is_stronger_card(c, current_best, trump_suit)]
            if stronger_cards:
                return max(stronger_cards, key=lambda c: c.get_points(trump_suit))
            # Can't beat - play the lowest card
            return min(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # Can't follow suit - try to trump
        if trump_suit and led_suit != trump_suit:
            trump_cards = [c for c in playable_cards if c.suit == trump_suit]
            if trump_cards:
                # Trump with the lowest trump that can win
                winning_trumps = [c for c in trump_cards
                                if self._can_trump_win(c, plays, trump_suit)]
                if winning_trumps:
                    return min(winning_trumps, key=lambda c: c.get_order(trump_suit))

        # Can't follow or trump - discard lowest from the shortest suit (excluding masters)
        non_master_cards = [
            c for c in playable_cards if not self._is_master_card(c, trump_suit, fallen)
        ]
        if non_master_cards:
            return min(non_master_cards, key=lambda c: (
                self._count_suit(observation.hand, c.suit),
                c.get_points(trump_suit)
            ))

        return playable_cards[0]

    @staticmethod
    def _count_suit(hand, suit: Suit) -> int:
        """Count the cards of ``suit`` in the observing seat's own hand.

        Args:
            hand: The observer's hand, from ``observation.hand``.
            suit: The suit to count.

        Returns:
            The number of cards in ``hand`` whose suit is ``suit``.
        """

        return sum(1 for card in hand if card.suit == suit)

    def _opponents_might_have_trump(
        self, trump_suit: Suit, fallen: dict[Suit, set], voids: set, hand
    ) -> bool:
        """Check if opponents might still have trump cards.

        Two knowledge sources, both derived from the observation's public
        trick history:

        1. **Counting** — 8 trumps exist; once every trump outside our
           own hand has fallen, nobody else holds one.
        2. **Void inference** — a contrée table has exactly two
           opponents. When both are known void (they were compelled to
           trump but couldn't), any unseen trumps sit in partner's hand,
           so pulling them helps nobody.

        Args:
            trump_suit: The current trump suit.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
            voids: The set of players known void in trump.
            hand: The observing seat's own hand (``observation.hand``).

        Returns:
            True if at least one opponent might still hold a trump.
        """

        # Counting: 8 trumps total; unseen = 8 - fallen - in our hand.
        trump_fallen = len(fallen.get(trump_suit, set()))
        trump_in_hand = self._count_suit(hand, trump_suit)
        if trump_fallen >= (8 - trump_in_hand):
            return False

        # Void inference: a contrée table has exactly two opponents. When both
        # are known void, any unseen trumps sit in partner's hand — pulling
        # them helps nobody. (`is not` — Team has no __eq__, identity is it.)
        opponents_void = {
            p for p in voids if p.team is not self.team
        }
        return len(opponents_void) < 2

    # TODO: replace trump_suit with a boolean is_trump parameter
    def _is_master_card(self, card, trump_suit, fallen: dict[Suit, set]) -> bool:
        """Check if a card is currently the master (highest remaining) in its suit.

        Args:
            card: The candidate card.
            trump_suit: The current trump suit.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
        """

        # Get fallen cards in this suit
        suit_fallen = fallen.get(card.suit, set())

        # Get all ranks higher than this card's rank
        higher_ranks = self._get_higher_ranks(card.rank, card.suit, trump_suit)

        # Check if all higher cards have fallen
        return all(rank in suit_fallen for rank in higher_ranks)

    @staticmethod
    def _get_higher_ranks(rank, suit, trump_suit):
        """Get all ranks higher than the given rank in the suit."""

        if suit == trump_suit:
            # Trump order: 7, 8, Queen, King, 10, Ace, 9, Jack
            trump_order = [
                Rank.SEVEN, Rank.EIGHT, Rank.QUEEN, Rank.KING,
                Rank.TEN, Rank.ACE, Rank.NINE, Rank.JACK,
            ]
        else:
            # Normal order: 7, 8, 9, Jack, Queen, King, 10, Ace
            trump_order = [
                Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.JACK,
                Rank.QUEEN, Rank.KING, Rank.TEN, Rank.ACE,
            ]

        try:
            rank_index = trump_order.index(rank)
            return trump_order[rank_index + 1:]
        except ValueError:
            return []

    def _is_team_winning_trick(self, plays, trump_suit=None) -> bool:
        """Check if our team is currently winning the trick.

        The determination is made on the led-suit ranking — the highest
        card of the led suit and who played it — with ``trump_suit``
        defaulting to ``None`` (the normal, trump-agnostic ordering). Our
        team is winning when our partner holds that top led-suit card. The
        team-losing branch does its own trump-aware comparison, so the
        cut/over-cut reasoning lives there rather than in this gate.

        Args:
            plays: The in-progress trick's plays, a ``tuple[Play, ...]``.
            trump_suit: Ordering to rank by; ``None`` for the normal order.
        """

        if len(plays) < 1:
            return False

        # Find partner's position
        partner_position = self._get_partner_position()

        # Check if partner played the strongest card so far
        strongest_position = self._get_strongest_card_position(plays, trump_suit)
        return strongest_position == partner_position

    def _get_partner_position(self):
        """Get partner's position."""

        position_map = {'North': 'South', 'South': 'North', 'East': 'West', 'West': 'East'}
        return position_map.get(self.position)

    def _get_strongest_card_position(self, plays, trump_suit):
        """Get the position of the player who played the strongest card.

        Args:
            plays: The trick's plays, a ``tuple[Play, ...]``.
            trump_suit: The suit to rank by, or ``None`` for normal order.
        """

        if not plays:
            return None

        strongest_card = self._get_strongest_card_in_trick(plays, trump_suit)

        # Find which player played the strongest card
        for player, card in plays:
            if card == strongest_card:
                return player.position

        return None

    @staticmethod
    def _get_strongest_card_in_trick(plays, trump_suit):
        """Get the strongest card played so far in the trick.

        Args:
            plays: The trick's plays, a ``tuple[Play, ...]``. ``Play``
                unpacks as ``(player, card)``.
            trump_suit: The suit to rank by, or ``None`` for normal order.
        """

        if not plays:
            return None

        led_suit = plays[0].card.suit
        cards = [card for _, card in plays]

        # Trump cards beat non-trump (unless led suit is trump)
        if led_suit != trump_suit:
            trump_cards = [c for c in cards if c.suit == trump_suit]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # Among cards of led suit
        led_suit_cards = [c for c in cards if c.suit == led_suit]
        if led_suit_cards:
            order_suit = trump_suit if led_suit == trump_suit else None
            return max(led_suit_cards, key=lambda c: c.get_order(order_suit))

        return cards[0]

    @staticmethod
    def _is_stronger_card(card, current_best, trump_suit):
        """Check if card is stronger than current_best."""

        if not current_best:
            return True

        # If current best is trump and our card isn't (and trump is not led suit)
        if current_best.suit == trump_suit and card.suit != trump_suit:
            return False

        # If our card is trump and current best isn't
        if card.suit == trump_suit and current_best.suit != trump_suit:
            return True

        # Both trump or both same suit
        if card.suit == current_best.suit:
            order_suit = trump_suit if card.suit == trump_suit else None
            return card.get_order(order_suit) > current_best.get_order(order_suit)

        return False

    def _can_trump_win(self, trump_card, plays, trump_suit):
        """Check if playing this trump card would win the trick.

        Args:
            trump_card: The trump card being considered.
            plays: The in-progress trick's plays, a ``tuple[Play, ...]``.
            trump_suit: The current trump suit.
        """

        current_best = self._get_strongest_card_in_trick(plays, trump_suit)
        return self._is_stronger_card(trump_card, current_best, trump_suit)
