"""PlayState: immutable play-phase state and card-legality oracle.

The :class:`PlayState` is the sibling of :class:`contrai_core.Auction`. Where
``Auction`` owns the bidding history and its rules, ``PlayState`` owns the
chronological play history of one round's card play and the follow / trump
obligations that decide what may be played. It exposes the same shape a
search or reinforcement-learning game-state interface wants:

- :meth:`PlayState.legal_actions` so callers filter to only the cards the
  rules allow — the enforcement itself lives in :meth:`PlayState.apply`.
- :meth:`PlayState.apply` to produce a new ``PlayState`` with the play
  appended; it raises :class:`IllegalPlayError` (carrying a
  :class:`PlayRuleViolation`) on an out-of-turn, not-held, or
  obligation-breaking play.
- Derived views (:attr:`PlayState.trick_number`,
  :attr:`PlayState.current_trick`, :attr:`PlayState.completed_tricks`,
  :attr:`PlayState.trick_winners`, :attr:`PlayState.card_points_by_side`,
  :attr:`PlayState.trick_counts_by_side`, :attr:`PlayState.to_act`,
  :meth:`PlayState.is_terminal`) recomputed from the flat play history.
- :meth:`PlayState.with_hands` to fork the same public state onto
  replacement hands — the determinization primitive search-based AIs need.
- :meth:`PlayState.observe` to project the full state — which holds every
  seat's hand — down to a :class:`PlayObservation`, the imperfect-
  information view a single player is allowed to see. Every person the
  observation names is named by seat: its trick records are sealed to
  :class:`ObservedPlay` ``(position, card)`` pairs, its contract to an
  :class:`~contrai_core.ObservedContract`, its auction to
  ``Bid[Position]`` records. This is the input surface handed to AI
  card-play strategies, never the raw ``PlayState``.

Play records are plain ``(player, card)`` pairs, so the same tuples flow
through the derived views and the winner rule (:func:`current_winner`) that
the rest of the domain already speaks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, Optional

from .bid import seal_bid
from .exceptions import IllegalPlayError, PlayRuleViolation
from .rule_config import RuleConfig
from .rules import NoTrumpRules, TrumpRules, rules_for
from .team_side import TeamSide
from .trick import TrickRecord, current_winner

if TYPE_CHECKING:
    from .bid import Bid
    from .card import Card
    from .contract import Contract, ObservedContract
    from .player import BasePlayer
    from .position import Position
    from .team import Team
    from .types import ContractSuit, Suit


class Play(NamedTuple):
    """A single card play: the player who played and the card they played.

    A :class:`~typing.NamedTuple` rather than a dataclass so a ``Play``
    unpacks exactly like the ``(player, card)`` pairs the trick and winner
    helpers already iterate over — existing tuple-consuming code stays
    drop-in compatible.

    Attributes:
        player: The player who made the play.
        card: The card that was played.
    """

    player: BasePlayer
    card: Card


class ObservedPlay(NamedTuple):
    """A single card play as an observation reports it: seat + card.

    The observation-facing counterpart of :class:`Play`. Where a ``Play``
    holds a live :class:`BasePlayer` reference — through which a consumer
    could reach ``player.hand`` and read cards it is not entitled to —
    an ``ObservedPlay`` carries only the seat's opaque :class:`Position`,
    so no hand is reachable through a trick record.

    Unpacks as a ``(position, card)`` pair, mirroring how :class:`Play`
    unpacks as ``(player, card)`` — consumers iterating ``(who, card)``
    pairs stay drop-in compatible, they just receive a seat identifier
    for ``who``.

    Attributes:
        position: The seat that played the card.
        card: The card that was played.
    """

    position: Position
    card: Card


@dataclass(frozen=True, slots=True)
class PlayState:
    """Immutable play-phase state for one round of contrée.

    ``PlayState`` is the canonical view of card-play-so-far: it owns the
    flat chronological tuple of :class:`Play` records, knows the follow /
    trump obligations, and answers what is legal now, whose turn it is,
    which tricks have completed and who won them, and whether the play
    phase is over.

    The state is stored as **parallel tuples** — ``players`` alongside
    ``hands`` — rather than a player-keyed mapping: it keeps the frozen
    value honest (nothing to mutate through) and does not lean on
    :class:`BasePlayer` hashing, which is identity-based. Everything else
    is derived from ``plays`` and recomputed on access; ``slots=True``
    forbids stashing lazy caches, which is the intent — the history is the
    single source of truth.

    Like :class:`contrai_core.Auction`, the bare constructor performs **no**
    validation, so tests and search forks can inject arbitrary mid-round
    states. Use :meth:`start` for a validated seeding from a fresh deal.

    ``PlayState`` is frozen but **not hashable**: :class:`Contract` defines
    ``__eq__`` without ``__hash__``, so hashing a ``PlayState`` (which would
    hash its contract) raises ``TypeError``. This is an accepted property —
    the state is compared by value, never used as a dict key or set member.

    Attributes:
        contract: The established :class:`Contract`; its ``suit`` supplies
            the trump suit for every legality and winner decision. May be a
            ``NO_TRUMP`` contract, in which case all trump obligations
            collapse to plain follow-suit.
        players: The seating order. ``players[0]`` leads trick 0; each later
            trick is led by the previous trick's winner.
        hands: Per-seat remaining cards, parallel to ``players``.
        plays: The flat chronological play history. Every four plays form
            one trick. Defaults to empty — a fresh play phase.
        rules: The table ruleset this play phase runs under. Carried, not
            yet consulted — no legality or scoring decision reads a knob
            of it today. Part of value equality, so two states played
            under different rulesets never compare equal.
    """

    contract: Contract
    players: tuple[BasePlayer, ...]
    hands: tuple[tuple[Card, ...], ...]
    plays: tuple[Play, ...] = field(default=())
    rules: RuleConfig = field(default_factory=RuleConfig)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def start(
        cls,
        contract: Contract,
        players: tuple[BasePlayer, ...],
        hands: tuple[tuple[Card, ...], ...],
        rules: RuleConfig | None = None,
    ) -> "PlayState":
        """Seed a validated play phase from a fresh deal.

        Args:
            contract: The established contract supplying the trump suit.
            players: The four players in seating order.
            hands: Per-seat starting hands, parallel to ``players``.
            rules: The table ruleset this play phase runs under. ``None``
                (the default) means :class:`~contrai_core.RuleConfig`'s
                own defaults — the §9 catalogue.

        Returns:
            A fresh :class:`PlayState` with no plays yet.

        Raises:
            ValueError: If there are not exactly 4 players, any hand does
                not hold exactly 8 cards, or the 32 dealt cards are not all
                distinct.
        """

        players = tuple(players)
        hands = tuple(tuple(hand) for hand in hands)

        if len(players) != 4:
            raise ValueError(
                f"A play phase needs exactly 4 players, got {len(players)}."
            )
        if len(hands) != 4:
            raise ValueError(
                f"A play phase needs exactly 4 hands, got {len(hands)}."
            )
        for seat, hand in enumerate(hands):
            if len(hand) != 8:
                raise ValueError(
                    f"Each starting hand must hold 8 cards; seat {seat} has "
                    f"{len(hand)}."
                )
        all_cards = [card for hand in hands for card in hand]
        if len(set(all_cards)) != 32:
            raise ValueError(
                "The 32 dealt cards must all be distinct; found duplicates "
                "across the hands."
            )

        return cls(
            contract=contract,
            players=players,
            hands=hands,
            plays=(),
            rules=rules if rules is not None else RuleConfig(),
        )

    # ------------------------------------------------------------------
    # Derived trick views
    # ------------------------------------------------------------------

    @property
    def trick_number(self) -> int:
        """The index of the trick in progress.

        Equals the number of completed tricks — so it advances only once the
        fourth card of a trick lands, and reads as the completed count while
        the state sits between tricks.
        """

        return len(self.plays) // 4

    @property
    def current_trick(self) -> tuple[Play, ...]:
        """The plays of the in-progress trick.

        Empty at the start of the round and immediately after any trick
        completes; otherwise the one to three plays made since the last
        trick boundary.
        """

        return self.plays[self.trick_number * 4:]

    @property
    def completed_tricks(self) -> tuple[TrickRecord[Play], ...]:
        """The completed tricks, each a :class:`TrickRecord` of four plays.

        Each record iterates and unpacks exactly like the bare four-play
        tuple it types, and additionally knows its :attr:`~TrickRecord.led_suit`
        and :meth:`~TrickRecord.winner`.
        """

        return tuple(
            TrickRecord(self.plays[i:i + 4])
            for i in range(0, self.trick_number * 4, 4)
        )

    @property
    def trick_winners(self) -> tuple[BasePlayer, ...]:
        """The winning player of each completed trick, in trick order."""

        trump_suit = self._trump_suit
        return tuple(
            current_winner(list(trick), trump_suit)
            for trick in self.completed_tricks
        )

    @property
    def card_points_by_side(self) -> dict[TeamSide, int]:
        """Trump-aware card points captured by each side so far.

        Each completed trick's whole pile is credited to the side of the
        seat that won it, scored through the contract's
        :class:`~contrai_core.TrumpRules` — so the 9 and the Jack of
        trump carry their trump values and every other card its plain
        one. The in-progress trick contributes nothing: nobody has
        captured it yet.

        This is the **raw pile only**. The last-trick bonus and the
        Belote bonus are contract-conversion rules, not facts about
        which cards were captured, and belong to whoever scores the
        round.

        Returns:
            Every :class:`~contrai_core.TeamSide` member as a key, so
            callers index directly rather than going through ``.get``;
            a side that has captured nothing maps to ``0``. Over a
            completed round the two values sum to 152 — the deck's card
            points, before any bonus.
        """

        rules = rules_for(self._trump_suit)
        points = {side: 0 for side in TeamSide}
        for trick, winner in zip(self.completed_tricks, self.trick_winners):
            points[winner.position.team_side] += sum(
                rules.points(play.card) for play in trick
            )
        return points

    @property
    def trick_counts_by_side(self) -> dict[TeamSide, int]:
        """Completed tricks captured by each side so far.

        The same tally as :attr:`card_points_by_side` counting tricks
        instead of points, credited off the same
        :attr:`trick_winners` derivation — so the two can never
        disagree about who took what.

        Returns:
            Every :class:`~contrai_core.TeamSide` member as a key,
            mapping to the number of completed tricks that side won;
            ``0`` for a side that has won none. The two values sum to
            :attr:`trick_number`.
        """

        counts = {side: 0 for side in TeamSide}
        for winner in self.trick_winners:
            counts[winner.position.team_side] += 1
        return counts

    @property
    def to_act(self) -> Optional[BasePlayer]:
        """The player whose turn it is, or ``None`` once the phase is over.

        Within a trick the turn rotates in seating order from that trick's
        leader. Trick 0 is led by ``players[0]``; every later trick is led
        by the previous trick's winner.
        """

        if self.is_terminal():
            return None
        leader = self._leader_of_current_trick()
        leader_seat = self._seat_of(leader)
        offset = len(self.current_trick)
        return self.players[(leader_seat + offset) % 4]

    def is_terminal(self) -> bool:
        """Return whether the play phase is over (all 32 cards played)."""

        return len(self.plays) == 32

    # ------------------------------------------------------------------
    # Hand lookup
    # ------------------------------------------------------------------

    def hand_of(self, player: BasePlayer) -> tuple[Card, ...]:
        """Return ``player``'s remaining cards.

        Args:
            player: The seated player to look up.

        Returns:
            The player's remaining hand as it stands in this state.

        Raises:
            ValueError: If ``player`` is not seated in this state.
        """

        return self.hands[self._seat_of(player)]

    # ------------------------------------------------------------------
    # Legality
    # ------------------------------------------------------------------

    def legal_actions(self, player: BasePlayer) -> tuple[Card, ...]:
        """Enumerate every card ``player`` may legally play right now.

        This is player-parametric and enforces **no** turn order — it
        answers "what would be legal for this player" exactly as
        :meth:`contrai_core.Auction.legal_actions` does for bids. Turn
        enforcement lives in :meth:`apply`.

        The obligations, from ``contrée`` follow / trump rules:

        1. Follow the led suit if able.
        2. When trump is led, over-trump the best trump on the table if you
           hold a higher one; otherwise any card of the led (trump) suit.
        3. Void in the led suit with your partner not currently master: you
           must trump, over-trumping an opponent's ruff if able.
        4. Partner-master exemption: if your partner is currently winning,
           discard freely.
        5. Otherwise discard freely.
        6. At all trump every suit is trump, so obligations 1–2 and 5 are
           the whole rulebook: follow and raise in the led suit if able,
           discard freely when void — there is nothing to cut with
           (contree-domain.md §6.4).

        The returned cards are the very objects held in ``player``'s hand
        tuple — filtered, never reconstructed — so callers matching cards by
        identity keep working.

        Args:
            player: The player whose legal cards to enumerate.

        Returns:
            The legal cards, a subset of the player's remaining hand.

        Raises:
            ValueError: If ``player`` is not seated in this state.
        """

        hand = self.hand_of(player)
        if not hand:
            return ()

        rules = rules_for(self._trump_suit)
        trick = self.current_trick
        if not trick:
            # First to play in this trick — anything goes.
            return tuple(hand)

        lead_suit = trick[0].card.suit
        lead_suit_cards = tuple(card for card in hand if card.suit == lead_suit)
        # No regime guard needed: ``rules.is_trump`` already answers False
        # for every card when no suit is trump.
        trump_cards = tuple(card for card in hand if rules.is_trump(card.suit))

        # Rule 1/2 — follow suit, over-trumping when the led suit is trump.
        if lead_suit_cards:
            if rules.is_trump(lead_suit):
                higher = _higher_trumps_than_played(
                    lead_suit_cards, trick, rules, lead_suit
                )
                return higher if higher else lead_suit_cards
            return lead_suit_cards

        # Rule 4 — partner-master exemption. Applies only while the partner
        # is *currently* winning; a partner since over-trumped no longer
        # shields the player from the trump obligation.
        current_master = current_winner(list(trick), self._trump_suit)
        if current_master is not None and current_master.team == player.team:
            return tuple(hand)

        # No trump suit at all, or the led suit is itself trump and we are
        # void in it (which is every all-trump trick where the seat cannot
        # follow): nothing to cut with, free discard — §6.4 for both
        # variants. The no-trump test is an ``isinstance`` check against
        # the sealed leaf: an enum member is always truthy, so no bare
        # ``if not trump_suit`` can distinguish a NO_TRUMP contract from a
        # suit one. ``AllTrumpRules`` is not that leaf and reaches this
        # line through ``rules.is_trump(lead_suit)`` instead.
        if isinstance(rules, NoTrumpRules) or rules.is_trump(lead_suit):
            return tuple(hand)

        # Rule 3 — trump obligation. If an opponent has ruffed, beat them.
        highest_opponent_trump = _highest_opponent_trump(
            trick, player.team, rules
        )
        if highest_opponent_trump is not None:
            higher_trumps = tuple(
                card
                for card in trump_cards
                if rules.rank_in_suit(card)
                > rules.rank_in_suit(highest_opponent_trump)
            )
            if higher_trumps:
                return higher_trumps
            if trump_cards:
                return trump_cards
            return tuple(hand)

        # No opponent trump yet but the partner is not master → must trump
        # if able.
        if trump_cards:
            return trump_cards
        return tuple(hand)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self, play: Play) -> "PlayState":
        """Return a new state with ``play`` applied.

        Args:
            play: The ``(player, card)`` play to make. The player must be
                :attr:`to_act`, must hold the card, and the card must be
                among :meth:`legal_actions` for that player.

        Returns:
            A new :class:`PlayState` with the play appended and the card
            removed from the player's hand (the relative order of the
            remaining cards is preserved). Contract, players and rules
            carry over unchanged. The receiver is unchanged.

        Raises:
            IllegalPlayError: Carrying the offending :class:`PlayRuleViolation`
                — ``OUT_OF_TURN`` (wrong player, or the phase is over),
                ``CARD_NOT_IN_HAND`` (the card is not held), or one of
                ``MUST_FOLLOW_SUIT`` / ``MUST_TRUMP`` / ``MUST_OVERTRUMP`` —
                and a ``context`` naming the acting player's seat.
        """

        player, card = play
        # Name the seat in every rejection so diagnostics immediately say
        # who misplayed, not just which card.
        context = f"{player.position} card play"

        actor = self.to_act
        if actor is None or player is not actor:
            raise IllegalPlayError(
                card, PlayRuleViolation.OUT_OF_TURN, (), context=context
            )

        hand = self.hand_of(player)
        legal = self.legal_actions(player)

        if card not in hand:
            raise IllegalPlayError(
                card, PlayRuleViolation.CARD_NOT_IN_HAND, legal, context=context
            )
        if card not in legal:
            reason = _classify_violation(
                player, card, self._trump_suit, self.current_trick, hand
            )
            raise IllegalPlayError(card, reason, legal, context=context)

        seat = self._seat_of(player)
        index = hand.index(card)
        new_hand = hand[:index] + hand[index + 1:]
        new_hands = self.hands[:seat] + (new_hand,) + self.hands[seat + 1:]
        return PlayState(
            contract=self.contract,
            players=self.players,
            hands=new_hands,
            plays=self.plays + (play,),
            rules=self.rules,
        )

    # ------------------------------------------------------------------
    # Determinization fork
    # ------------------------------------------------------------------

    def with_hands(
        self, hands: tuple[tuple[Card, ...], ...]
    ) -> "PlayState":
        """Fork this state onto replacement hands.

        The public history — contract, players, plays **and rules** — is
        preserved; only the per-seat hands change. This is the
        determinization primitive a search-based AI uses to sample worlds
        consistent with what it has seen: every sampled world must be
        played under the same table ruleset as the real one.

        Args:
            hands: Replacement per-seat hands, parallel to ``players``.

        Returns:
            A new :class:`PlayState` with the same history and the given
            hands.

        Raises:
            ValueError: If a seat's replacement hand does not match its
                current remaining card count, or any replacement card has
                already been played in this round.
        """

        new_hands = tuple(tuple(hand) for hand in hands)
        if len(new_hands) != len(self.players):
            raise ValueError(
                f"Expected {len(self.players)} hands, got {len(new_hands)}."
            )
        for seat, hand in enumerate(new_hands):
            if len(hand) != len(self.hands[seat]):
                raise ValueError(
                    f"Seat {seat} must keep {len(self.hands[seat])} cards; "
                    f"replacement has {len(hand)}."
                )
        played = {play.card for play in self.plays}
        for hand in new_hands:
            for card in hand:
                if card in played:
                    raise ValueError(
                        f"Replacement hand contains an already-played card: "
                        f"{card!r}."
                    )
        return PlayState(
            contract=self.contract,
            players=self.players,
            hands=new_hands,
            plays=self.plays,
            rules=self.rules,
        )

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def observe(
        self, player: BasePlayer, bids: tuple[Bid, ...] = ()
    ) -> "PlayObservation":
        """Project this state down to what ``player`` is allowed to see.

        This is the state's sanctioned trust boundary: the full
        ``PlayState`` holds every seat's hand, but a card-play strategy
        must reason from only what its own seat has observed. The
        resulting :class:`PlayObservation` carries ``player``'s own hand,
        the public trick history, the contract and auction, and
        ``player``'s legal plays right now — nothing else.

        Every person the observation would otherwise name is re-recorded
        as a bare seat on the way out: the trick history as
        :class:`ObservedPlay` ``(position, card)`` pairs, the contract as
        an :class:`~contrai_core.ObservedContract`, each bid via
        :func:`~contrai_core.seal_bid`. No live :class:`BasePlayer`
        survives the projection, so no other seat's hand is reachable
        through what is handed over — not directly, and not through
        ``player.team.players`` either.

        The observing player is still passed in live: the state needs the
        identity to look up the hand and the legal actions. Only what
        comes back out is sealed.

        Args:
            player: The observing seat.
            bids: The auction history to attach to the observation,
                sealed onto seats on the way in. ``PlayState`` has no
                notion of the auction itself — the caller (the engine's
                ``Round``) supplies it. Defaults to an empty tuple.

        Returns:
            A :class:`PlayObservation` seeded from this state, from
            ``player``'s point of view.

        Raises:
            ValueError: If ``player`` is not seated in this state.
        """

        return PlayObservation(
            position=player.position,
            hand=self.hand_of(player),
            contract=self.contract.observed() if self.contract else None,
            bids=tuple(seal_bid(bid) for bid in bids),
            completed_tricks=tuple(
                TrickRecord(_seal_plays(trick))
                for trick in self.completed_tricks
            ),
            current_trick=_seal_plays(self.current_trick),
            legal_cards=self.legal_actions(player),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @property
    def _trump_suit(self) -> Optional[ContractSuit]:
        """The contract's trump suit, or ``None`` when there is no contract."""

        return self.contract.suit if self.contract else None

    def _seat_of(self, player: BasePlayer) -> int:
        """Return the seat index of ``player`` by identity.

        Args:
            player: The player to locate.

        Returns:
            The index of ``player`` within ``players``.

        Raises:
            ValueError: If ``player`` is not seated in this state.
        """

        for seat, seated in enumerate(self.players):
            if seated is player:
                return seat
        raise ValueError(f"Player {player!r} is not seated in this state.")

    def _leader_of_current_trick(self) -> BasePlayer:
        """Return the player who led the in-progress trick.

        Trick 0 is led by ``players[0]``; every later trick is led by the
        winner of the trick before it.
        """

        trick_index = self.trick_number
        if trick_index == 0:
            return self.players[0]
        return self.trick_winners[trick_index - 1]


