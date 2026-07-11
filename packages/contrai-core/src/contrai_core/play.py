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
  :attr:`PlayState.trick_winners`, :attr:`PlayState.to_act`,
  :meth:`PlayState.is_terminal`) recomputed from the flat play history.
- :meth:`PlayState.with_hands` to fork the same public state onto
  replacement hands — the determinization primitive search-based AIs need.

Play records are plain ``(player, card)`` pairs, so the same tuples flow
through the derived views and the winner rule (:func:`current_winner`) that
the rest of the domain already speaks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, Optional

from .exceptions import IllegalPlayError, PlayRuleViolation
from .trick import current_winner

if TYPE_CHECKING:
    from .card import Card
    from .contract import Contract
    from .player import BasePlayer
    from .team import Team
    from .types import Suit


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

    player: "BasePlayer"
    card: "Card"


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
    """

    contract: "Contract"
    players: tuple["BasePlayer", ...]
    hands: tuple[tuple["Card", ...], ...]
    plays: tuple[Play, ...] = field(default=())

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def start(
        cls,
        contract: "Contract",
        players: tuple["BasePlayer", ...],
        hands: tuple[tuple["Card", ...], ...],
    ) -> "PlayState":
        """Seed a validated play phase from a fresh deal.

        Args:
            contract: The established contract supplying the trump suit.
            players: The four players in seating order.
            hands: Per-seat starting hands, parallel to ``players``.

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

        return cls(contract=contract, players=players, hands=hands, plays=())

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
    def completed_tricks(self) -> tuple[tuple[Play, ...], ...]:
        """The completed tricks, each a tuple of exactly four plays."""

        return tuple(
            self.plays[i:i + 4] for i in range(0, self.trick_number * 4, 4)
        )

    @property
    def trick_winners(self) -> tuple["BasePlayer", ...]:
        """The winning player of each completed trick, in trick order."""

        trump_suit = self._trump_suit
        return tuple(
            current_winner(list(trick), trump_suit)
            for trick in self.completed_tricks
        )

    @property
    def to_act(self) -> Optional["BasePlayer"]:
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

    def hand_of(self, player: "BasePlayer") -> tuple["Card", ...]:
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

    def legal_actions(self, player: "BasePlayer") -> tuple["Card", ...]:
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

        trump_suit = self._trump_suit
        trick = self.current_trick
        if not trick:
            # First to play in this trick — anything goes.
            return tuple(hand)

        lead_suit = trick[0].card.suit
        lead_suit_cards = tuple(card for card in hand if card.suit == lead_suit)
        trump_cards = (
            tuple(card for card in hand if card.suit == trump_suit)
            if trump_suit
            else ()
        )

        # Rule 1/2 — follow suit, over-trumping when the led suit is trump.
        if lead_suit_cards:
            if trump_suit and lead_suit == trump_suit:
                higher = _higher_trumps_than_played(
                    lead_suit_cards, trick, trump_suit
                )
                return higher if higher else lead_suit_cards
            return lead_suit_cards

        # Rule 4 — partner-master exemption. Applies only while the partner
        # is *currently* winning; a partner since over-trumped no longer
        # shields the player from the trump obligation.
        current_master = current_winner(list(trick), trump_suit)
        if current_master is not None and current_master.team == player.team:
            return tuple(hand)

        # No trump suit (or the led suit is trump and we are void in it):
        # nothing to over-trump, free discard.
        if not trump_suit or lead_suit == trump_suit:
            return tuple(hand)

        # Rule 3 — trump obligation. If an opponent has ruffed, beat them.
        highest_opponent_trump = _highest_opponent_trump(
            trick, player.team, trump_suit
        )
        if highest_opponent_trump is not None:
            higher_trumps = tuple(
                card
                for card in trump_cards
                if card.get_order(trump_suit)
                > highest_opponent_trump.get_order(trump_suit)
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
            remaining cards is preserved). The receiver is unchanged.

        Raises:
            IllegalPlayError: Carrying the offending :class:`PlayRuleViolation`
                — ``OUT_OF_TURN`` (wrong player, or the phase is over),
                ``CARD_NOT_IN_HAND`` (the card is not held), or one of
                ``MUST_FOLLOW_SUIT`` / ``MUST_TRUMP`` / ``MUST_OVERTRUMP``.
        """

        player, card = play

        actor = self.to_act
        if actor is None or player is not actor:
            raise IllegalPlayError(card, PlayRuleViolation.OUT_OF_TURN, ())

        hand = self.hand_of(player)
        legal = self.legal_actions(player)

        if card not in hand:
            raise IllegalPlayError(
                card, PlayRuleViolation.CARD_NOT_IN_HAND, legal
            )
        if card not in legal:
            reason = _classify_violation(
                player, card, self._trump_suit, self.current_trick, hand
            )
            raise IllegalPlayError(card, reason, legal)

        seat = self._seat_of(player)
        index = hand.index(card)
        new_hand = hand[:index] + hand[index + 1:]
        new_hands = self.hands[:seat] + (new_hand,) + self.hands[seat + 1:]
        return PlayState(
            contract=self.contract,
            players=self.players,
            hands=new_hands,
            plays=self.plays + (play,),
        )

    # ------------------------------------------------------------------
    # Determinization fork
    # ------------------------------------------------------------------

    def with_hands(
        self, hands: tuple[tuple["Card", ...], ...]
    ) -> "PlayState":
        """Fork this state onto replacement hands.

        The public history — contract, players, plays — is preserved; only
        the per-seat hands change. This is the determinization primitive a
        search-based AI uses to sample worlds consistent with what it has
        seen.

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
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @property
    def _trump_suit(self) -> Optional["Suit"]:
        """The contract's trump suit, or ``None`` when there is no contract."""

        return self.contract.suit if self.contract else None

    def _seat_of(self, player: "BasePlayer") -> int:
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

    def _leader_of_current_trick(self) -> "BasePlayer":
        """Return the player who led the in-progress trick.

        Trick 0 is led by ``players[0]``; every later trick is led by the
        winner of the trick before it.
        """

        trick_index = self.trick_number
        if trick_index == 0:
            return self.players[0]
        return self.trick_winners[trick_index - 1]


