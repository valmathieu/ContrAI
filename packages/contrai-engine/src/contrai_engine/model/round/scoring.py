"""Round scoring — the pure transformation from a played-out round to
its team scores.

``score_round`` reads the authoritative :class:`contrai_core.PlayState`
on the round (contract, completed tricks and their winners) plus the
round's belote holder, and returns a :class:`RoundScore` result; it
mutates nothing. The thin ``Round.calculate_round_scores`` wrapper
unpacks that result onto the round's public result attributes. Keeping
the maths side-effect-free here isolates ~250 lines of scoring rules
from the lifecycle orchestrator.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Sequence, TYPE_CHECKING

from contrai_core.bid import SlamLevel
from contrai_core.rules import rules_for
from contrai_core.team_side import TeamSide

if TYPE_CHECKING:
    from contrai_core.player import BasePlayer
    from ..player import Player
    from .round import Round


class UnannouncedSlam(Enum):
    """Outcome tag for an *undeclared* all-tricks sweep on a numeric contract.

    Set by :func:`score_round` (via :meth:`Round.calculate_round_scores`)
    after play, when the declaring team takes all 8 tricks on an
    un-doubled numeric (80-180) contract without having announced
    anything. The round still scores on the numeric path — the bidder's
    contract value plus a flat 250 substitute for the trick pile — *not*
    the Slam at-risk grid.

    This is deliberately distinct from :class:`contrai_core.SlamLevel`: that
    enum is a *declared bid value*; this is a post-play classification, and
    its ``GRAND_SLAM`` member is named for the undeclared sweep (it is not a
    Solo Slam). Each member's value is its display label, so ``str(tag)``
    yields the text the recap panel shows.

    Members:
        SLAM: The declaring *team* swept all 8 tricks (partner won at
            least one).
        GRAND_SLAM: The contracting *player personally* won all 8 tricks.
    """

    SLAM = "Slam"
    GRAND_SLAM = "Grand Slam"

    def __str__(self) -> str:
        return self.value


@dataclass
class RoundScore:
    """Result of scoring a played-out round.

    A side-effect-free bundle of the three values
    :meth:`Round.calculate_round_scores` publishes onto the round:

    Attributes:
        scores: Per-team round scores, keyed by :class:`TeamSide`.
        contract_made: The canonical made/failed signal — ``None`` when
            the round was all-passed (no contract), else a bool.
        unannounced_slam: The :class:`UnannouncedSlam` tag when the
            declaring team swept all 8 tricks on an un-doubled numeric
            contract, else ``None``.
    """

    scores: Dict[TeamSide, int]
    contract_made: Optional[bool]
    unannounced_slam: Optional[UnannouncedSlam]


def count_player_tricks(
    trick_winners: Sequence['BasePlayer'], player: 'Player'
) -> int:
    """Count the number of completed tricks personally won by ``player``.

    A straight tally over the per-trick winners the play state already
    derives (:attr:`contrai_core.PlayState.trick_winners`) — the winner
    rule itself lives in the core, so nothing is re-adjudicated here.
    Used by the Solo Slam predicate in :func:`score_round`.

    Args:
        trick_winners: The winning player of each completed trick, in
            trick order.
        player: The player whose personal trick tally we want.

    Returns:
        The number of completed tricks won outright by ``player``.
    """
    return sum(1 for winner in trick_winners if winner is player)


def score_round(round_: 'Round') -> RoundScore:
    """Score a played-out round into a :class:`RoundScore`.

    Three scoring shapes, all sharing the same Belote rule:

    - **Numeric, un-doubled (M = 1).** Made → declarer scores
      ``C + P_attack`` and the defense keeps its own card points;
      failed → the defense scores ``160 + C`` and the declarer
      scores nothing. ``P_attack`` is the declarer's card points
      (which already include the last-trick bonus) plus the Belote
      bonus when the declarer holds it.
    - **Unannounced Slam (M = 1).** When the declaring team wins
      *all 8 tricks* on a numeric contract without having bid a
      Slam, the trick pile (152 cards + the 10-point last-trick
      bonus = 162) is
      replaced by a flat **250** substitute: the declarer scores
      ``C + 250`` (+ Belote), the defense scores nothing, and the
      contract is necessarily made. The personal-trick predicate
      tags it :attr:`UnannouncedSlam.GRAND_SLAM` when the
      *contracting player* won all 8, else
      :attr:`UnannouncedSlam.SLAM`. Only un-doubled — a
      doubled/redoubled sweep keeps the winner-takes-all shape below.
    - **Numeric, doubled / redoubled (M > 1).** Winner-takes-all:
      the side that wins the round takes the whole pile, the loser
      scores 0. The winner scores ``160 + C × M`` whether it is the
      declarer (made) or the defense (failed). See
      contree-domain.md §7.2.
    - **Slam / Solo Slam.** A symmetric grid that replaces the
      162-point pile with a flat substitute equal to the base: the
      winning side scores ``(base + substitute) × M`` (500 / 1000 /
      2000 for Slam; 1000 / 2000 / 4000 for Solo Slam). Solo Slam
      additionally requires the *contracting player personally* to
      win every trick.

    Across every shape the **Belote bonus (+20)** is credited to the
    team *holding* both K and Q of trump (``round_.belote_holder`` — not
    whoever captures the cards in a trick) and is always preserved,
    even for the side that loses the round.

    Args:
        round_: The played-out round, read by reference (contract,
            play_state, belote_holder, players_order). Nothing on it is
            mutated.

    Returns:
        A :class:`RoundScore` carrying the per-team scores, the
        made/failed signal, and any unannounced-Slam tag.
    """
    if not round_.contract:
        # No contract established, return zero scores.
        sides = {player.position.team_side for player in round_.players_order}
        return RoundScore(
            scores={side: 0 for side in sides},
            contract_made=None,
            unannounced_slam=None,
        )

    contract_value = round_.contract.value
    trump_suit = round_.contract.suit
    rules = rules_for(trump_suit)

    # The authoritative play history: completed tricks and their winners
    # come straight from the core play state, never from the view-facing
    # mirrors.
    play_state = round_.play_state
    completed_tricks = play_state.completed_tricks
    trick_winners = play_state.trick_winners

    # Every seat has a Position and every Position has a side, so the
    # two buckets are derived from the seating rather than from the
    # mutable Team roster objects.
    sides = {player.position.team_side for player in round_.players_order}
    team_card_points = {side: 0 for side in sides}
    team_scores = {side: 0 for side in sides}
    team_trick_counts = {side: 0 for side in sides}

    # Card points and trick counts per side (trump-aware): each completed
    # trick's whole pile is credited to its winner's side. Belote is
    # deliberately NOT folded in here — it is a *held-cards* bonus
    # credited below to the holder's side, independent of who captured
    # the K/Q.
    for trick, winner in zip(completed_tricks, trick_winners):
        winner_side = winner.position.team_side
        team_card_points[winner_side] += sum(
            rules.points(play.card) for play in trick
        )
        team_trick_counts[winner_side] += 1

    # Add the last-trick bonus (10 points for winning the last trick).
    if trick_winners:
        team_card_points[trick_winners[-1].position.team_side] += 10

    # Belote (+20) belongs to the side *holding* K + Q of trump, not
    # to whoever wins the trick those cards land in. ``belote_holder``
    # is the single player holding both at deal time (None when split,
    # or at No-Trump).
    belote_side: Optional[TeamSide] = None
    if round_.belote_holder is not None:
        belote_side = round_.belote_holder.position.team_side

    def belote_bonus(side: TeamSide) -> int:
        """Belote (+20) for ``side`` when it holds the pair."""
        return 20 if side is belote_side else 0

    contract_side = round_.contract.player.position.team_side

    # Multiplier for double/redouble (shared by both paths).
    multiplier = round_.contract.get_multiplier()

    # ----- Slam / Solo Slam scoring path -----
    # The 162 of trick-card points is replaced by a flat substitute
    # equal to the contract base (see Contract.get_slam_card_substitute).
    # The full at-risk amount is (base + substitute) × multiplier,
    # giving 500 / 1000 / 2000 for Slam and 1000 / 2000 / 4000 for
    # Solo Slam at normal / doubled / redoubled. The grid is symmetric:
    # whichever side wins the contract scores the at-risk amount.
    if round_.contract.is_slam_family():
        contract_made = team_trick_counts[contract_side] == 8

        # Solo Slam: the bidder *personally* must win all 8 tricks.
        # Even if their team takes every trick collectively, the
        # contract fails when the partner won any of them.
        if round_.contract.is_solo_slam():
            bidder_personal_tricks = count_player_tricks(
                trick_winners, round_.contract.player
            )
            contract_made = contract_made and bidder_personal_tricks == 8

        base = round_.contract.get_base_points()
        substitute = round_.contract.get_slam_card_substitute()
        at_risk = (base + substitute) * multiplier
        if contract_made:
            team_scores[contract_side] = at_risk
        else:
            for side in team_scores:
                if side is not contract_side:
                    team_scores[side] = at_risk

        # Belote (+20) layered on top — independent of who won the contract.
        if belote_side is not None:
            team_scores[belote_side] += 20

        return RoundScore(
            scores=team_scores,
            contract_made=contract_made,
            unannounced_slam=None,
        )

    # ----- Numeric contract scoring path (80-180) -----
    defender_sides = [side for side in team_scores if side is not contract_side]

    # Unannounced Slam: the declaring team swept all 8 tricks on a
    # numeric contract. Recognised only un-doubled — the
    # doubled/redoubled path keeps its winner-takes-all 160 + C×M
    # shape regardless. The trick pile (152 cards + the 10-point
    # last-trick bonus) is replaced by a flat 250 substitute and the
    # contract is necessarily made. GRAND_SLAM when the contracting
    # player won all 8 personally (the Solo Slam predicate), else plain
    # SLAM. The 250 substitute is the same flat amount a *declared* Slam
    # is worth, so it reads from the SlamLevel single source of truth.
    UNANNOUNCED_SLAM_SUBSTITUTE = SlamLevel.SLAM.base_value
    unannounced_slam: Optional[UnannouncedSlam] = None
    declarer_slam = (
        multiplier == 1
        and team_trick_counts[contract_side] == 8
    )
    if declarer_slam:
        bidder_personal_tricks = count_player_tricks(
            trick_winners, round_.contract.player
        )
        unannounced_slam = (
            UnannouncedSlam.GRAND_SLAM
            if bidder_personal_tricks == 8
            else UnannouncedSlam.SLAM
        )

    # The declarer's *realized* points decide made/failed: card
    # points (already including the last-trick bonus) plus the Belote
    # bonus when the declarer holds it. An unannounced Slam is made
    # outright — sweeping every trick can never fail.
    attacker_realized = (
        team_card_points[contract_side] + belote_bonus(contract_side)
    )
    contract_made = declarer_slam or attacker_realized >= contract_value

    if multiplier == 1:
        # Un-doubled: the two sides share the pile.
        if contract_made:
            # On an unannounced Slam the 162 pile (last-trick bonus
            # included) is swapped for the flat 250 substitute; otherwise the
            # declarer adds its real captured card points.
            attacker_pile = (
                UNANNOUNCED_SLAM_SUBSTITUTE
                if declarer_slam
                else team_card_points[contract_side]
            )
            team_scores[contract_side] = (
                contract_value
                + attacker_pile
                + belote_bonus(contract_side)
            )
            for side in defender_sides:
                team_scores[side] = team_card_points[side] + belote_bonus(side)
        else:
            # Failed: the defense takes the whole pile plus
            # the contract; the declarer keeps only its Belote bonus.
            team_scores[contract_side] = belote_bonus(contract_side)
            for side in defender_sides:
                team_scores[side] = (160 + contract_value) + belote_bonus(side)
    else:
        # Doubled / redoubled: winner-takes-all. The losing side
        # scores nothing but its Belote bonus (always preserved).
        if contract_made:
            team_scores[contract_side] = (
                160 + contract_value * multiplier
                + belote_bonus(contract_side)
            )
            for side in defender_sides:
                team_scores[side] = belote_bonus(side)
        else:
            team_scores[contract_side] = belote_bonus(contract_side)
            for side in defender_sides:
                team_scores[side] = (
                    160 + contract_value * multiplier + belote_bonus(side)
                )

    return RoundScore(
        scores=team_scores,
        contract_made=contract_made,
        unannounced_slam=unannounced_slam,
    )
