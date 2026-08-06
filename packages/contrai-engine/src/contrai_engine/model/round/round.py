"""Round class for the contrée card game.

This class represents a complete round of the card game from dealing to
scoring.
"""

import itertools
import logging
from typing import Optional, Dict, List, TYPE_CHECKING

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.contract import Contract
from contrai_core.play import Play, PlayState
from contrai_core.rules import rules_for
from contrai_core.team_side import TeamSide
from contrai_core.trick import Trick
from contrai_core.types import Rank

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

    def __init__(self, players_order: List[Player], dealer: Player, deck: Deck, round_number: int):
        """
        Initialize a round with the given parameters.

        Args:
            players_order: List of players in playing order for this round
            dealer: The dealer for this round
            deck: The deck to use for dealing cards
            round_number: The current round number
        """
        self.players_order = players_order
        self.dealer = dealer
        self.deck = deck
        self.round_number = round_number

        # Round state
        self.contract: Optional[Contract] = None
        # The auction that produced ``contract``, retained by
        # ``manage_bidding`` once the bidding phase closes. ``None`` until
        # then. Kept so the play phase can attach the bidding history to
        # the observation it hands each card-play strategy.
        self.auction: Auction | None = None
        # The immutable core play-phase state — authoritative for whose
        # turn it is, which cards are legal, and each seat's remaining
        # cards. Seeded at the start of play (by ``play_all_tricks``, or
        # lazily by ``play_trick`` when driven directly); ``None`` before
        # play begins. The engine mirrors it onto ``current_trick`` and the
        # players' hands so the view keeps reading the classic engine
        # objects. AI seats instead read the frozen ``PlayObservation``
        # projected from this state.
        self.play_state: PlayState | None = None
        self.tricks: List[Trick] = []
        self.current_trick: Optional[Trick] = None
        self.last_trick_winner: Optional[Player] = None
        self.team_tricks: Dict[TeamSide, List[Trick]] = {}
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

        # Belote / rebelote announcement state. ``belote_holder`` is the
        # unique player holding both the K and the Q of trump at deal time
        # (None when no one has both, or when the contract is NO_TRUMP /
        # passed). ``belote_state`` tracks which of the two cards they
        # have already played: missing → not yet announced; "belote" →
        # one played; "rebelote" → both played.
        self.belote_holder: Optional[Player] = None
        self.belote_state: Dict[Player, str] = {}

        # Initialize the trick piles, one per seated side. Keying off the
        # seats rather than the Team roster objects means the piles exist
        # before teams are wired up.
        if players_order:
            sides = {player.position.team_side for player in players_order}
            self.team_tricks = {side: [] for side in sides}

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

        Args:
            view: Optional view that drives human input and pacing
                hooks.

        Returns:
            The established :class:`Contract`, or ``None`` if every
            player passed.
        """

        auction = Auction.empty()
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
            self._detect_belote_holder()
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

    def _is_belote_event(self, player: Player, card) -> bool:
        """True if *player* playing *card* counts toward a belote announcement."""
        if self.belote_holder is None or self.contract is None:
            return False
        if player is not self.belote_holder:
            return False
        rules = rules_for(self.contract.suit)
        return rules.is_trump(card.suit) and card.rank in (
            Rank.KING,
            Rank.QUEEN,
        )

    def _transition_belote_state(self, player: Player) -> Optional[str]:
        """Advance the belote_state machine and return the new state name.

        Returns ``"belote"`` if this is the first of the K+Q pair played,
        ``"rebelote"`` if it's the second, or ``None`` if the player has
        already fired both (defensive — shouldn't happen, since each card
        is unique).
        """
        current = self.belote_state.get(player)
        if current is None:
            self.belote_state[player] = "belote"
            return "belote"
        if current == "belote":
            self.belote_state[player] = "rebelote"
            return "rebelote"
        return None

    def _detect_belote_holder(self) -> None:
        """Snapshot which player (if any) holds the K + Q of trump.

        Belote/rebelote is a per-round, per-holder narrative event:
        whoever holds both cards announces ``Belote`` on the first they
        play and ``Rebelote`` on the second. No-trump contracts have no
        belote.
        """
        trump = self.contract.suit if self.contract else None
        # The rules object knows which suits can carry a belote — always
        # real card suits, so ``has_card`` below (which builds a Card from
        # what it is handed) never sees a suitless trump. Empty under a
        # no-trump contract: no belote to hold.
        for suit in rules_for(trump).belote_suits:
            for player in self.players_order:
                has_king = player.hand.has_card(suit, Rank.KING)
                has_queen = player.hand.has_card(suit, Rank.QUEEN)
                if has_king and has_queen:
                    self.belote_holder = player
                    return
        self.belote_holder = None

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

    def play_trick(self, view=None) -> None:
        """
        Play a single trick.

        The trick is driven by the immutable core :class:`PlayState`: the
        active player, the legal cards, and each play's effect on the hands
        all come from it. The engine keeps two mutable mirrors in
        lock-step — :attr:`current_trick` and each ``player.hand`` — for the
        view, which still reads the classic engine objects. Each AI seat is
        instead handed the frozen :class:`PlayObservation` projected from
        the play state, and derives its own card tracking from that.

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
            )

        # The mutable mirror of the trick. Its object identity must persist
        # across all four ``on_card_played`` notifications and the
        # ``on_trick_complete`` call, so it is built once here and never
        # rebuilt mid-trick.
        self.current_trick = Trick()

        # Trump is fixed for the whole trick; resolve it once for the
        # final winner call.
        trump_suit = self.contract.suit if self.contract else None

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
                card = view.request_card_action(
                    player, self.current_trick, self.contract, playable_cards
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

            # Re-mirror the hands from the new state, then mirror the play
            # onto the trick. Model bookkeeping stays ahead of view pacing.
            self._sync_hands()
            self.current_trick.add_play(player, card)

            # Notify the view that a card just landed on the table.
            # Lets interactive views render the AI action and pause.
            if view is not None and hasattr(view, 'on_card_played'):
                view.on_card_played(player, card, self.current_trick)

            # Belote / rebelote announcement. Fires only when the holder
            # plays one of the K/Q of trump. Each card fires at most once.
            if self._is_belote_event(player, card):
                kind = self._transition_belote_state(player)
                if kind is not None and view is not None and hasattr(
                    view, 'on_belote_announced'
                ):
                    view.on_belote_announced(player, kind, self)

        # Determine trick winner. Who wins is a pure rule of the trick
        # given trump, so we delegate to contrai-core rather than duplicate
        # the comparison here. The contract carries the authoritative trump
        # suit (None only defensively, before a contract is established).
        winner = self.current_trick.get_current_winner(trump_suit)
        self.last_trick_winner = winner

        # Add trick to the tricks list and to the winner's side pile
        if self.current_trick:
            self.tricks.append(self.current_trick)
            if winner:
                self.team_tricks[winner.position.team_side].append(
                    self.current_trick
                )

            # The point total costs a real sum over the trick's four cards,
            # unlike a bare lazy %s argument — guard it explicitly so a
            # disabled run never pays for it.
            if logger.isEnabledFor(logging.DEBUG):
                rules = rules_for(trump_suit)
                trick_points = sum(
                    rules.points(card)
                    for _, card in self.current_trick.get_plays()
                )
                logger.debug(
                    "trick %d complete: winner %s, %d points",
                    len(self.tricks),
                    winner.position if winner else None,
                    trick_points,
                )

        # Add cards back to deck (last card played first, then reverse order)
        if self.current_trick and hasattr(self.current_trick, 'get_plays'):
            trick_cards = [card for _, card in self.current_trick.get_plays()]
            trick_cards.reverse()  # Last card played becomes first to be added back
            self.deck.add_cards(trick_cards)

        # Notify the view that a trick just completed (optional view hook).
        # Used by interactive views (e.g. RichView) to pause for "Press Enter"
        # between tricks. Skipped silently when no such hook exists.
        if view is not None and hasattr(view, 'on_trick_complete'):
            view.on_trick_complete(self.current_trick, winner, self)

        return

    def play_all_tricks(self, view=None) -> Dict[TeamSide, List[Trick]]:
        """
        Play all 8 tricks of the round.

        Args:
            view: Optional view for human player interaction

        Returns:
            Dict mapping each team side to the tricks it won
        """
        # Initialize team tricks tracking
        self.last_trick_winner = None

        # Seed the authoritative play state from the fresh deal — a
        # validated start (4 seats, 8 distinct cards each). The very Card
        # objects held in the players' hands flow into it, so the view can
        # keep matching playable cards by identity.
        self.play_state = PlayState.start(
            self.contract,
            tuple(self.players_order),
            tuple(tuple(player.hand) for player in self.players_order),
        )

        # Play 8 tricks
        for _ in range(8):
            self.play_trick(view)

        return self.team_tricks

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
        result = score_round(self)
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