@dataclass(frozen=True, slots=True)
class PlayObservation:
    """The imperfect-information view of a play phase for one player.

    Where :class:`PlayState` is the omniscient state — every seat's hand,
    all at once — ``PlayObservation`` is what a *single* player is allowed
    to know: their own remaining hand, the publicly visible trick history,
    the established contract and auction, and their own legal plays right
    now. :meth:`PlayState.observe` builds one per player; this is the
    input surface AI card-play strategies are handed, so a strategy can
    never accidentally read another seat's hand through the object it was
    given.

    **Every seat is named by :class:`~contrai_core.Position` and nothing
    else** — the observer itself, the trick records, the contract's
    declarer and doublers, and each bidder in the auction. No live
    :class:`BasePlayer` is reachable by any object path from an
    observation, which is what makes that guarantee hold: a
    ``BasePlayer`` exposes ``.hand`` directly and, through ``.team``,
    the partner's hand as well, so a single surviving reference would
    reopen the whole leak. The hidden state a search or learning agent
    must infer therefore cannot be read off its own input, and an
    evaluation result cannot be quietly invalidated by a policy that
    found the shortcut.

    Attributes:
        position: The observer's seat — the point of view this
            observation is from.
        hand: The observer's own remaining cards, and only the observer's;
            no other seat's hand is reachable from this field.
        contract: The established contract as an
            :class:`~contrai_core.ObservedContract`, supplying the trump
            suit, value, and the declarer's seat. ``None`` when the state
            carries no contract.
        bids: The auction history sealed onto seats — the same four
            variants an :class:`~contrai_core.Auction` holds, each with
            a :class:`~contrai_core.Position` in place of the bidder.
        completed_tricks: The completed tricks, each a
            :class:`~contrai_core.TrickRecord` of four
            :class:`ObservedPlay` records mirroring
            :attr:`PlayState.completed_tricks` play for play — this
            history is public.
        current_trick: The plays made so far in the in-progress trick,
            as :class:`ObservedPlay` records — also public.
        legal_cards: The observer's legal plays right now, a subset of
            ``hand``.
    """

    position: Position
    hand: tuple[Card, ...]
    contract: Optional[ObservedContract]
    bids: tuple[Bid[Position], ...]
    completed_tricks: tuple[TrickRecord[ObservedPlay], ...]
    current_trick: tuple[ObservedPlay, ...]
    legal_cards: tuple[Card, ...]

    @property
    def trick_number(self) -> int:
        """The index of the trick in progress.

        Consistent with :attr:`PlayState.trick_number`: the count of
        completed tricks, so it reads the same whether the state sits
        between tricks or the phase has not started yet.
        """

        return len(self.completed_tricks)

    @property
    def trump_suit(self) -> Optional[ContractSuit]:
        """The contract's trump suit, or ``None`` when there is no contract.

        Same rule as the state this observation was derived from: for a
        ``NO_TRUMP`` contract this is ``TrumpVariant.NO_TRUMP`` itself, not
        ``None`` — no real card ever carries that suit, so every
        trump-related rule (and :func:`current_winner`) already degrades
        correctly when handed it.
        """

        return self.contract.suit if self.contract else None

    @property
    def led_suit(self) -> Optional[Suit]:
        """The suit led in the in-progress trick, or ``None`` if it is empty."""

        if not self.current_trick:
            return None
        return self.current_trick[0].card.suit

    @property
    def played_cards(self) -> tuple[Card, ...]:
        """Every publicly seen card, flattened in chronological play order.

        Completed tricks first (in trick order), then the in-progress
        trick's plays so far.
        """

        return tuple(
            play.card for trick in self.completed_tricks for play in trick
        ) + tuple(play.card for play in self.current_trick)

    @property
    def current_winner(self) -> Optional[Position]:
        """The seat currently winning the in-progress trick.

        ``None`` while the trick is empty. Computed the same way
        :attr:`PlayState.trick_winners` computes a completed trick's
        winner — via the module-level :func:`current_winner`, which is
        generic over the "who" slot of its plays — so a partially played
        trick and a just-completed one agree on who is master. Reported
        as a :class:`Position` because that is all the sealed
        :class:`ObservedPlay` records carry.
        """

        return current_winner(list(self.current_trick), self.trump_suit)


