"""Round class for the contrée card game.

This class represents a complete round of the card game from dealing to
scoring.
"""

import itertools
import logging
from typing import Optional, Dict, List, Sequence, TYPE_CHECKING

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.contract import Contract
from contrai_core.play import Play, PlayState
from contrai_core.rule_config import AllTrumpBelote, RuleConfig
from contrai_core.rules import rules_for
from contrai_core.team_side import TeamSide
from contrai_core.types import Rank, Suit, TrumpVariant

from .scoring import UnannouncedSlam, score_round

if TYPE_CHECKING:
    from ..player import Player
    from contrai_core.team import Team
    from contrai_core.deck import Deck

# Logging is infrastructure, not presentation: this module never attaches a
# handler or configures a level itself (see contrai_engine.log_setup) — it
# only ever emits through the standard logging module, so the calls below
# are silent no-ops for any interface that hasn't opted into debug mode.
logger = logging.getLogger(__name__)


class Round:
    """
    Represents a complete round of the card game from dealing to scoring.

    Manages the complete round lifecycle including bidding phase coordination,
    trick sequence management, and round score calculation.
    """

    def __init__(
        self,
        players_order: List[Player],
        dealer: Player,
        deck: Deck,
        round_number: int,
        rules: RuleConfig | None = None,
    ):
        """
        Initialize a round with the given parameters.

        Args:
            players_order: List of players in playing order for this round
            dealer: The dealer for this round
            deck: The deck to use for dealing cards
            round_number: The current round number
            rules: The table ruleset this round is played under. ``None``
                (the default) means the §9 catalogue defaults.
        """
        self.players_order = players_order
        self.dealer = dealer
        self.deck = deck
        self.round_number = round_number
        # The table ruleset, normally inherited from the Game. It is
        # seeded into the core play state and named to the scorer, so
        # both sides of the round read the same ruleset object — no knob
        # of it changes a decision yet.
        self.rules: RuleConfig = rules if rules is not None else RuleConfig()

        # Round state
        self.contract: Optional[Contract] = None
        # The auction that produced ``contract``, retained by
        # ``manage_bidding`` once the bidding phase closes. ``None`` until
        # then. Kept so the play phase can attach the bidding history to
        # the observation it hands each card-play strategy.
        self.auction: Auction | None = None
        # The immutable core play-phase state — the single source of
        # truth for the whole play phase: whose turn it is, which cards
        # are legal, each seat's remaining cards, which tricks have
        # completed, who won them and what each side has captured.
        # Seeded at the start of play (by ``play_all_tricks``, or lazily
        # by ``play_trick`` when driven directly); ``None`` before play
        # begins. The engine mirrors it onto the players' hands so the
        # view keeps reading the classic engine objects; everything else
        # about the play phase is read off it directly. AI seats instead
        # read the frozen ``PlayObservation`` projected from this state.
        self.play_state: PlayState | None = None
        self.round_scores: Dict[TeamSide, int] = {}
        # Single source of truth for the contract outcome, set by
        # ``calculate_round_scores``. ``None`` until scored (or when the
        # round was all-passed). The view reads this rather than
        # re-deriving "made" from the scores — a failed declarer can
        # still score a non-zero Belote bonus, so "round_score > 0" is
        # not a reliable made/failed signal.
        self.contract_made: Optional[bool] = None
        # Unannounced-Slam marker, set by ``calculate_round_scores``.
        # ``None`` when the round was not an unannounced Slam; otherwise
        # the matching :class:`UnannouncedSlam` member — ``SLAM`` (the
        # declaring *team* swept all 8 tricks) or ``GRAND_SLAM`` (the
        # contracting *player personally* won them all). Only set for
        # un-doubled numeric contracts — the path that swaps the
        # 162-point pile for a flat 250 substitute. The view reads this to
        # render the 250 and its explanatory tag.
        self.unannounced_slam: Optional[UnannouncedSlam] = None

        #: Every K + Q pair held at deal time, holder -> the suits they
        #: pair in. At most one entry with one suit outside all trump —
        #: a suit contract has a single trump suit and no trump has none.
        #: Under the all-trump ``four`` regime up to four pairs live in
        #: one deal and a seat can pair in two suits.
        self.belote_pairs: Dict[Player, tuple[Suit, ...]] = {}
        #: Announcement progress per pair: (holder, suit) -> "belote" |
        #: "rebelote". Keyed by the pair, not the holder, because a seat
        #: can be mid-announcement in two suits at once. Missing → not
        #: yet announced; "belote" → one of the two played; "rebelote"
        #: → both played.
        self.belote_state: Dict[tuple[Player, Suit], str] = {}
        #: The pairs in the order they were first announced. Under the
        #: ``single`` regime the head of this list is the one that marks.
        self.belote_order: List[tuple[Player, Suit]] = []

    def deal_cards(self):
        """
        Deal cards to all players in the proper order.
        Dealer gets cards last.
        """
        self.deck.deal(self.players_order)

    def manage_bidding(self, view=None) -> Optional[Contract]:
        """Handle the complete bidding phase.

        Drives an :class:`Auction` through the standard cyclic
        ``players_order``. Each iteration:

        1. Look up the legal actions for the active player. When the
           only legal action is :class:`PassBid` (partner just doubled
           or redoubled, or a pass already closed the redouble window)
           the engine auto-applies it without prompting the player or
           the view.
        2. Otherwise consult ``player.choose_bid`` and — for the
           human seat — ``view.request_bid_action`` to gather the
           player's chosen :class:`Bid`.
        3. Apply the bid via :meth:`Auction.apply`. An illegal bid
           raises :class:`IllegalBidError` — there is no silent
           "force a Pass on illegal" fallback any more.

        The auction runs under this round's :attr:`rules`, which is what
        decides the trump choices on offer (the four suits, plus no trump
        and all trump when ``extended_trump_choices`` is on) and where
        each mode's numeric ladder stops.

        Args:
            view: Optional view that drives human input and pacing
                hooks.

        Returns:
            The established :class:`Contract`, or ``None`` if every
            player passed.
        """

        auction = Auction.empty(rules=self.rules)
        player_iter = itertools.cycle(self.players_order)

        while not auction.is_terminal():
            player = next(player_iter)
            legal = auction.legal_actions(player)
            if len(legal) == 1:
                # Pass is the only legal action — skip both the AI
                # strategy and the human prompt entirely. Covers the
                # "partner doubled / redoubled" UX as a special case
                # of the general "no real choice" rule.
                bid = legal[0]
            else:
                bid = self._gather_bid(player, auction, view)
            auction = auction.apply(bid)
            # Notify the view that a bid was just registered. Used by
            # interactive views to render the AI action and pause
            # briefly before the next bidder.
            if view is not None and hasattr(view, 'on_bid_made'):
                view.on_bid_made(player, bid, list(auction.bids))

        self.auction = auction
        self.contract = auction.contract()
        if self.contract is not None:
            logger.debug("contract fixed: %s", self.contract)
            self._detect_belote_pairs()
            # Bookmark the contract in the event log so the start of
            # play is clearly delimited.
            if view is not None and hasattr(view, 'on_contract_established'):
                view.on_contract_established(self)

        return self.contract

    def _gather_bid(
        self,
        player: Player,
        auction: Auction,
        view,
    ) -> Bid:
        """Ask ``player`` for a :class:`Bid`, consulting ``view`` for humans.

        Args:
            player: The active bidder.
            auction: The current auction state. Passed verbatim to
                both ``player.choose_bid`` and (for humans)
                ``view.request_bid_action``.
            view: The optional view.

        Returns:
            The player's chosen :class:`Bid`.

        Raises:
            RuntimeError: If neither the player nor the view produced
                a bid — that's an engine wiring bug (e.g. a
                :class:`HumanPlayer` with no view attached).
        """

        bid: Optional[Bid] = None
        if hasattr(player, 'choose_bid'):
            bid = player.choose_bid(auction)
        if (
            view is not None
            and getattr(player, 'is_human', False)
            and hasattr(view, 'request_bid_action')
        ):
            bid = view.request_bid_action(player, auction)
        if bid is None:
            raise RuntimeError(
                f"No bid produced for {player.position}: "
                f"choose_bid returned None and the view did not intercept."
            )
        return bid

    def _belote_suits(self) -> tuple[Suit, ...]:
        """The suits a belote can live in at this table, this round.

        The rules object answers where a belote is *possible* — the trump
        suit at a suit contract, nothing at no trump, every suit at all
        trump. The ``none`` regime then removes all trump's belotes
        entirely: a table playing it has no belote to announce, not one
        that fails to score (contree-domain.md §6.6, §9.2).

        Returns:
            The suits to scan hands for a K + Q pair in. Always real card
            suits, so the ``has_card`` calls in
            :meth:`_detect_belote_pairs` — which build a
            :class:`~contrai_core.Card` from what they are handed — never
            see a suitless trump.
        """

        trump = self.contract.suit if self.contract else None
        if (trump is TrumpVariant.ALL_TRUMP
                and self.rules.all_trump_belote is AllTrumpBelote.NONE):
            return ()
        return rules_for(trump).belote_suits

    def _detect_belote_pairs(self) -> None:
        """Snapshot every K + Q pair held at deal time.

        Belote/rebelote is a per-pair narrative event: the holder
        announces ``Belote`` on the first of the two cards they play and
        ``Rebelote`` on the second. Scanning every seat rather than
        stopping at the first is what the all-trump ``four`` regime needs
        — up to four pairs live in one deal, and one seat may hold two.
        """

        suits = self._belote_suits()
        self.belote_pairs = {}
        for player in self.players_order:
            paired = tuple(
                suit for suit in suits
                if player.hand.has_card(suit, Rank.KING)
                and player.hand.has_card(suit, Rank.QUEEN)
            )
            if paired:
                self.belote_pairs[player] = paired

    def _is_belote_event(self, player: Player, card) -> bool:
        """True if ``player`` playing ``card`` announces a belote.

        The predicate keys on the *pair*, not on trumpness: at all trump
        every King and Queen is a trump King and Queen, so a trumpness
        test would fire on a holder's unpaired King in a fourth suit.
        """

        if self.contract is None:
            return False
        return (
            card.suit in self.belote_pairs.get(player, ())
            and card.rank in (Rank.KING, Rank.QUEEN)
        )

    def _transition_belote_state(
        self, player: Player, suit: Suit
    ) -> Optional[str]:
        """Advance one pair's announcement state and return its new name.

        Returns ``"belote"`` on the first card of the pair, ``"rebelote"``
        on the second, or ``None`` if both have already fired (defensive
        — each card is unique). First announcements are appended to
        :attr:`belote_order`, which is what the ``single`` regime reads.

        Args:
            player: The pair's holder.
            suit: The suit the pair lives in.

        Returns:
            The announcement name to narrate, or ``None``.
        """

        key = (player, suit)
        current = self.belote_state.get(key)
        if current is None:
            self.belote_state[key] = "belote"
            self.belote_order.append(key)
            return "belote"
        if current == "belote":
            self.belote_state[key] = "rebelote"
            return "rebelote"
        return None

    def _scoring_belotes(self) -> tuple[tuple[Player, Suit], ...]:
        """The held pairs that actually mark, under this table's regime.

        Outside all trump at most one pair exists and it always marks —
        the regime knob is an all-trump rule (§6.6). Under all trump:
        ``none`` is already empty (no pair was ever detected), ``four``
        marks every pair, and ``single`` marks only the first one
        announced in play, whichever team announced it.

        Returns:
            The ``(holder, suit)`` pairs worth 20 points each.
        """

        held = tuple(
            (player, suit)
            for player, suits in self.belote_pairs.items()
            for suit in suits
        )
        if (self.contract is None
                or self.contract.suit is not TrumpVariant.ALL_TRUMP
                or self.rules.all_trump_belote is AllTrumpBelote.FOUR):
            return held
        return tuple(self.belote_order[:1])

    @property
    def belote_counts_by_side(self) -> Dict[TeamSide, int]:
        """How many belotes each side marks this round.

        Returns:
            Every :class:`~contrai_core.TeamSide` member as a key, so
            callers index directly; a side marking none maps to ``0``.
            The scorer multiplies each by 20.
        """

        counts = {side: 0 for side in TeamSide}
        for player, _suit in self._scoring_belotes():
            counts[player.position.team_side] += 1
        return counts

    def _sync_hands(self) -> None:
        """Re-mirror the players' hands from the authoritative play state.

        :attr:`play_state` is the single source of truth for each seat's
        remaining cards; the players' :class:`~contrai_core.Hand` objects
        are mutable mirrors kept in lock-step so the view — which still
        reads ``player.hand`` — stays correct. Clearing and re-extending
        the same ``Hand`` in place preserves its object identity, the card
        object references, and their relative order.
        """
        for player in self.players_order:
            player.hand.clear()
            player.hand.extend(self.play_state.hand_of(player))

    def _trick_after_play(self) -> Sequence[Play]:
        """The trick on the table, read **after** a card has been applied.

        Normally the in-progress trick. Once its fourth card lands the
        play state has *already* closed it — ``current_trick`` is empty
        again and the trick has moved into ``completed_tricks`` — yet
        that is precisely the moment the view is asked to render it, so
        the just-closed trick is handed back instead. An empty tuple is
        falsy, which is what picks between the two.

        The "after a card has been applied" precondition is what makes
        that choice sound: an empty ``current_trick`` can then only mean
        the trick just closed. Before a play — prompting a seat that is
        on lead — an empty current trick means the opposite, so those
        call sites read ``play_state.current_trick`` directly.
        """
        return (
            self.play_state.current_trick
            or self.play_state.completed_tricks[-1]
        )

    def play_trick(self, view=None) -> None:
        """
        Play a single trick.

        The trick is driven by the immutable core :class:`PlayState`: the
        active player, the legal cards, each play's effect on the hands,
        and the trick's winner all come from it. The one mutable mirror
        the engine still keeps is each ``player.hand``, held in lock-step
        for the view. Each AI seat is instead handed the frozen
        :class:`PlayObservation` projected from the play state, and
        derives its own card tracking from that.

        Args:
            view: Optional view for human player interaction
        """
        # Lazy-seed guard: tests drive ``play_trick`` directly without going
        # through ``play_all_tricks``. Seed from the current contract,
        # seating and hands via the bare (unvalidated) constructor —
        # mirroring the Auction idiom — since a directly injected mid-round
        # hand need not hold the full 8 cards.
        if self.play_state is None:
            self.play_state = PlayState(
                self.contract,
                tuple(self.players_order),
                tuple(tuple(player.hand) for player in self.players_order),
                rules=self.rules,
            )

        # Four plays make a trick. The active player and the legal cards
        # come from the authoritative play state, never from local
        # bookkeeping.
        for _ in range(4):
            player = self.play_state.to_act
            playable_cards = list(self.play_state.legal_actions(player))

            # Source the card. A human's input is driven by the view —
            # the model can't block on terminal I/O under MVC — so we
            # ask the view directly and never call choose_card, whose
            # HumanPlayer override only returns None. AI players (and any
            # viewless/headless player) are asked via choose_card, with a
            # bare first-playable fallback for objects that lack it.
            if view is not None and getattr(player, 'is_human', False):
                # Nothing has been applied yet, so the in-progress trick
                # is exactly what the seat is looking at — empty when
                # they are on lead.
                card = view.request_card_action(
                    player,
                    self.play_state.current_trick,
                    self.contract,
                    playable_cards,
                )
            elif hasattr(player, 'choose_card'):
                # Hand the AI a frozen observation projected from the
                # authoritative play state — its own hand, legal cards, and
                # the public trick history — attaching the retained
                # auction's bids (empty until the auction is set).
                card = player.choose_card(
                    self.play_state.observe(
                        player,
                        bids=self.auction.bids if self.auction else (),
                    )
                )
            else:
                # Simple fallback: play first playable card
                card = playable_cards[0] if playable_cards else None

            # Advance the authoritative state. The core enforces turn order
            # and legality itself: an out-of-turn, not-held, or
            # obligation-breaking card raises IllegalPlayError rather than
            # being silently corrected — so an absent/illegal card from
            # choose_card / request_card_action fails loudly here.
            self.play_state = self.play_state.apply(Play(player, card))

            # Re-mirror the hands from the new state. Model bookkeeping
            # stays ahead of view pacing.
            self._sync_hands()

            # Notify the view that a card just landed on the table.
            # Lets interactive views render the AI action and pause.
            if view is not None and hasattr(view, 'on_card_played'):
                view.on_card_played(player, card, self._trick_after_play())

            # Belote / rebelote announcement. Fires only when a seat
            # plays a K or Q of a pair it holds — at all trump every K/Q
            # is a trump K/Q, so trumpness alone is not the test. Each
            # card fires at most once.
            if self._is_belote_event(player, card):
                kind = self._transition_belote_state(player, card.suit)
                if kind is not None and view is not None and hasattr(
                    view, 'on_belote_announced'
                ):
                    view.on_belote_announced(player, kind, self)

        # Who won is a pure rule of the trick given trump, and the play
        # state has already applied it — the winner of the trick just
        # closed is the last entry of its per-trick winners.
        completed = self._trick_after_play()
        winner = self.play_state.trick_winners[-1]

        # The point total costs a real sum over the trick's four cards,
        # unlike a bare lazy %s argument — guard it explicitly so a
        # disabled run never pays for it.
        if logger.isEnabledFor(logging.DEBUG):
            rules = rules_for(self.contract.suit if self.contract else None)
            trick_points = sum(rules.points(play.card) for play in completed)
            logger.debug(
                "trick %d complete: winner %s, %d points",
                len(self.play_state.completed_tricks),
                winner.position if winner else None,
                trick_points,
            )

        # Add cards back to deck (last card played first, then reverse order)
        trick_cards = [play.card for play in completed]
        trick_cards.reverse()  # Last card played becomes first to be added back
        self.deck.add_cards(trick_cards)

        # Notify the view that a trick just completed (optional view hook).
        # Used by interactive views (e.g. RichView) to pause for "Press Enter"
        # between tricks. Skipped silently when no such hook exists.
        if view is not None and hasattr(view, 'on_trick_complete'):
            view.on_trick_complete(completed, winner, self)

        return

    def play_all_tricks(self, view=None) -> None:
        """
        Play all 8 tricks of the round.

        The played-out round is left on :attr:`play_state`, which every
        consumer — scoring, the recap, the screens — reads directly.

        Args:
            view: Optional view for human player interaction
        """
        # Seed the authoritative play state from the fresh deal — a
        # validated start (4 seats, 8 distinct cards each). The very Card
        # objects held in the players' hands flow into it, so the view can
        # keep matching playable cards by identity.
        self.play_state = PlayState.start(
            self.contract,
            tuple(self.players_order),
            tuple(tuple(player.hand) for player in self.players_order),
            rules=self.rules,
        )

        # Play 8 tricks
        for _ in range(8):
            self.play_trick(view)

    def calculate_round_scores(self) -> Dict[TeamSide, int]:
        """
        Calculate scores for this round.

        Thin lifecycle wrapper around the pure :func:`scoring.score_round`
        transformation: it runs the scoring rules over the round's final
        state and publishes the three result attributes the view reads —
        :attr:`round_scores`, :attr:`contract_made` (the canonical
        made/failed signal), and :attr:`unannounced_slam`. The scoring
        shapes (numeric, unannounced Slam, doubled winner-takes-all,
        Slam / Solo Slam) and the Belote rule all live in
        :mod:`scoring`.

        Returns:
            Dict: Round scores, keyed by team side
        """
        result = score_round(self, rules=self.rules)
        self.round_scores = result.scores
        self.contract_made = result.contract_made
        self.unannounced_slam = result.unannounced_slam
        return self.round_scores

    def handle_failed_contract(self) -> Dict[TeamSide, int]:
        """
        Manage cards when all players pass.

        Returns:
            Dict: Zero scores for both team sides
        """
        # Put all players' cards back in deck (8 cards per player)
        for player in self.players_order:
            self.deck.add_cards(player.hand)
            player.hand.clear()

        # Return zero scores
        sides = {player.position.team_side for player in self.players_order}
        self.round_scores = {side: 0 for side in sides}
        return self.round_scores
