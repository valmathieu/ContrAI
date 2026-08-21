"""The two components of a mark (contree-domain.md §7.2).

Every mark written at the end of a round is the sum of a **made-points**
component — what the trick pile is worth — and an **announced-points**
component — what the contract itself is worth. A table marks one or both
(§7.3), and the double/redouble multiplier lands on one or both (§7.2).
Splitting the mark this way is what turns the §9.6 catalogue into a set of
independent expressions instead of a branch per combination.

Nothing here knows about a :class:`Round`, a :class:`contrai_core.Contract`
or a :class:`contrai_core.PlayState` — the whole module is arithmetic over
ints and a :class:`contrai_core.RuleConfig`, so the §7.2 grids can be
tested exactly as the domain reference prints them.
"""

from dataclasses import dataclass

from contrai_core.rule_config import Rounding, RuleConfig

#: What a failed contract's pile is worth to the defense (§7.2). The real
#: pile is 162, but on a failure the defense takes it whole and it is
#: written as a flat 160 rather than counted out. The same flattening
#: applies to every doubled round, won or lost.
FLAT_FAILURE_PILE = 160


@dataclass(frozen=True, slots=True)
class Mark:
    """One side's two §7.2 components, before the multiplier and belote.

    Attributes:
        made: The made-points component — what the trick pile is worth
            to this side: its share of the pile, the flat
            :data:`FLAT_FAILURE_PILE`, or a Slam-family / unannounced-sweep
            substitute.
        announced: The announced-points component — what the contract
            itself is worth to this side. Non-zero for exactly one side:
            the declarer on a made contract, the defense on a failed one.
    """

    made: int
    announced: int


def contract_components(
    *,
    contract_value: int,
    slam_family: bool,
    made: bool,
    multiplier: int,
    attack_pile: int,
    defense_pile: int,
    substitute: int | None,
    rules: RuleConfig,
) -> tuple[Mark, Mark]:
    """Split a round's outcome into the two §7.2 components per side.

    The multiplier is *not* applied here — it lands in
    :func:`marked_total`, because where it bites depends on the marking
    conventions (§7.3). ``multiplier`` is still needed as an input: a
    doubled round turns into a single winner-takes-all stake with the pile
    flat, whatever the conventions later do with it.

    Args:
        contract_value: ``C`` — the numeric bid, or the Slam-family base
            (250 / 500) from ``Contract.get_base_points()``.
        slam_family: Whether the contract is an announced Slam or Solo
            Slam. Only used on a failure, where two §9.6 switches decide
            whether the substitute survives.
        made: Whether the contract was made. Judged by the caller — the
            component rules never re-adjudicate it.
        multiplier: 1, 2 or 4.
        attack_pile: The declaring side's card points *including* the
            last-trick bonus.
        defense_pile: The defending side's, likewise. The two sum to 162
            on a numeric contract.
        substitute: The flat amount that replaces the pile for the side
            that wins the contract — the Slam-family base, the 250 / 500
            of an unannounced sweep, or ``None`` when the pile is counted
            for real.
        rules: The table ruleset.

    Returns:
        ``(declarer_mark, defense_mark)``.
    """

    # --- made points -------------------------------------------------
    if made:
        if substitute is not None:
            # A Slam-family contract, or an unannounced sweep at a table
            # that marks the substitute: the flat amount absorbs the 152
            # cards and the 10-point last-trick bonus alike, and the
            # other side marks nothing.
            made_att, made_def = substitute, 0
        elif multiplier > 1:
            # Doubled and made: winner takes all, the pile flat.
            made_att, made_def = FLAT_FAILURE_PILE, 0
        else:
            # The only shape where both sides mark a pile.
            made_att, made_def = attack_pile, defense_pile
    else:
        made_att = 0
        if slam_family and rules.failed_slam_marks_made_points:
            made_def = (
                substitute if substitute is not None else FLAT_FAILURE_PILE
            )
        else:
            made_def = FLAT_FAILURE_PILE

    # --- announced points --------------------------------------------
    announced = contract_value
    if not made and rules.any_failure_marks_160:
        # The switch replaces C with a flat 160 on every failure — unless
        # the contract was an announced Slam and the table keeps its
        # 250 / 500 there too. That second switch is inert while this one
        # is off, which is exactly what §9.6 documents.
        if not (slam_family and rules.failed_slam_marks_announced_points):
            announced = FLAT_FAILURE_PILE

    if made:
        return Mark(made_att, announced), Mark(made_def, 0)
    return Mark(made_att, 0), Mark(made_def, announced)


def marked_total(mark: Mark, multiplier: int, rules: RuleConfig) -> int:
    """Reduce one side's components to the number actually written down.

    Applies the two §7.3 marking conventions and places the multiplier.
    Under the default table both components are marked and only the
    announced one is multiplied, so a doubled numeric contract marks
    ``160 + C × M``.

    When only one convention is active the multiplier falls on whichever
    component survives — otherwise a double would change nothing at all
    for a table that does not mark announced points (§7.3).

    Args:
        mark: The side's components, from :func:`contract_components`.
        multiplier: 1, 2 or 4.
        rules: The table ruleset.

    Returns:
        The marked total, before belote and before rounding.
    """

    if rules.mark_made_points and rules.mark_announced_points:
        if rules.only_announced_points_multiplied:
            return mark.made + mark.announced * multiplier
        return (mark.made + mark.announced) * multiplier
    if rules.mark_announced_points:
        return mark.announced * multiplier
    return mark.made * multiplier


def round_mark(value: int, rounding: Rounding) -> int:
    """Round one side's mark to the table's step (§7.4).

    Halves round **up**, so a raw 85 marks 90 at
    :attr:`~contrai_core.Rounding.NEAREST_10` and an 85-77 split marks
    90-80 — 170 in total, which §7.4 calls out as the expected
    consequence rather than an error.

    Only the *marks* move: whether the contract was made is judged on
    exact points, upstream of this call. The flat components (160, the
    250 / 500 substitutes, the contract value) and the belote bonus are
    already multiples of ten, so in practice only a shared pile ever
    moves — which is why rounding is applied last, to the finished mark,
    rather than to each component.

    Args:
        value: The side's marked total, belote included.
        rounding: The table's rounding rule.

    Returns:
        The rounded mark. ``NEAREST_5`` can never see an exact half:
        piles are integers, so ``value`` is never a multiple of 5 plus 2.5.
    """

    if rounding is Rounding.NEAREST_10:
        return ((value + 5) // 10) * 10
    if rounding is Rounding.NEAREST_5:
        return ((value + 2) // 5) * 5
    return value
