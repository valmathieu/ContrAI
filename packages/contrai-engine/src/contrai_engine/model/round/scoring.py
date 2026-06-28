# Round scoring — the pure transformation from a played-out round to
# its team scores.
#
# ``score_round`` reads the final round state (contract, captured tricks,
# last-trick winner, belote holder) and returns a :class:`RoundScore`
# result; it mutates nothing. The thin ``Round.calculate_round_scores``
# wrapper unpacks that result onto the round's public result attributes.
# Keeping the maths side-effect-free here isolates ~250 lines of scoring
# rules from the lifecycle orchestrator (see contree-domain.md §6.5, §7).

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, TYPE_CHECKING

from contrai_core.bid import SlamLevel

if TYPE_CHECKING:
    from contrai_core.trick import Trick
    from contrai_core.types import Suit
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
        scores: Per-team round scores, keyed by team name.
        contract_made: The canonical made/failed signal — ``None`` when
            the round was all-passed (no contract), else a bool.
        unannounced_capot: The :class:`UnannouncedSlam` tag when the
            declaring team swept all 8 tricks on an un-doubled numeric
            contract, else ``None``.
    """

    scores: Dict[str, int]
    contract_made: Optional[bool]
    unannounced_capot: Optional[UnannouncedSlam]


def count_player_tricks(
    tricks: List['Trick'], trump_suit: Optional['Suit'], player: 'Player'
) -> int:
    """Count the number of completed tricks personally won by ``player``.

    Walks the round's trick history and asks each trick for its
    winner via :meth:`contrai_core.Trick.get_current_winner`,
    forcing the contract's trump suit so trump beats lead-suit
    regardless of whether the trick had its ``trump_suit`` bound
    at construction time. Used by the Solo Slam predicate in
    :func:`score_round`.

    Args:
        tricks: The round's completed tricks.
        trump_suit: The contract's trump suit, forced into the winner
            comparison.
        player: The player whose personal trick tally we want.

    Returns:
        The number of completed tricks won outright by ``player``.
    """
    if not tricks or trump_suit is None:
        return 0
    count = 0
    for trick in tricks:
        winner = trick.get_current_winner(trump_suit)
        if winner is player:
            count += 1
    return count


def score_round(round_: 'Round') -> RoundScore:
    """Score a played-out round into a :class:`RoundScore`.

    Three scoring shapes, all sharing the same Belote rule (see
    contree-domain.md §6.5, §7):

    - **Numeric, un-doubled (M = 1).** Made → declarer scores
      ``C + P_attack`` and the defense keeps its own card points;
      failed → the defense scores ``160 + C`` and the declarer
      scores nothing. ``P_attack`` is the declarer's card points
      (which already include the *dix de der*) plus the Belote
      bonus when the declarer holds it.
    - **Unannounced capot (M = 1).** When the declaring team wins
      *all 8 tricks* on a numeric contract without having bid a
      Slam, the trick pile (152 cards + 10 *dix de der* = 162) is
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
            team_tricks, tricks, last_trick_winner, belote_holder,
            players_order). Nothing on it is mutated.

    Returns:
        A :class:`RoundScore` carrying the per-team scores, the
        made/failed signal, and any unannounced-capot tag.
    """
    if not round_.contract:
        # No contract established, return zero scores.
        teams = set(player.team for player in round_.players_order)
        return RoundScore(
            scores={team.name: 0 for team in teams},
            contract_made=None,
            unannounced_capot=None,
        )

    contract_team = round_.contract.player.team
    contract_value = round_.contract.value
    trump_suit = round_.contract.suit

    team_card_points = {team_name: 0 for team_name in round_.team_tricks.keys()}
    team_scores = {team_name: 0 for team_name in round_.team_tricks.keys()}

    # Card points per team (trump-aware). Belote is deliberately NOT
    # folded in here — it is a *held-cards* bonus credited below to
    # the holder's team, independent of who captured the K/Q.
    for team_name, tricks in round_.team_tricks.items():
        points = 0
        for trick in tricks:
            if hasattr(trick, 'get_plays'):
                for _player, card in trick.get_plays():
                    points += card.get_points(trump_suit)
        team_card_points[team_name] = points

    # Add "dix de der" (10 points for last trick).
    if round_.last_trick_winner and round_.last_trick_winner.team:
        team_card_points[round_.last_trick_winner.team.name] += 10

    # Belote (+20) belongs to the team *holding* K + Q of trump
    # (contree-domain.md §6.5), not to whoever wins the trick those
    # cards land in. ``belote_holder`` is the single player holding
    # both at deal time (None when split, or at No-Trump).
    belote_team: Optional[str] = None
    if round_.belote_holder is not None and round_.belote_holder.team is not None:
        belote_team = round_.belote_holder.team.name

    def belote_bonus(team_name: str) -> int:
        """Belote (+20) for ``team_name`` when it holds the pair."""
        return 20 if team_name == belote_team else 0

    contract_team_name = contract_team.name

    # Multiplier for double/redouble (shared by both paths).
    multiplier = round_.contract.get_multiplier()

    # ----- Slam / Solo Slam scoring path -----
    # The 162 of trick-card points is replaced by a flat substitute
    # equal to the contract base (see Contract.get_slam_card_substitute).
    # The full at-risk amount is (base + substitute) × multiplier,
    # giving 500 / 1000 / 2000 for Slam and 1000 / 2000 / 4000 for
    # Solo Slam at normal / doubled / redoubled. The grid is symmetric:
    # whichever side wins the contract scores the at-risk amount.
    # See contree-domain.md §7.2.
    if round_.contract.is_slam_family():
        contract_team_trick_count = len(round_.team_tricks[contract_team_name])
        contract_made = contract_team_trick_count == 8

        # Solo Slam: the bidder *personally* must win all 8 tricks.
        # Even if their team takes every trick collectively, the
        # contract fails when the partner won any of them.
        if round_.contract.is_solo_slam():
            bidder_personal_tricks = count_player_tricks(
                round_.tricks, trump_suit, round_.contract.player
            )
            contract_made = contract_made and bidder_personal_tricks == 8

        base = round_.contract.get_base_points()
        substitute = round_.contract.get_slam_card_substitute()
        at_risk = (base + substitute) * multiplier
        if contract_made:
            team_scores[contract_team_name] = at_risk
        else:
            for team_name in team_scores:
                if team_name != contract_team_name:
                    team_scores[team_name] = at_risk

        # Belote (+20) layered on top — independent of who won the contract.
        if belote_team is not None:
            team_scores[belote_team] += 20

        return RoundScore(
            scores=team_scores,
            contract_made=contract_made,
            unannounced_capot=None,
        )

    # ----- Numeric contract scoring path (80-180) -----
    defender_names = [t for t in team_scores if t != contract_team_name]

    # Unannounced capot: the declaring team swept all 8 tricks on a
    # numeric contract. Recognised only un-doubled — the
    # doubled/redoubled path keeps its winner-takes-all 160 + C×M
    # shape regardless. The trick pile (152 cards + 10 der) is
    # replaced by a flat 250 substitute and the contract is
    # necessarily made. GRAND_SLAM when the contracting player won all
    # 8 personally (the Solo Slam predicate), else plain SLAM.
    # The 250 substitute is the same flat amount a *declared* Slam is
    # worth, so it reads from the SlamLevel single source of truth.
    UNANNOUNCED_CAPOT_SUBSTITUTE = SlamLevel.SLAM.base_value
    unannounced_capot: Optional[UnannouncedSlam] = None
    declarer_capot = (
        multiplier == 1
        and len(round_.team_tricks[contract_team_name]) == 8
    )
    if declarer_capot:
        bidder_personal_tricks = count_player_tricks(
            round_.tricks, trump_suit, round_.contract.player
        )
        unannounced_capot = (
            UnannouncedSlam.GRAND_SLAM
            if bidder_personal_tricks == 8
            else UnannouncedSlam.SLAM
        )

    # The declarer's *realized* points decide made/failed: card
    # points (already including the dix de der) plus the Belote
    # bonus when the declarer holds it (contree-domain.md §7.1-§7.2).
    # A capot is made outright — sweeping every trick can never fail.
    attacker_realized = (
        team_card_points[contract_team_name] + belote_bonus(contract_team_name)
    )
    contract_made = declarer_capot or attacker_realized >= contract_value

    if multiplier == 1:
        # Un-doubled: the two sides share the pile.
        if contract_made:
            # On an unannounced capot the 162 pile (der included) is
            # swapped for the flat 250 substitute; otherwise the
            # declarer adds its real captured card points.
            attacker_pile = (
                UNANNOUNCED_CAPOT_SUBSTITUTE
                if declarer_capot
                else team_card_points[contract_team_name]
            )
            team_scores[contract_team_name] = (
                contract_value
                + attacker_pile
                + belote_bonus(contract_team_name)
            )
            for name in defender_names:
                team_scores[name] = team_card_points[name] + belote_bonus(name)
        else:
            # Failed (chuté): the defense takes the whole pile plus
            # the contract; the declarer keeps only its Belote bonus.
            team_scores[contract_team_name] = belote_bonus(contract_team_name)
            for name in defender_names:
                team_scores[name] = (160 + contract_value) + belote_bonus(name)
    else:
        # Doubled / redoubled: winner-takes-all. The losing side
        # scores nothing but its Belote bonus (always preserved).
        if contract_made:
            team_scores[contract_team_name] = (
                160 + contract_value * multiplier
                + belote_bonus(contract_team_name)
            )
            for name in defender_names:
                team_scores[name] = belote_bonus(name)
        else:
            team_scores[contract_team_name] = belote_bonus(contract_team_name)
            for name in defender_names:
                team_scores[name] = (
                    160 + contract_value * multiplier + belote_bonus(name)
                )

    return RoundScore(
        scores=team_scores,
        contract_made=contract_made,
        unannounced_capot=unannounced_capot,
    )
