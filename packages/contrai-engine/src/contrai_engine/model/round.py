# Round class for the "contree" card game.
# This class represents a complete round of the card game from dealing to scoring.

import itertools
from typing import Optional, Dict, List, TYPE_CHECKING

from contrai_core.auction import Auction
from contrai_core.bid import Bid
from contrai_core.contract import Contract
from contrai_core.trick import Trick
from contrai_core.types import Rank, Suit

if TYPE_CHECKING:
    from .player import Player
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
            has_king = any(
                card.suit == trump and card.rank == Rank.KING
                for card in player.hand
            )
            has_queen = any(
                card.suit == trump and card.rank == Rank.QUEEN
                for card in player.hand
            )
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
            playable_cards = self._get_playable_cards(player)

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

            # Validate that the chosen card is legal
            played_card = None
            if card and card in playable_cards and card in player.hand:
                player.hand.remove(card)
                self.current_trick.add_play(player, card)
                played_card = card
            elif card and playable_cards:
                # Card chosen is not legal - fallback to first playable card
                fallback_card = playable_cards[0]
                player.hand.remove(fallback_card)
                self.current_trick.add_play(player, fallback_card)
                played_card = fallback_card

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

        # Determine trick winner
        winner = self._determine_trick_winner(self.current_trick)
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

        Two scoring paths coexist:

        - **Numeric contracts (80–160)** use the historical formula
          ``base + card_points`` (made) and ``(160 + base) * multiplier``
          (failed). Card points include the *dix de der* (10 pts for
          the last trick) and the belote bonus (+20 for the team
          capturing both K and Q of trump).
        - **Slam / Solo Slam** use a symmetric grid that *replaces*
          ``base + card_points`` entirely: the winning side (attacker
          if made, defender if failed) scores ``base * multiplier``
          (500 / 1000 / 2000 for Slam; 1000 / 2000 / 4000 for Solo
          Slam). The belote bonus (+20) still applies and is layered
          on top of the grid. Solo Slam additionally requires that
          the *contracting player personally* win every trick — if
          their partner takes any trick the contract fails even when
          the team wins all 8 collectively.

        Returns:
            Dict: Team scores for this round
        """
        if not self.contract:
            # No contract established, return zero scores
            teams = set(player.team for player in self.players_order)
            self.round_scores = {team.name: 0 for team in teams}
            return self.round_scores

        contract_team = self.contract.player.team
        contract_value = self.contract.value
        trump_suit = self.contract.suit
        is_doubled = self.contract.double
        is_redoubled = self.contract.redouble

        team_card_points = {team_name: 0 for team_name in self.team_tricks.keys()}
        team_scores = {team_name: 0 for team_name in self.team_tricks.keys()}

        # Count card points for each team and check for belote (King + Queen of trump)
        belote_teams = set()  # Teams that have belote
        for team_name, tricks in self.team_tricks.items():
            points = 0
            trump_cards_played = []

            for trick in tricks:
                # Handle Trick objects
                if hasattr(trick, 'get_plays'):
                    plays = trick.get_plays()
                    for player, card in plays:
                        points += card.get_points(trump_suit)
                        # Track trump cards played by this team
                        if trump_suit and card.suit == trump_suit:
                            trump_cards_played.append(card.rank)

            # Check for belote (King and Queen of trump suit in same round)
            if trump_suit and Rank.KING in trump_cards_played and Rank.QUEEN in trump_cards_played:
                points += 20  # Belote bonus
                belote_teams.add(team_name)

            team_card_points[team_name] = points

        # Add "dix de der" (10 points for last trick)
        if self.last_trick_winner and self.last_trick_winner.team:
            last_trick_team = self.last_trick_winner.team.name
            team_card_points[last_trick_team] += 10

        contract_team_name = contract_team.name

        # Calculate multiplier for double/redouble (shared by both paths).
        multiplier = 1
        if is_redoubled:
            multiplier = 4
        elif is_doubled:
            multiplier = 2

        # ----- Slam / Solo Slam scoring path -----
        # The 162 of trick-card points is replaced by a flat substitute
        # equal to the contract base (see Contract.get_slam_card_substitute).
        # The full at-risk amount is (base + substitute) × multiplier,
        # giving 500 / 1000 / 2000 for Slam and 1000 / 2000 / 4000 for
        # Solo Slam at normal / doubled / redoubled. The grid is symmetric:
        # whichever side wins the contract scores the at-risk amount.
        # See contree-domain.md §7.2.
        if self.contract.is_slam_family():
            contract_team_trick_count = len(self.team_tricks[contract_team_name])
            contract_made = contract_team_trick_count == 8

            # Solo Slam: the bidder *personally* must win all 8 tricks.
            # Even if their team takes every trick collectively, the
            # contract fails when the partner won any of them.
            if self.contract.is_solo_slam():
                bidder_personal_tricks = self._count_player_tricks(
                    self.contract.player
                )
                contract_made = contract_made and bidder_personal_tricks == 8

            base = self.contract.get_base_points()
            substitute = self.contract.get_slam_card_substitute()
            at_risk = (base + substitute) * multiplier
            if contract_made:
                team_scores[contract_team_name] = at_risk
            else:
                for team_name in team_scores:
                    if team_name != contract_team_name:
                        team_scores[team_name] = at_risk

            # Belote (+20) layered on top — independent of who won the contract.
            for team_name in belote_teams:
                team_scores[team_name] += 20

            self.round_scores = team_scores
            return team_scores

        # ----- Numeric contract scoring path (80-160) -----
        contract_team_points = team_card_points[contract_team_name]
        contract_made = contract_team_points >= contract_value

        # Calculate final scores
        if contract_made:
            # Contract successful
            if is_doubled or is_redoubled:
                # When contract is made with double/redouble, attacking team gets
                # the same points that defending team would have gotten if contract failed
                team_scores[contract_team_name] = 160 + contract_value * multiplier

                # Defending team gets their actual points (no multiplier)
                for team_name, points in team_card_points.items():
                    if team_name != contract_team_name:
                        team_scores[team_name] = points
            else:
                # Normal contract made without double/redouble
                team_scores[contract_team_name] = contract_value + contract_team_points
                # Opposing team gets their points
                for team_name, points in team_card_points.items():
                    if team_name != contract_team_name:
                        team_scores[team_name] = points
        else:
            # Contract failed
            team_scores[contract_team_name] = 0  # Contract team gets 0
            # Opposing team gets all points + contract value
            for team_name in team_scores:
                if team_name != contract_team_name:
                    team_scores[team_name] = (160 + contract_value) * multiplier

        self.round_scores = team_scores
        return team_scores

    def _count_player_tricks(self, player: 'Player') -> int:
        """Count the number of completed tricks personally won by ``player``.

        Walks the round's trick history and asks each trick for its
        winner via :meth:`contrai_core.Trick.get_current_winner`,
        forcing the contract's trump suit so trump beats lead-suit
        regardless of whether the trick had its ``trump_suit`` bound
        at construction time. Used by the Solo Slam predicate in
        :meth:`calculate_round_scores`.

        Args:
            player: The player whose personal trick tally we want.

        Returns:
            The number of completed tricks won outright by ``player``.
        """
        if not self.tricks or self.contract is None:
            return 0
        trump_suit = self.contract.suit
        count = 0
        for trick in self.tricks:
            winner = trick.get_current_winner(trump_suit)
            if winner is player:
                count += 1
        return count

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

    def _get_playable_cards(self, player: 'Player'):
        """
        Determine which cards a player can legally play.

        Implements the rules from contree-domain.md §6.2-§6.3:
            1. Follow the led suit if you can.
            2. When trump is led, you must additionally over-trump if you
               hold a higher trump than the highest already on the table
               (§6.3).
            3. When you cannot follow suit and your partner is *not*
               currently master of the trick, you must trump. If an
               opponent has already trumped, you must over-trump if able;
               otherwise play any trump.
            4. Partner-master exception: if your partner is currently
               winning the trick, you may discard freely — no obligation
               to trump or over-trump (§6.2 rule 4).
            5. Otherwise (no trump in your hand, or no trump suit) any
               card may be discarded.

        Args:
            player: The player whose playable cards we want to determine.

        Returns:
            list: List of cards that can be legally played.
        """
        if not player.hand:
            return []

        trump_suit = self.contract.suit if self.contract else None
        if not self.current_trick or not hasattr(self.current_trick, 'get_plays'):
            return player.hand.copy()

        plays = self.current_trick.get_plays()
        if not plays:
            # First to play in this trick — anything goes.
            return player.hand.copy()

        lead_suit = plays[0][1].suit
        lead_suit_cards = [card for card in player.hand if card.suit == lead_suit]
        trump_cards = (
            [card for card in player.hand if card.suit == trump_suit]
            if trump_suit
            else []
        )

        # Rule 1 — follow suit. Special-case rule 2 (over-trump when trump
        # is led): the player MUST go higher than the best trump on the
        # table if they hold one; only fall back to lower trumps when no
        # higher trump exists.
        if lead_suit_cards:
            if trump_suit and lead_suit == trump_suit:
                higher = self._higher_trumps_than_played(lead_suit_cards, plays, trump_suit)
                return higher if higher else lead_suit_cards
            return lead_suit_cards

        # Rule 4 — partner-master exemption per contree-domain.md §6.2
        # rule 4. The exemption applies only when the partner is
        # *currently winning* the partial trick, not just whoever led:
        # a partner who has since been over-trumped by an opponent no
        # longer protects you from the trump obligation.
        current_master = self.current_trick.get_current_winner(trump_suit)
        if current_master is not None and current_master.team == player.team:
            return player.hand.copy()

        # No trump suit, or led suit is trump (and we have none — already
        # handled above when we have some): nothing to over-trump, free discard.
        if not trump_suit or lead_suit == trump_suit:
            return player.hand.copy()

        # Trump obligations apply. If any opponent trumped, must beat them.
        highest_opponent_trump = self._highest_opponent_trump(plays, player.team, trump_suit)
        if highest_opponent_trump is not None:
            higher_trumps = [
                card for card in trump_cards
                if card.get_order(trump_suit) > highest_opponent_trump.get_order(trump_suit)
            ]
            if higher_trumps:
                return higher_trumps
            if trump_cards:
                return trump_cards
            return player.hand.copy()

        # No opponent trump yet but partner is not master either → must
        # trump if able.
        if trump_cards:
            return trump_cards
        return player.hand.copy()

    @staticmethod
    def _higher_trumps_than_played(trumps_in_hand, plays, trump_suit):
        """Return the subset of *trumps_in_hand* that beat every trump in *plays*.

        Used by the over-trump rule when the led suit is itself trump.
        Returns an empty list if no trump has been played to the trick
        yet (logically impossible here, but kept defensive) or if no
        trump in hand beats the current best.
        """
        best_so_far = None
        for _, card in plays:
            if card.suit != trump_suit:
                continue
            if best_so_far is None or card.get_order(trump_suit) > best_so_far.get_order(trump_suit):
                best_so_far = card
        if best_so_far is None:
            return []
        return [
            c for c in trumps_in_hand
            if c.get_order(trump_suit) > best_so_far.get_order(trump_suit)
        ]

    @staticmethod
    def _highest_opponent_trump(plays, player_team, trump_suit):
        """Return the highest trump played by an opponent of *player_team*, or None."""
        highest = None
        for trick_player, card in plays:
            if card.suit != trump_suit or trick_player.team == player_team:
                continue
            if highest is None or card.get_order(trump_suit) > highest.get_order(trump_suit):
                highest = card
        return highest

    def _determine_trick_winner(self, trick: Trick) -> Optional['Player']:
        """
        Determines the winner of a trick based on the cards played.

        Args:
            trick: a Trick object containing the plays

        Returns:
            Player: The winner of the trick or None
        """
        trump_suit = self.contract.suit if self.contract else None
        if not trick or not hasattr(trick, 'get_plays'):
            return None

        plays = trick.get_plays()
        if not plays:
            return None

        lead_suit = plays[0][1].suit  # Suit of the first card played

        best_player = plays[0][0]
        best_card = plays[0][1]
        best_is_trump = trump_suit and best_card.suit == trump_suit

        for player, card in plays[1:]:
            card_is_trump = trump_suit and card.suit == trump_suit

            if card_is_trump and not best_is_trump:
                # Trump beats non-trump
                best_player = player
                best_card = card
                best_is_trump = True
            elif card_is_trump and best_is_trump:
                # Compare trump cards
                if card.get_order(trump_suit) > best_card.get_order(trump_suit):
                    best_player = player
                    best_card = card
            elif not card_is_trump and not best_is_trump and card.suit == lead_suit:
                # Compare cards of the same suit (non-trump)
                if card.get_order() > best_card.get_order():
                    best_player = player
                    best_card = card

        return best_player
