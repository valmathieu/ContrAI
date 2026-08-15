"""Game class for the contrée card game.

This class manages the game state, players, teams, deck, and game logic.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from contrai_core.deck import Deck
from contrai_core.exceptions import InvalidPlayerCountError
from contrai_core.position import Position
from contrai_core.team import Team
from contrai_core.team_side import TeamSide

from ..debug_state import deal_lines, round_result_lines
from .player import Player
from .round import Round

if TYPE_CHECKING:
    from ..view.rich_view import RichView

# Logging is infrastructure, not presentation: this module never attaches a
# handler or configures a level itself (see contrai_engine.log_setup) — it
# only ever emits through the standard logging module, so the calls below
# are silent no-ops for any interface that hasn't opted into debug mode.
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameOverStatus:
    """Structured verdict returned by :meth:`Game.check_game_over`.

    Every team in this verdict is named by its :class:`TeamSide`, the
    same key :attr:`Game.scores` uses — never by a display string, so a
    caller can look a winner straight up in ``final_scores`` and the view
    is free to spell the label however it likes.

    Attributes:
        game_over: Whether a team strictly leads at or above the target score.
        winner: The winning team side — always set when ``game_over`` is
            True, ``None`` otherwise.
        tied_teams: The team sides sharing the lead at or above the target —
            the sudden-death signal: the game continues with tiebreaker
            rounds until one team leads. ``None`` when no such tie exists.
        final_scores: Snapshot of every team's score at the moment of the check.
    """

    game_over: bool
    winner: TeamSide | None
    tied_teams: list[TeamSide] | None
    final_scores: dict[TeamSide, int]

class Game:
    """
    Represents a full game of contrée.

    Attributes:
        teams (list[Team]): The two teams playing the game.
        players (list[Player]): The four players (flattened from teams).
        players_by_position (dict[Position, Player]): Each player, keyed by
            the seat it occupies.
        deck (Deck): The deck of cards for the game.
        dealer (Player): The current dealer.
        players_order (list[Player]): The order of players for the current round.
        current_contract (Contract): The current contract object.
        current_round (Round): The current round object.
        round_number (int): The current round number.
        scores (dict[TeamSide, int]): The cumulative game score of each side.
    """
    def __init__(self, players):
        """
        Initialize a game with 4 players, one per seat.
        Teams are automatically created: North-South vs East-West.

        Seating works one of two ways, and the list decides which:

        - **Pre-seated** — every player already names a seat, and the four
          must be the four distinct :class:`Position` members. This is
          what the CLI does: it builds a specific table (the human at
          South) and says so.
        - **Unseated** — no player names a seat, and the table is laid out
          from the list order against ``list(Position)``, i.e. the
          anticlockwise turn order *North, West, South, East*. Note this
          is the seating order, not the compass order N-E-S-W: the first
          and third players end up partners, as do the second and fourth.
          A caller wanting shuffled seating shuffles the list before
          passing it, which keeps the RNG (and its seeding) in the
          caller's hands — a self-play harness reseeding per game and a
          test pinning a deterministic table both need that control.

        A half-seated list is rejected rather than completed: filling the
        gaps would silently decide partnerships the caller only specified
        half of.

        Args:
            players (list[Player]): The 4 players, either all carrying a
                distinct Position or none carrying one at all.

        Raises:
            InvalidPlayerCountError: If the number of players is not exactly 4.
            ValueError: If only some players name a seat, or if the seats
                they name are not the four distinct positions.
        """
        # Validate players
        if len(players) != 4:
            raise InvalidPlayerCountError(4, len(players), "Initializing game")

        unseated = [player for player in players if player.position is None]

        if unseated and len(unseated) != len(players):
            raise ValueError(
                "Players must either all have a position or all have none; "
                f"got {len(players) - len(unseated)} seated and "
                f"{len(unseated)} unseated."
            )

        if unseated:
            # Seat them in list order around the table. Position's own
            # definition order is the anticlockwise seating, so zipping
            # against it is the whole assignment — no seat table here to
            # drift out of step with the enum.
            for player, position in zip(players, Position):
                player.position = position
        else:
            # Validate positions
            required_positions = set(Position)
            player_positions = {player.position for player in players}
            if player_positions != required_positions:
                required = ", ".join(str(p) for p in Position)
                raise ValueError(f"Players must have positions: {required}")

        # Sort players by position (the canonical anticlockwise seating).
        canonical_seating = list(Position)
        self.players = sorted(players, key=lambda p: canonical_seating.index(p.position))

        # Index players by seat once, so team formation (and any future
        # seat-based lookup) never re-scans the player list.
        self.players_by_position: dict[Position, Player] = {
            player.position: player for player in self.players
        }

        # Create teams automatically: North-South vs East-West
        north_player = self.players_by_position[Position.NORTH]
        south_player = self.players_by_position[Position.SOUTH]
        east_player = self.players_by_position[Position.EAST]
        west_player = self.players_by_position[Position.WEST]

        team_ns = Team("North-South", [north_player, south_player])
        team_ew = Team("East-West", [east_player, west_player])
        self.teams = [team_ns, team_ew]

        # Assign teams to players
        north_player.team = team_ns
        south_player.team = team_ns
        east_player.team = team_ew
        west_player.team = team_ew

        self.deck = Deck()  # Deck instance
        self.dealer = None
        self.players_order = []
        self.current_contract = None
        self.current_round = None
        self.round_number = 0
        self.scores: dict[TeamSide, int] = {side: 0 for side in TeamSide}

    def start_new_round(self):
        """
        Starts a new round: shuffles or cuts, deals, resets contract and sets the next dealer.
        """
        # Reset contract and set next dealer
        self.current_contract = None
        self.next_dealer()

        # Shuffle if it's the first round and cut deck otherwise
        if self.round_number == 0:
            self.deck.shuffle()
        else:
            self.deck.cut()

        # Set players order for the round
        self.set_players_order()

        # Increment the round number
        self.round_number += 1

        # Create new Round object
        self.current_round = Round(self.players_order, self.dealer, self.deck, self.round_number)

        # Deal cards
        self.current_round.deal_cards()

        # Snapshot the fresh deal for any interface's debug log, view
        # attached or not. Building the snapshot (a per-seat hand sort)
        # is only worth doing when a handler is actually listening.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s", "\n".join(deal_lines(self.current_round)))

    def manage_round(self, view: RichView | None = None) -> None:
        """
        Manages a complete round: bidding, trick-taking, and scoring using Round class.

        Mutates game state in place: sets ``current_contract`` (``None`` when every
        player passed) and folds the round's points into ``scores``. Returns nothing —
        callers read the outcome off the ``Game``/``Round`` they passed in.

        Args:
            view: Optional view for human player interaction.
        """
        # Start new round (deal cards, set dealer, etc.)
        self.start_new_round()

        # Notify the view that a fresh round has been dealt. Used by
        # interactive views to log the deal in the rolling event log.
        if view is not None and hasattr(view, 'on_round_dealt'):
            view.on_round_dealt(self.current_round)

        # Bidding phase - delegate to Round
        contract = self.current_round.manage_bidding(view)
        self.current_contract = contract

        # If no contract (all passed), handle failed contract (redistributes cards).
        if not contract:
            self.current_round.handle_failed_contract()
            self._log_round_result()
            # Notify the view that the round will be redealt. The hook is a
            # pure announcement — it carries no round payload.
            if view is not None and hasattr(view, 'on_all_pass_redeal'):
                view.on_all_pass_redeal()
            return

        # Play all tricks - delegate to Round
        self.current_round.play_all_tricks(view)

        # Calculate scores for the round - delegate to Round
        round_scores = self.current_round.calculate_round_scores()

        # Update total scores
        for side, points in round_scores.items():
            self.scores[side] += points

        self._log_round_result()

    def _log_round_result(self) -> None:
        """Log the just-finished round's outcome at DEBUG.

        Covers both a scored round and an all-passed redeal in one call
        site: :func:`contrai_engine.debug_state.round_result_lines` itself
        branches on ``current_round.contract`` to pick the right wording.
        Building the lines is only worth doing when a handler is actually
        listening.
        """
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s",
                "\n".join(
                    round_result_lines(self.current_round, self.scores)
                ),
            )

    def check_game_over(self, target_score: int = 1500) -> GameOverStatus:
        """
        Checks if a team strictly leads at the target score, ending the game.

        A tie at or above the target does not end the game: the teams are in
        sudden death and keep playing tiebreaker rounds until one of them
        leads. The tie is surfaced through ``tied_teams`` so callers (e.g.
        the view) can announce the tiebreaker.

        Args:
            target_score: Score required to win the game.

        Returns:
            GameOverStatus: Whether the game is over, the winner (always set
                when over), any teams tied at/above the target, and a
                snapshot of the final scores.
        """
        max_score = max(self.scores.values())

        if max_score >= target_score:
            # Find the side(s) sharing the top score.
            leading_teams = [side for side, score in self.scores.items()
                             if score == max_score]

            if len(leading_teams) == 1:
                return GameOverStatus(
                    game_over=True,
                    winner=leading_teams[0],
                    tied_teams=None,
                    final_scores=self.scores.copy(),
                )

            # Sudden death: level at/above the target — play another round.
            return GameOverStatus(
                game_over=False,
                winner=None,
                tied_teams=leading_teams,
                final_scores=self.scores.copy(),
            )

        return GameOverStatus(
            game_over=False,
            winner=None,
            tied_teams=None,
            final_scores=self.scores.copy(),
        )

    def next_dealer(self):
        """
        Sets the next dealer for the next round (player to the left of current dealer, anticlockwise).
        """
        if self.dealer is None:
            self.dealer = random.choice(self.players)
        else:
            idx = self.players.index(self.dealer)
            self.dealer = self.players[(idx + 1) % 4]

    def set_players_order(self):
        """
        Sets the players order starting with the player after the dealer (anticlockwise order).
        """
        # Reset players order and start with next player after dealer (anticlockwise order)
        dealer_idx = self.players.index(self.dealer)
        self.players_order = []
        for i in range(4):
            player_idx = (dealer_idx + 1 + i) % 4
            self.players_order.append(self.players[player_idx])