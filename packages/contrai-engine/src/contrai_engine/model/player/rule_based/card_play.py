"""Expert rule-based card-play strategy (see the package docstring)."""

from contrai_core.card import Card
from contrai_core.play import PlayObservation
from contrai_core.position import Position
from contrai_core.trick import current_winner
from contrai_core.types import ContractSuit, Rank, Suit, is_trump, trump_suits

from ..strategy import CardPlayStrategy, PlayerStateMixin


class RuleBasedCardPlayStrategy(CardPlayStrategy, PlayerStateMixin):
    """Expert card-play policy.

    Stateless between calls: every decision is a pure function of the
    frozen :class:`~contrai_core.PlayObservation` it is handed. The card
    tracking the rules need — which cards have fallen and which suits
    each seat is proven void in — is *derived* from the observation's
    public trick history on each turn (see :meth:`_derive_tracking`),
    never carried across calls or rounds. Decides which card to play
    from the trick state, the contract, and what has fallen.
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

        # Rebuild the fallen-card map and the per-player void suits from
        # the public history before deciding; the trick-reading helpers
        # below consume them.
        fallen, voids = self._derive_tracking(observation)

        if not observation.current_trick:
            # First to play this trick. Trick 0 with nothing played yet is
            # the opening lead; any later trick is a fresh lead with
            # history behind it.
            if observation.trick_number == 0:
                return self._play_opening_card(observation)
            return self._play_leading_card(observation, fallen, voids)

        # Someone has already played this trick — we are following.
        return self._play_following_card(observation, fallen, voids)

    def _derive_tracking(
        self, observation: PlayObservation
    ) -> tuple[dict[Suit, set], dict[Position, set[Suit]]]:
        """Rebuild fallen-card and per-seat void tracking from history.

        Replays every play in ``(*completed_tricks, current_trick)``
        chronologically and reconstructs, for each play, exactly the
        information a per-card tracker accumulates:

        - **Fallen cards**: every played card is recorded under its suit —
          own plays included — so ``fallen[suit]`` plus the seat's own
          holding plus the still-unseen cards always sum to 8 per suit.
        - **Led-suit voids**: following suit is never optional, so *any*
          card off the led suit — ruff and discard alike — proves the
          seat holds none of that suit. No exemption applies here.
        - **Voids in trump**: a seat that fails to follow the led suit and
          does not trump has proven it holds no trump, but only when it was
          *compelled* to. The compulsion is judged against the trick state
          **before** the play lands — the master among the plays strictly
          earlier in the same trick. A seat discarding while its own
          partner is already master was free to (the partner-master
          exemption), so that discard proves nothing. On a trump lead there
          is no exemption: holding trump forces playing it, so any
          off-trump card there is always a void (the led-suit rule above
          already records it — led suit and trump are the same suit).

        The pre-play winner must be read from the plays *before* this one,
        not after: a discard whose partner becomes master only through a
        later play in the same trick was still compelled at decision time,
        and evaluating the winner one play too late would silently hide the
        void.

        The observation's trick records are sealed ``ObservedPlay``
        pairs, so every play reads as ``(position, card)`` and all
        tracking below keys on the seat's :class:`Position` — the
        strategy never touches a live player object.

        Args:
            observation: The play-phase view whose public history is
                replayed.

        Returns:
            A ``(fallen, voids)`` pair — ``fallen`` maps each card suit to
            its set of fallen ranks; ``voids`` maps each seat's
            :class:`Position` to the set of suits that seat is proven to
            hold no card of.
        """

        trump_suit = observation.trump_suit
        # The round's trump as actual card suits — empty in a no-trump round.
        # ``voids`` below maps seats to sets of *card* suits, so a round with
        # nothing trump must contribute no trump-void entry at all.
        round_trumps = trump_suits(trump_suit)
        # One bucket per card suit — every Suit member is one, so the map
        # derives from the enum rather than restating the four.
        fallen: dict[Suit, set] = {suit: set() for suit in Suit}
        voids: dict[Position, set[Suit]] = {}

        for trick in (*observation.completed_tricks, observation.current_trick):
            for index, (position, card) in enumerate(trick):
                # Record the fallen card — happens for every play, whatever
                # it proves about voids.
                fallen[card.suit].add(card.rank)

                # Not following the led suit is always proof of a led-suit
                # void — following is mandatory whenever possible.
                led_suit = trick[0].card.suit
                if card.suit != led_suit:
                    voids.setdefault(position, set()).add(led_suit)

                # Trump led: the led-suit rule above already recorded the
                # trump void, and no further inference exists.
                if is_trump(led_suit, trump_suit):
                    continue

                # Reconstruct the pre-play master: the winner among the
                # plays strictly earlier in this trick — the state the seat
                # decided against. On sealed records the winner comes back
                # as a Position, so "partner was master" is seat arithmetic:
                # the master seat is this seat's partner. (Within one trick
                # the prior master can never be the seat itself — it has not
                # played yet.)
                prior = current_winner(list(trick[:index]), trump_suit)
                partner_was_master = prior is position.partner

                # Non-trump led: a discard behind a master partner is
                # voluntary and proves nothing about trump.
                if partner_was_master:
                    continue
                # Neither followed nor trumped, with no partner to shield the
                # choice — proof of a trump void. Guarded on ``round_trumps``,
                # not on ``trump_suit is not None``: a no-trump contract has a
                # trump_suit that is simply not a card suit, and ``voids``
                # holds card suits only.
                if (
                    round_trumps
                    and card.suit != led_suit
                    and not is_trump(card.suit, trump_suit)
                ):
                    voids.setdefault(position, set()).update(round_trumps)

        return fallen, voids

    def _play_opening_card(self, observation: PlayObservation) -> Card:
        """Play the very first card of the round."""

        contract = observation.contract
        playable_cards = observation.legal_cards
        hand = observation.hand
        trump_suit = observation.trump_suit

        if contract and contract.player.team == self.team:
            # Our team has the contract - play the strongest trump
            trump_cards = [c for c in playable_cards if c.is_trump(trump_suit)]
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
        non_trump_cards = [c for c in playable_cards if not c.is_trump(trump_suit)]

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
        self,
        observation: PlayObservation,
        fallen: dict[Suit, set],
        voids: dict[Position, set[Suit]],
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
            trump_cards = [c for c in playable_cards if c.is_trump(trump_suit)]
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
        non_trump_cards = [c for c in playable_cards if not c.is_trump(trump_suit)]

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
        self,
        observation: PlayObservation,
        fallen: dict[Suit, set],
        voids: dict[Position, set[Suit]],
    ) -> Card:
        """Strategy when not first to play.

        Both follow branches receive the anticipated-ruff flag — whether
        an opponent still to play in this trick is expected to cut it
        (see :meth:`_opponent_cut_expected`) — which turns their usual
        point-piling plays into damage control.

        Args:
            observation: The frozen play-phase view for this seat.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
            voids: The per-seat proven-void suits from
                :meth:`_derive_tracking`, keyed by :class:`Position`.
        """

        cut_expected = self._opponent_cut_expected(observation, fallen, voids)
        if self._is_team_winning_trick(observation.current_trick):
            return self._play_when_team_winning(observation, fallen, cut_expected)
        return self._play_when_team_losing(observation, fallen, cut_expected)

    def _play_when_team_winning(
        self,
        observation: PlayObservation,
        fallen: dict[Suit, set],
        cut_expected: bool,
    ) -> Card:
        """Play when our team is currently winning the trick.

        Partner already secures the trick, so the goal is to add value
        (high-points cards) to the pile WITHOUT wasting trumps — unless
        an opponent still to play is expected to ruff (``cut_expected``),
        in which case the trick is presumed lost and every pile-on rule
        flips to conceding as little as possible:

        1. Follow suit if able — pile the highest-points lead-suit card
           on partner's win, but never a master: a card partner's own
           play just promoted (their Ace makes our Ten the new suit
           master) can still win a later trick, so keep it and give the
           next-highest instead. When the only followable card IS the
           master, the play is forced and it goes anyway. Ruff expected
           → concede the lowest-points card instead of piling on.
        2. Cannot follow suit → discard a NON-TRUMP card. Don't dump
           trumps onto a trick the partner has already locked down.
           Prefer non-master cards (preserve cards that can still win
           their suit later); within the candidate set, pick the
           highest-points to maximize this trick's value — or the
           lowest-points when the ruff is expected to capture it.
        3. Hand has nothing but trumps → forced to play one. Use the
           lowest trump so we don't waste the Jack or 9.

        Args:
            observation: The frozen play-phase view for this seat.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
            cut_expected: Whether :meth:`_opponent_cut_expected` predicts
                an opponent still to play will ruff this trick.
        """
        trump_suit = observation.trump_suit
        led_suit = observation.led_suit
        playable_cards = observation.legal_cards

        # 1. Follow suit if able, preserving the suit's current master.
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            if cut_expected:
                # The trick is presumed lost to the ruff — concede the
                # cheapest card instead of feeding the cutter.
                return min(same_suit_cards, key=lambda c: c.get_points(trump_suit))
            non_master = [
                c for c in same_suit_cards
                if not self._is_master_card(c, trump_suit, fallen)
            ]
            candidates = non_master or same_suit_cards
            return max(candidates, key=lambda c: c.get_points(trump_suit))

        # 2. Discard a non-trump card.
        non_trump_cards = [
            c for c in playable_cards if not c.is_trump(trump_suit)
        ]
        if non_trump_cards:
            non_master_non_trump = [
                c for c in non_trump_cards
                if not self._is_master_card(c, trump_suit, fallen)
            ]
            candidates = non_master_non_trump or non_trump_cards
            if cut_expected:
                # Same logic as above: a discard onto a ruffed trick is
                # captured too, so it turns cheap.
                return min(candidates, key=lambda c: c.get_points(trump_suit))
            return max(candidates, key=lambda c: c.get_points(trump_suit))

        # 3. Only trumps in hand — dump the lowest one.
        if playable_cards:
            return min(playable_cards, key=lambda c: c.get_order(trump_suit))
        return playable_cards[0]

    def _play_when_team_losing(
        self,
        observation: PlayObservation,
        fallen: dict[Suit, set],
        cut_expected: bool,
    ) -> Card:
        """Play when an opponent is currently winning the trick.

        The goal flips from adding value to contesting the trick: win it
        when a card can, concede as cheaply as possible when none can.
        The rules cascade in order:

        1. Follow suit and beat if able — among the led-suit cards that
           beat the current best, play the highest-points one: it takes
           the trick AND banks the most points. When an opponent still
           to play is expected to ruff (``cut_expected``), whatever we
           invest is likely captured — so beat with the *smallest*
           stronger card instead. That hedge keeps the loss minimal and
           still pays off when we sit second: our partner plays after
           the predicted cutter and may over-ruff, turning the cheap
           investment into a won trick.
        2. Follow suit but cannot beat → the trick is gone; concede the
           lowest-points card of the led suit rather than feed it.
        3. Cannot follow suit → ruff if it wins: play the lowest trump
           that beats the current best (over-ruffing a trump already
           played works the same way — the comparison is trump-aware).
           No trump wins → fall through rather than waste one that
           would be over-ruffed.
        4. Cannot follow or usefully ruff → discard the lowest-points
           card from the shortest suit, excluding masters (a master can
           still win its suit later). Nothing but masters left → the
           first legal card goes.

        Args:
            observation: The frozen play-phase view for this seat.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
            cut_expected: Whether :meth:`_opponent_cut_expected` predicts
                an opponent still to play will ruff this trick.
        """

        trump_suit = observation.trump_suit
        led_suit = observation.led_suit
        playable_cards = observation.legal_cards
        plays = observation.current_trick
        current_best = self._get_strongest_card_in_trick(plays, trump_suit)

        # 1./2. Try to follow suit.
        same_suit_cards = [c for c in playable_cards if c.suit == led_suit]
        if same_suit_cards:
            # 1. Beat the current best if able.
            stronger_cards = [c for c in same_suit_cards
                             if self._is_stronger_card(c, current_best, trump_suit)]
            if stronger_cards:
                if cut_expected:
                    # A ruff is coming — invest the smallest card that
                    # still beats the current best. (The predicate is
                    # False on trump leads, so the led suit is plain here
                    # and the normal order applies.)
                    return min(stronger_cards, key=lambda c: c.get_order(None))
                return max(stronger_cards, key=lambda c: c.get_points(trump_suit))
            # 2. Can't beat — concede the lowest card.
            return min(same_suit_cards, key=lambda c: c.get_points(trump_suit))

        # 3. Can't follow suit — ruff if it wins the trick.
        if trump_suits(trump_suit) and not is_trump(led_suit, trump_suit):
            trump_cards = [c for c in playable_cards if c.is_trump(trump_suit)]
            if trump_cards:
                winning_trumps = [c for c in trump_cards
                                if self._can_trump_win(c, plays, trump_suit)]
                if winning_trumps:
                    return min(winning_trumps, key=lambda c: c.get_order(trump_suit))

        # 4. Can't follow or usefully ruff — discard lowest from the
        # shortest suit (excluding masters).
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
        self,
        trump_suit: ContractSuit | None,
        fallen: dict[Suit, set],
        voids: dict[Position, set[Suit]],
        hand,
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
            trump_suit: The round's trump, or ``None`` with no contract. A
                round where nothing is trump answers ``False`` outright —
                there is no trump for anyone to hold.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
            voids: The per-seat proven-void suits from
                :meth:`_derive_tracking`, keyed by :class:`Position`.
            hand: The observing seat's own hand (``observation.hand``).

        Returns:
            True if at least one opponent might still hold a trump.
        """

        round_trumps = trump_suits(trump_suit)
        if not round_trumps:
            return False
        # Narrowed to a real card suit: both knowledge sources below key on
        # the fallen map and the void sets, which hold card suits only.
        trump = round_trumps[0]

        # Counting: 8 trumps total; unseen = 8 - fallen - in our hand.
        trump_fallen = len(fallen.get(trump, set()))
        trump_in_hand = self._count_suit(hand, trump)
        if trump_fallen >= (8 - trump_in_hand):
            return False

        # Void inference: a contrée table has exactly two opponents. When both
        # are known void, any unseen trumps sit in partner's hand — pulling
        # them helps nobody. Voids key on seat positions, so "opponent" is
        # seat arithmetic against our own position.
        opponents_void = {
            seat
            for seat, void_suits in voids.items()
            if trump in void_suits and seat in self.position.opponents
        }
        return len(opponents_void) < 2

    def _opponent_cut_expected(
        self,
        observation: PlayObservation,
        fallen: dict[Suit, set],
        voids: dict[Position, set[Suit]],
    ) -> bool:
        """Predict whether an opponent still to play will ruff this trick.

        Pure inference from the public history: an opponent seen unable
        to follow the led suit earlier in the round cannot hold it now,
        so if that opponent can still hold a trump, the expert assumption
        is that the trick will be cut. All three legs must hold for some
        opponent who has not played in the current trick yet:

        1. **Led-suit void** — the opponent is proven void in the suit
           led right now.
        2. **Trump plausible** — that same opponent is *not* proven void
           in trump.
        3. **A trump is unseen** — at least one of the 8 trumps sits
           outside our own hand and the fallen cards; with none left,
           nobody can ruff anything.

        Trump leads and ``NO_TRUMP`` contracts have no ruff concept, and
        a void seat that already played this trick is no longer a threat
        — both come back ``False``. Being last to play also naturally
        returns ``False``: no opponent is left behind us.

        Args:
            observation: The frozen play-phase view for this seat.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
            voids: The per-seat proven-void suits from
                :meth:`_derive_tracking`, keyed by :class:`Position`.

        Returns:
            True when some opponent yet to play in the current trick is
            proven void in the led suit and may still hold a trump.
        """

        trump_suit = observation.trump_suit
        led_suit = observation.led_suit
        # No trump in this round (no contract, or a no-trump one) and no trump
        # lead: either way nothing can be cut. One ``trump_suits`` call answers
        # both cases, and its emptiness is what keeps a no-trump round out of
        # the trump counting below.
        round_trumps = trump_suits(trump_suit)
        if not round_trumps or led_suit is None or is_trump(led_suit, trump_suit):
            return False
        # Narrowed to a real card suit, which is what the fallen map and the
        # void sets are keyed by.
        trump = round_trumps[0]

        # Leg 3 — counting: any unseen trump at all?
        trump_fallen = len(fallen.get(trump, set()))
        trump_in_hand = self._count_suit(observation.hand, trump)
        if trump_fallen + trump_in_hand >= 8:
            return False

        # Legs 1 and 2, restricted to opponents still to play. Everything
        # here is seat arithmetic on positions — the sealed trick records
        # carry no player objects to compare teams through.
        already_played = {play.position for play in observation.current_trick}
        return any(
            seat in self.position.opponents
            and seat not in already_played
            and led_suit in void_suits
            and trump not in void_suits
            for seat, void_suits in voids.items()
        )

    def _is_master_card(self, card, trump_suit, fallen: dict[Suit, set]) -> bool:
        """Check if a card is currently the master (highest remaining) in its suit.

        Args:
            card: The candidate card.
            trump_suit: The round's trump, or ``None`` with no contract.
            fallen: The fallen-card map from :meth:`_derive_tracking`.
        """

        # Get fallen cards in this suit
        suit_fallen = fallen.get(card.suit, set())

        # Get all ranks higher than this card's rank
        higher_ranks = self._get_higher_ranks(
            card.rank, as_trump=card.is_trump(trump_suit)
        )

        # Check if all higher cards have fallen
        return all(rank in suit_fallen for rank in higher_ranks)

    @staticmethod
    def _get_higher_ranks(rank: Rank, *, as_trump: bool) -> list[Rank]:
        """Get all ranks higher than the given rank, in the applicable order.

        Takes the already-decided answer rather than a suit to compare
        against: only the caller knows whether the card in hand is trump,
        and re-deriving it here from a suit pair is what let a contract
        naming no suit pick the wrong ordering unnoticed.

        Args:
            rank: The rank to rank above.
            as_trump: Whether to use the trump ordering (the card is trump)
                or the plain one.

        Returns:
            The ranks above ``rank``, weakest first; empty if it is already
            the highest.
        """

        if as_trump:
            # Trump order: 7, 8, Queen, King, 10, Ace, 9, Jack
            rank_order = [
                Rank.SEVEN, Rank.EIGHT, Rank.QUEEN, Rank.KING,
                Rank.TEN, Rank.ACE, Rank.NINE, Rank.JACK,
            ]
        else:
            # Normal order: 7, 8, 9, Jack, Queen, King, 10, Ace
            rank_order = [
                Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.JACK,
                Rank.QUEEN, Rank.KING, Rank.TEN, Rank.ACE,
            ]

        try:
            rank_index = rank_order.index(rank)
            return rank_order[rank_index + 1:]
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
            plays: The in-progress trick's plays, a
                ``tuple[ObservedPlay, ...]``.
            trump_suit: Ordering to rank by; ``None`` for the normal order.
        """

        if len(plays) < 1:
            return False

        # Our team is winning when partner played the strongest card so far.
        strongest_position = self._get_strongest_card_position(plays, trump_suit)
        return strongest_position == self.position.partner

    def _get_strongest_card_position(self, plays, trump_suit) -> Position | None:
        """Get the position of the seat that played the strongest card.

        Args:
            plays: The trick's plays, a ``tuple[ObservedPlay, ...]`` —
                sealed ``(position, card)`` records.
            trump_suit: The suit to rank by, or ``None`` for normal order.

        Returns:
            The position that played the strongest card, or ``None`` when
            ``plays`` is empty.
        """

        if not plays:
            return None

        strongest_card = self._get_strongest_card_in_trick(plays, trump_suit)

        # Find which seat played the strongest card
        for position, card in plays:
            if card == strongest_card:
                return position

        return None

    @staticmethod
    def _get_strongest_card_in_trick(plays, trump_suit):
        """Get the strongest card played so far in the trick.

        Args:
            plays: The trick's plays, a ``tuple[ObservedPlay, ...]``.
                ``ObservedPlay`` unpacks as ``(position, card)``.
            trump_suit: The suit to rank by, or ``None`` for normal order.
        """

        if not plays:
            return None

        led_suit = plays[0].card.suit
        cards = [card for _, card in plays]

        # Trump cards beat non-trump (unless led suit is trump)
        if not is_trump(led_suit, trump_suit):
            trump_cards = [c for c in cards if c.is_trump(trump_suit)]
            if trump_cards:
                return max(trump_cards, key=lambda c: c.get_order(trump_suit))

        # Among cards of led suit
        led_suit_cards = [c for c in cards if c.suit == led_suit]
        if led_suit_cards:
            order_suit = trump_suit if is_trump(led_suit, trump_suit) else None
            return max(led_suit_cards, key=lambda c: c.get_order(order_suit))

        return cards[0]

    @staticmethod
    def _is_stronger_card(card, current_best, trump_suit):
        """Check if card is stronger than current_best."""

        if not current_best:
            return True

        best_is_trump = current_best.is_trump(trump_suit)
        card_is_trump = card.is_trump(trump_suit)

        # If current best is trump and our card isn't (and trump is not led suit)
        if best_is_trump and not card_is_trump:
            return False

        # If our card is trump and current best isn't
        if card_is_trump and not best_is_trump:
            return True

        # Both trump or both same suit
        if card.suit == current_best.suit:
            order_suit = trump_suit if card_is_trump else None
            return card.get_order(order_suit) > current_best.get_order(order_suit)

        return False

    def _can_trump_win(self, trump_card, plays, trump_suit):
        """Check if playing this trump card would win the trick.

        Args:
            trump_card: The trump card being considered.
            plays: The in-progress trick's plays, a
                ``tuple[ObservedPlay, ...]``.
            trump_suit: The current trump suit.
        """

        current_best = self._get_strongest_card_in_trick(plays, trump_suit)
        return self._is_stronger_card(trump_card, current_best, trump_suit)