def _higher_trumps_than_played(
    trumps_in_hand: tuple["Card", ...],
    plays: tuple[Play, ...],
    trump_suit: "Suit",
) -> tuple["Card", ...]:
    """Return the held trumps that beat every trump already in ``plays``.

    Used by the over-trump rule when the led suit is itself trump. Returns
    an empty tuple when no trump has been played yet (defensive; the rule
    only calls this once trump is on the table) or when no held trump beats
    the current best.

    Args:
        trumps_in_hand: The candidate trumps from the player's hand.
        plays: The plays of the current trick.
        trump_suit: The trump suit to rank by.

    Returns:
        The subset of ``trumps_in_hand`` outranking the best trump played.
    """

    best_so_far = None
    for _, card in plays:
        if card.suit != trump_suit:
            continue
        if best_so_far is None or card.get_order(trump_suit) > best_so_far.get_order(
            trump_suit
        ):
            best_so_far = card
    if best_so_far is None:
        return ()
    return tuple(
        card
        for card in trumps_in_hand
        if card.get_order(trump_suit) > best_so_far.get_order(trump_suit)
    )


def _highest_opponent_trump(
    plays: tuple[Play, ...], player_team: "Team", trump_suit: Optional["Suit"]
) -> Optional["Card"]:
    """Return the highest trump an opponent of ``player_team`` has played.

    Args:
        plays: The plays of the current trick.
        player_team: The team whose opponents' trumps we scan for.
        trump_suit: The trump suit to rank by; ``None``/``NO_TRUMP`` yields
            no trumps at all.

    Returns:
        The highest opposing trump card, or ``None`` if none was played.
    """

    highest = None
    for trick_player, card in plays:
        if card.suit != trump_suit or trick_player.team == player_team:
            continue
        if highest is None or card.get_order(trump_suit) > highest.get_order(
            trump_suit
        ):
            highest = card
    return highest


def _classify_violation(
    player: "BasePlayer",
    card: "Card",
    trump_suit: Optional["Suit"],
    trick: tuple[Play, ...],
    hand: tuple["Card", ...],
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

    lead_suit = trick[0].card.suit
    lead_suit_cards = [c for c in hand if c.suit == lead_suit]

    # Held the led suit. Trump led + a too-low trump is an over-trump
    # failure; anything else off-suit is a follow failure.
    if lead_suit_cards:
        if trump_suit and lead_suit == trump_suit and card.suit == trump_suit:
            return PlayRuleViolation.MUST_OVERTRUMP
        return PlayRuleViolation.MUST_FOLLOW_SUIT

    # Void in the led suit (partner-master plays are legal, so never reach
    # here). An opponent already ruffed and we under-trumped → over-trump
    # failure; otherwise we discarded instead of trumping.
    highest_opponent_trump = _highest_opponent_trump(
        trick, player.team, trump_suit
    )
    if highest_opponent_trump is not None and card.suit == trump_suit:
        return PlayRuleViolation.MUST_OVERTRUMP
    return PlayRuleViolation.MUST_TRUMP