def _seal_plays(plays: tuple[Play, ...]) -> tuple[ObservedPlay, ...]:
    """Project :class:`Play` records down to sealed observation records.

    The seat's :class:`Position` replaces the live :class:`BasePlayer`
    reference — the one object path through which an observation
    consumer could have reached another seat's hand.

    Args:
        plays: The play records to seal, in play order.

    Returns:
        The same plays as :class:`ObservedPlay` ``(position, card)``
        pairs, order preserved.
    """

    return tuple(
        ObservedPlay(play.player.position, play.card) for play in plays
    )


def _higher_trumps_than_played(
    candidates: tuple[Card, ...],
    plays: tuple[Play, ...],
    rules: TrumpRules,
    led_suit: Suit,
) -> tuple[Card, ...]:
    """Return the held cards that beat every card already competing.

    Used by the over-trump rule when the led suit is itself trump. Ranking
    goes through :meth:`TrumpRules.trick_rank`, not ``rank_in_suit``,
    because that is the only comparator that knows which cards *compete*:
    at all trump every suit is trump, so a discard of another suit passes
    an ``is_trump`` test yet can never take the trick, and ranking it on
    the trump scale would raise the bar for no reason.

    Args:
        candidates: The cards from the player's hand to filter — the held
            cards of the led suit.
        plays: The plays of the current trick.
        rules: The contract's trick rules.
        led_suit: The suit of the trick's first card, which selects which
            cards compete.

    Returns:
        The subset of ``candidates`` outranking every competing card
        played so far; empty when none does, or when nothing competes yet
        (defensive — the rule only calls this with a card on the table).
    """

    best_so_far = None
    for _, card in plays:
        rank = rules.trick_rank(card, led_suit)
        if rank is not None and (best_so_far is None or rank > best_so_far):
            best_so_far = rank
    if best_so_far is None:
        return ()
    return tuple(
        card
        for card in candidates
        if (rank := rules.trick_rank(card, led_suit)) is not None
        and rank > best_so_far
    )


