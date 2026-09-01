"""Round scoring — the pure transformation from a played-out round to
its team scores.

``score_round`` reads the authoritative :class:`contrai_core.PlayState`
on the round (contract, the per-side captured piles and trick counts,
and the per-trick winners) plus the round's belote counts, reduces them
to the numbers ``contree-domain.md`` §7.2 needs, and hands those to
:mod:`components` — which owns the arithmetic. It mutates nothing. The
thin ``Round.calculate_round_scores`` wrapper publishes the resulting
:class:`RoundScore` onto the round. Keeping the maths side-effect-free
here isolates the scoring rules from the lifecycle orchestrator.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Sequence, TYPE_CHECKING

from contrai_core.bid import SlamLevel
from contrai_core.team_side import TeamSide

from .components import Mark, contract_components, marked_total, round_mark

if TYPE_CHECKING:
    from contrai_core.player import BasePlayer
    from ..player import Player
    from .round import Round


class UnannouncedSlam(Enum):
    """Outcome tag for an *unannounced* all-tricks sweep on a numeric contract.

    Set by :func:`score_round` (via :meth:`Round.calculate_round_scores`)
    after play, when the declaring team takes all 8 tricks on an
    un-doubled numeric (80-180) contract without having bid a Slam.
    The round still scores on the numeric path — the bidder's
    contract value plus a flat substitute for the trick pile, 250 or 500
    depending on the member below — *not* the Slam at-risk grid.

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

    Attributes:
        scores: Per-team round scores, keyed by :class:`TeamSide` — the
            numbers actually written down, belote included.
        contract_made: The canonical made/failed signal — ``None`` when
            the round was all-passed (no contract), else a bool.
        unannounced_slam: The :class:`UnannouncedSlam` tag when the
            declaring team swept all 8 tricks on an un-doubled numeric
            contract, else ``None``.
        marks: Each side's two §7.2 components, before the multiplier and
            before belote. What the recap breaks the score down into.
        belote_points: 20 per pair each side marks, after any
            failure-transfer.
        card_points: Each side's raw captured pile, last-trick bonus
            included — 162 across the two on a numeric contract, and the
            input the contract was judged on.
        last_trick_side: The side that took the last trick, or ``None``
            when no trick was played.
        multiplier: 1, 2 or 4 — needed to reduce ``marks`` back to
            ``scores``.
    """

    scores: Dict[TeamSide, int]
    contract_made: Optional[bool]
    unannounced_slam: Optional[UnannouncedSlam]
    marks: Dict[TeamSide, Mark]
    belote_points: Dict[TeamSide, int]
    card_points: Dict[TeamSide, int]
    last_trick_side: Optional[TeamSide]
    multiplier: int


def sweep_substitute(tag: Optional[UnannouncedSlam]) -> int:
    """The flat amount an unannounced sweep puts in place of the pile.

    A sweep the declaring team never bid still marks a flat
    substitute rather than its 162 of cards, and *which* flat amount
    depends on who did the sweeping: a split team sweep marks the 250
    of the Slam it could have bid, while the declarer's personal sweep
    marks the 500 of the Solo Slam that was there for the taking. A
    partner's solo sweep is not that shape and stays on the team's 250.

    Named for the *sweep*, not for the ruleset row: the bool that decides
    whether a table marks this substitute at all is
    :attr:`contrai_core.RuleConfig.unannounced_slam_substitute`, and two
    different things under one name in the same call graph is a trap.

    Args:
        tag: The round's :class:`UnannouncedSlam` classification, or
            ``None`` for an ordinary round.

    Returns:
        500 for a declarer's personal sweep, 250 for a team sweep, and
        0 when there was no sweep at all (the pile is counted for real).
    """
    if tag is UnannouncedSlam.GRAND_SLAM:
        return SlamLevel.SOLO_SLAM.base_value
    if tag is UnannouncedSlam.SLAM:
        return SlamLevel.SLAM.base_value
    return 0


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

    The round is reduced to the numbers §7.2 needs — each side's pile,
    the contract value, the multiplier, whether the pile is replaced by a
    substitute — and :mod:`components` turns those into the two
    components of each side's mark. Belote is added on top, because it is
    a *held-cards* bonus that belongs to whoever holds K + Q whatever the
    round did (§6.6). How many pairs mark is the round's call: one at a
    suit contract, none at no trump, and none / the first announced /
    every pair at all trump, where a side can mark up to four.

    Nothing about the scoring *rules* lives here: this function's job is
    reading the round and judging made/failed.

    The table ruleset is read off ``round_.rules`` rather than taken as
    an argument, so a round can only ever be scored under the ruleset it
    was played under: the same object seeded ``round_.play_state`` and
    decided which belote pairs mark.

    Args:
        round_: The played-out round, read by reference (contract,
            play_state, belote_counts_by_side, players_order, rules).
            Nothing on it is mutated.

    Returns:
        A :class:`RoundScore` carrying the per-team scores, their two
        components, the belote credited, the raw piles, the made/failed
        signal and any unannounced-Slam tag.
    """
    rules = round_.rules
    # Every seat has a Position and every Position has a side, so the
    # score buckets are derived from the seating rather than from the
    # mutable Team roster objects.
    sides = {player.position.team_side for player in round_.players_order}

    if not round_.contract:
        # All passed: no contract, nothing to decompose.
        return RoundScore(
            scores={side: 0 for side in sides},
            contract_made=None,
            unannounced_slam=None,
            marks={side: Mark(0, 0) for side in sides},
            belote_points={side: 0 for side in sides},
            card_points={side: 0 for side in sides},
            last_trick_side=None,
            multiplier=1,
        )

    # The authoritative play history. ``card_points_by_side`` hands back a
    # fresh mapping on every access, so layering the last-trick bonus onto
    # it here cannot leak back into the state.
    play_state = round_.play_state
    trick_winners = play_state.trick_winners
    card_points = play_state.card_points_by_side
    trick_counts = play_state.trick_counts_by_side

    last_trick_side = (
        trick_winners[-1].position.team_side if trick_winners else None
    )
    if last_trick_side is not None:
        card_points[last_trick_side] += 10

    # Belote (+20 per pair) belongs to the side *holding* K + Q, not to
    # whoever wins the trick those cards land in.
    belote_counts = round_.belote_counts_by_side
    belote_points = {side: 20 * belote_counts[side] for side in sides}

    contract = round_.contract
    contract_side = contract.player.position.team_side
    defender_sides = [side for side in sides if side is not contract_side]
    multiplier = contract.get_multiplier()
    slam_family = contract.is_slam_family()
    # ``contract.value`` is a SlamLevel on the Slam path — the base-point
    # accessor is the only spelling that is right on both paths.
    contract_value = contract.get_base_points()

    unannounced_slam: Optional[UnannouncedSlam] = None

    if slam_family:
        # Made-ness is a trick predicate — an announced Slam never
        # consults points. Solo Slam additionally requires the *bidder
        # personally* to have won all 8: a team sweep the partner helped
        # with is not the contract that was bid.
        contract_made = trick_counts[contract_side] == 8
        if contract.is_solo_slam():
            contract_made = contract_made and count_player_tricks(
                trick_winners, contract.player
            ) == 8
    else:
        # An unannounced sweep: the declaring team took all 8 on a
        # numeric contract without having bid a Slam. Recognised only
        # un-doubled — a doubled sweep keeps the winner-takes-all shape
        # (§7.2, "Un-doubled only"). GRAND_SLAM when the contracting
        # player swept personally, which is worth the 500 of the Solo
        # Slam that was there for the taking.
        if multiplier == 1 and trick_counts[contract_side] == 8:
            unannounced_slam = (
                UnannouncedSlam.GRAND_SLAM
                if count_player_tricks(trick_winners, contract.player) == 8
                else UnannouncedSlam.SLAM
            )
        # Belote is a *held-cards* bonus, credited below whatever
        # happens. Whether it also counts toward the contract — both for
        # reaching C and for out-scoring the defense — is the table's
        # call (§6.6, §9.5).
        counted = rules.belote_counts_toward_contract
        attack_realized = card_points[contract_side] + (
            belote_points[contract_side] if counted else 0
        )
        defense_realized = sum(
            card_points[side] + (belote_points[side] if counted else 0)
            for side in defender_sides
        )
        contract_made = attack_realized >= contract_value
        if rules.attack_must_outscore_defense:
            # §7.5: reaching C is not enough, and a dispute (an exact
            # tie) therefore fails the contract — no separate knob
            # needed, the strict comparison is the whole rule.
            contract_made = contract_made and attack_realized > defense_realized
        # A sweep can never fail — every trick is already taken, so it
        # out-scores by construction and short-circuits both tests.
        contract_made = unannounced_slam is not None or contract_made

    # §6.6: a table may take the failing attackers' Belote and give it to
    # the defense. A defending team's Belote is never taken, so the
    # transfer is one-directional and only on a failure — and it happens
    # *after* the contract is judged, so a belote that carried the
    # contract home is never the one that moves.
    if not contract_made and rules.belote_lost_when_contract_fails:
        forfeited = belote_points[contract_side]
        belote_points[contract_side] = 0
        for side in defender_sides:
            belote_points[side] += forfeited

    # The flat amount that stands in for the pile, or None to count it.
    # An announced Slam always substitutes; a sweep the table does not
    # substitute for marks its real 162 instead, so the round scores like
    # any other made contract (§7.2). The tag survives either way — it
    # classifies what happened, not what it is worth.
    substitute: Optional[int] = None
    if slam_family:
        substitute = contract.get_slam_card_substitute()
    elif unannounced_slam is not None and rules.unannounced_slam_substitute:
        substitute = sweep_substitute(unannounced_slam)

    attack_mark, defense_mark = contract_components(
        contract_value=contract_value,
        slam_family=slam_family,
        made=contract_made,
        multiplier=multiplier,
        attack_pile=card_points[contract_side],
        defense_pile=sum(card_points[side] for side in defender_sides),
        substitute=substitute,
        rules=rules,
    )
    # ``defender_sides`` holds exactly one side at a four-player table, so
    # ``defense_mark`` is assigned once; the loop is there because
    # ``sides`` is derived from the seating rather than hardcoded to two.
    marks = {contract_side: attack_mark}
    for side in defender_sides:
        marks[side] = defense_mark

    # §7.4 last, on the finished mark: the flat components and the belote
    # are already multiples of ten, so only a shared pile ever moves, and
    # rounding before or after adding belote is the same number.
    scores = {
        side: round_mark(
            marked_total(marks[side], multiplier, rules) + belote_points[side],
            rules.rounding,
        )
        for side in sides
    }

    return RoundScore(
        scores=scores,
        contract_made=contract_made,
        unannounced_slam=unannounced_slam,
        marks=marks,
        belote_points=belote_points,
        card_points=card_points,
        last_trick_side=last_trick_side,
        multiplier=multiplier,
    )
