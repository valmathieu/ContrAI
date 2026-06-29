# Round class for the contrée card game.
# This class represents a complete round of the card game from dealing to scoring.

import itertools
from typing import Optional, Dict, List, TYPE_CHECKING

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.contract import Contract
from contrai_core.exceptions import IllegalPlayError
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

from . import legality
from .scoring import UnannouncedSlam, score_round

if TYPE_CHECKING:
    from ..player import Player
    from contrai_core.team import Team
    from contrai_core.deck import Deck


class Round:
    """
    Represents a complete round of the card game from dealing to scoring.

    Manages the complete round lifecycle including bidding phase coordination,
    trick sequence management, and round score calculation.
    """

    def __init__(self, players_order: List['Player'], dealer: 'Player', deck: 'Deck', round_number: int):
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
        self.tricks: List[Trick] = []
        self.current_trick: Optional[Trick] = None
        self.last_trick_winner: Optional['Player'] = None
        self.team_tricks: Dict[str, List[Trick]] = {}
        self.round_scores: Dict[str, int] = {}
        # Single source of truth for the contract outcome, set by
        # ``calculate_round_scores``. ``None`` until scored (or when the
        # round was all-passed). The view reads this rather than
        # re-deriving "made" from the scores — a failed declarer can
        # still score a non-zero Belote bonus, so "round_score > 0" is
        # not a reliable made/failed signal.
        self.contract_made: Optional[bool] = None
        # Unannounced-capot marker, set by ``calculate_round_scores``.
        # ``None`` when the round was not an unannounced capot; otherwise
        # the matching :class:`UnannouncedSlam` member — ``SLAM`` (the
        # declaring *team* swept all 8 tricks) or ``GRAND_SLAM`` (the
        # contracting *player personally* won them all). Only set for
        # un-doubled numeric contracts — the path that swaps the
        # 162-point pile for a flat 250 substitute. The view reads this to
        # render the 250 and its explanatory tag.
        self.unannounced_capot: Optional[UnannouncedSlam] = None

        # Belote / rebelote announcement state. ``belote_holder`` is the
        # unique player holding both the K and the Q of trump at deal time
        # (None when no one has both, or when the contract is NO_TRUMP /
        # passed). ``belote_state`` tracks which of the two cards they
        # have already played: missing → not yet announced; "belote" →
        # one played; "rebelote" → both played.
        self.belote_holder: Optional['Player'] = None
        self.belote_state: Dict['Player', str] = {}

        # Initialize team tricks dictionary
        if players_order:
            teams = set(player.team for player in players_order)
            self.team_tricks = {team.name: [] for team in teams}

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

        self.contract = auction.contract()
        if self.contract is not None:
            self._detect_belote_holder()
            # Bookmark the contract in the event log so the start of
            # play is clearly delimited.
            if view is not None and hasattr(view, 'on_contract_established'):
                view.on_contract_established(self)

        return self.contract

    def _gather_bid(
        self,
        player: 'Player',
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

    def _is_belote_event(self, player: 'Player', card) -> bool:
        """True if *player* playing *card* counts toward a belote announcement."""
        if self.belote_holder is None or self.contract is None:
            return False
        if player is not self.belote_holder:
            return False
        trump = self.contract.suit
        return card.suit == trump and card.rank in (Rank.KING, Rank.QUEEN)

    def _transition_belote_state(self, player: 'Player') -> Optional[str]:
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
        if self.contract is None or self.contract.suit == Suit.NO_TRUMP:
            self.belote_holder = None
            return
        trump = self.contract.suit
        for player in self.players_order:
            has_king = player.hand.has_card(trump, Rank.KING)
            has_queen = player.hand.has_card(trump, Rank.QUEEN)
            if has_king and has_queen:
                self.belote_holder = player
                return
        self.belote_holder = None

    def play_trick(self, view=None) -> Optional['Player']:
        """
        Play a single trick and return winner.

        Args:
            view: Optional view for human player interaction

        Returns:
            Player: The winner of the trick or None if no winner
        """
        self.current_trick = Trick()
        trick_leader = self.players_order[0] if self.last_trick_winner is None else self.last_trick_winner

        # Determine the order for this trick (winner of last trick leads)
        leader_idx = self.players_order.index(trick_leader)
        trick_order = []
        for i in range(4):
            trick_order.append(self.players_order[(leader_idx + i) % 4])

        # Each player plays a card
        for player in trick_order:
            # Get the playable cards for this player
            playable_cards = legality.get_playable_cards(
                player, self.contract, self.current_trick
            )

            if hasattr(player, 'choose_card'):
                # AI player or player with choose_card method
                # Pass playable cards to help AI make legal moves
                card = player.choose_card(self.current_trick, self.contract, playable_cards)
            else:
                # Simple fallback: play first playable card
                card = playable_cards[0] if playable_cards else None

            # If view provided and player is human, use view for input
            if view and hasattr(player, 'is_human') and player.is_human:
                card = view.request_card_action(player, self.current_trick, self.contract, playable_cards)

            # Validate that the chosen card is legal. An illegal card is
            # surfaced as a loud failure (IllegalPlayError) rather than
            # silently corrected to a legal one: choose_card /
            # request_card_action are contracted to return a card from
            # playable_cards, so a violation here is a wiring bug we want
            # to see, not paper over.
            played_card = None
            if card and card in playable_cards:  # playable ⊆ hand, so in-hand is implied
                player.hand.remove(card)
                self.current_trick.add_play(player, card)
                played_card = card
            elif card:  # truthy but illegal → loud failure
                raise IllegalPlayError(
                    card,
                    legality.classify_play_violation(
                        player, card, self.contract, self.current_trick
                    ),
                    playable_cards,
                    context=f"{getattr(player, 'position', player)} card play",
                )
            # card falsy → unchanged (out of scope)

            # Notify the view that a card just landed on the table.
            # Lets interactive views render the AI action and pause.
            if (
                played_card is not None
                and view is not None
                and hasattr(view, 'on_card_played')
            ):
                view.on_card_played(player, played_card, self.current_trick)

            # Belote / rebelote announcement. Fires only when the holder
            # plays one of the K/Q of trump. Each card fires at most once.
            if played_card is not None and self._is_belote_event(player, played_card):
                kind = self._transition_belote_state(player)
                if kind is not None and view is not None and hasattr(
                    view, 'on_belote_announced'
                ):
                    view.on_belote_announced(player, kind, self)

        # Determine trick winner. Who wins is a pure rule of the trick
        # given trump, so we delegate to contrai-core rather than duplicate
        # the comparison here. The contract carries the authoritative trump
        # suit (None only defensively, before a contract is established).
        winner = self.current_trick.get_current_winner(
            self.contract.suit if self.contract else None
        )
        self.last_trick_winner = winner

        # Add trick to the tricks list and to winner's team
        if self.current_trick:
            self.tricks.append(self.current_trick)
            if winner and winner.team:
                self.team_tricks[winner.team.name].append(self.current_trick)

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

        return winner

    def play_all_tricks(self, view=None) -> Dict[str, List[Trick]]:
        """
        Play all 8 tricks of the round.

        Args:
            view: Optional view for human player interaction

        Returns:
            Dict mapping team names to their tricks
        """
        # Initialize team tricks tracking
        self.last_trick_winner = None

        # Play 8 tricks
        for trick_num in range(8):
            winner = self.play_trick(view)

        return self.team_tricks

    def calculate_round_scores(self) -> Dict[str, int]:
        """
        Calculate scores for this round.

        Thin lifecycle wrapper around the pure :func:`scoring.score_round`
        transformation: it runs the scoring rules over the round's final
        state and publishes the three result attributes the view reads —
        :attr:`round_scores`, :attr:`contract_made` (the canonical
        made/failed signal), and :attr:`unannounced_capot`. The scoring
        shapes (numeric, unannounced capot, doubled winner-takes-all,
        Slam / Solo Slam) and the Belote rule all live in
        :mod:`scoring`.

        Returns:
            Dict: Team scores for this round
        """
        result = score_round(self)
        self.round_scores = result.scores
        self.contract_made = result.contract_made
        self.unannounced_capot = result.unannounced_capot
        return self.round_scores

    def handle_failed_contract(self) -> Dict[str, int]:
        """
        Manage cards when all players pass.

        Returns:
            Dict: Zero scores for all teams
        """
        # Put all players' cards back in deck (8 cards per player)
        for player in self.players_order:
            self.deck.add_cards(player.hand)
            player.hand.clear()

        # Return zero scores
        teams = set(player.team for player in self.players_order)
        self.round_scores = {team.name: 0 for team in teams}
        return self.round_scores