def _highest_opponent_trump(
    plays: tuple[Play, ...],
    player_team: Team,
    rules: TrumpRules,
) -> Optional[Card]:
    """Return the highest trump an opponent of ``player_team`` has played.

    Args:
        plays: The plays of the current trick.
        player_team: The team whose opponents' trumps we scan for.
        rules: The contract's trick rules; under the no-trump regime no
            card is trump, so the scan finds nothing.

    Returns:
        The highest opposing trump card, or ``None`` if none was played.
    """

    highest = None
    for trick_player, card in plays:
        if not rules.is_trump(card.suit) or trick_player.team == player_team:
            continue
        if highest is None or rules.rank_in_suit(card) > rules.rank_in_suit(
            highest
        ):
            highest = card
    return highest


def _classify_violation(
    player: BasePlayer,
    card: Card,
    trump_suit: Optional[ContractSuit],
    trick: tuple[Play, ...],
    hand: tuple[Card, ...],
) -> PlayRuleViolation:
    """Classify *why* an in-hand card is an illegal play.

    Called only for a genuinely illegal card — held in hand, absent from
    :meth:`PlayState.legal_actions`, with the current trick already holding
    at least one play. The branch order mirrors ``legal_actions`` so the
    reason matches the obligation that filtered the card out.

    Args:
        player: The player whose illegal play is being explained.
        card: The illegal card attempted.
        trump_suit: The trump suit, or ``None``/``NO_TRUMP`` for none.
        trick: The plays of the current trick.
        hand: The player's remaining hand.

    Returns:
        The :class:`PlayRuleViolation` for the broken obligation.
    """

    rules = rules_for(trump_suit)
    lead_suit = trick[0].card.suit
    lead_suit_cards = [c for c in hand if c.suit == lead_suit]

    # Held the led suit. Trump led + a too-low card of that suit is an
    # over-trump failure; anything else off-suit is a follow failure. The
    # played card is discriminated by its *suit* against the led one —
    # under a single-suit contract that is the same question as "is it
    # trump", and it stays the right one for any regime where the led
    # suit competes on its own scale.
    if lead_suit_cards:
        if rules.is_trump(lead_suit) and card.suit == lead_suit:
            return PlayRuleViolation.MUST_OVERTRUMP
        return PlayRuleViolation.MUST_FOLLOW_SUIT

    # Void in the led suit (partner-master plays are legal, so never reach
    # here). An opponent already ruffed and we under-trumped → over-trump
    # failure; otherwise we discarded instead of trumping. Here the
    # discriminator is genuine trumpness — the card competes as a trump,
    # wherever the trick was led.
    highest_opponent_trump = _highest_opponent_trump(
        trick, player.team, rules
    )
    if highest_opponent_trump is not None and rules.is_trump(card.suit):
        return PlayRuleViolation.MUST_OVERTRUMP
    return PlayRuleViolation.MUST_TRUMP
