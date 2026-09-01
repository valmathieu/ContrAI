"""Round-recap screen rendering for the Rich terminal UI.

The between-rounds panel: contract + made/failed, an Outcome sub-table
(the factual play tally) and a Scoring sub-table (how the round scored),
closing with the running game totals. ``_recap_breakdown`` computes the
per-team point components both sub-tables read; the rest are pure
``(data) -> Panel/Text`` builders ``RichView.show_round_recap`` drives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from contrai_core import Suit, TeamSide
from contrai_core.rule_config import RuleConfig
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_engine.model.round.components import Mark, marked_total
from contrai_engine.view.formatting import (
    _format_contract_short,
    _format_trump_label,
    _suit_color,
    _suit_glyph,
    _team_abbr,
    _team_color,
)
from contrai_engine.view.theme import (
    DEFAULT_TARGET,
    DIM,
    FG,
    GOLD,
    GREEN_CHECK,
    RED,
    YELLOW,
)

if TYPE_CHECKING:
    from contrai_engine.model.round import Round


def _panel_round_recap(
    round_: Round,
    running_scores: dict,
    target_score: int = DEFAULT_TARGET,
    *,
    tiebreaker: bool = False,
    belote_gated: TeamSide | None = None,
) -> Panel:
    """Between-rounds recap panel — what just happened, in one read.

    Two stacked sub-tables share the N-S / E-W columns. The
    **Outcome** table reports the factual play tally — tricks won,
    trick points (trump-aware pile), last trick (10) and belote (20)
    each side captured — closing with a Total of those points. The
    **Scoring** table then summarizes how the round scored: contract
    bonus / penalty, round points (the score-contributing part of the
    tally — belote only on a failed/doubled round), then the round-score
    total. A final Running line carries the game-level totals and the
    target. When ``tiebreaker`` is set (both teams level at/above the
    target) a sudden-death notice closes the panel; when ``belote_gated``
    names a side, a notice says that side is past the target on Belote
    the table's §8 option has not let it win on yet.
    """
    body = Text()
    body.append("\n")
    contract = getattr(round_, "contract", None)
    ns_round = round_.round_scores.get(TeamSide.NS, 0)
    ew_round = round_.round_scores.get(TeamSide.EW, 0)
    running_ns = running_scores.get(TeamSide.NS, 0)
    running_ew = running_scores.get(TeamSide.EW, 0)

    # Contract line
    body.append("  Contract:  ", style=DIM)
    if contract is None:
        body.append("All passed — no contract", style=f"bold {YELLOW}")
        body.append("\n\n")
    else:
        body.append_text(_format_contract_short(contract, verbose=True))
        body.append("\n")
        # Trump recall — the contract label omits the suit, so spell
        # it out here the same way the in-game Round panel does.
        body.append("  Trump:     ", style=DIM)
        body.append_text(_format_trump_label(contract.suit))
        body.append("\n")
        # Made/failed badge
        made = _contract_made(round_)
        body.append("  Result:    ", style=DIM)
        if made:
            body.append("✓ Contract made", style=f"bold {GREEN_CHECK}")
        else:
            body.append("✗ Contract failed", style=f"bold {RED}")
        body.append("\n\n")

    # Two stacked sub-tables sharing the same N-S / E-W columns.
    # "Outcome" first — the factual play tally (tricks won, trick
    # points, last trick, belote each side captured). "Scoring" next
    # — contract bonus, the rolled-up round points, and round score.
    breakdown = _recap_breakdown(round_)
    trump = contract.suit if contract is not None else None
    all_passed = contract is None

    body.append_text(_section_rule("Outcome"))
    body.append("\n")
    body.append_text(
        _format_outcome_table(
            breakdown,
            trump=trump,
            all_passed=all_passed,
            slam_label=getattr(round_, "unannounced_slam", None),
        )
    )
    body.append("\n")

    body.append_text(_section_rule("Scoring"))
    body.append("\n")
    body.append_text(
        _format_recap_table(
            breakdown, ns_round, ew_round, all_passed=all_passed
        )
    )
    body.append("\n")

    # Running game totals + target. Label padded to the shared
    # 24-char column gutter so the numbers line up under N-S / E-W.
    body.append(f"  {'Running':<22}", style=DIM)
    body.append(f"{running_ns:>6}", style=f"bold {_team_color(TeamSide.NS)}")
    body.append(f"  {running_ew:>6}", style=f"bold {_team_color(TeamSide.EW)}")
    body.append(f"     target {target_score}", style=DIM)

    if tiebreaker:
        # Sudden death: both teams sit level at/above the target, so
        # the game continues until one of them leads.
        body.append("\n\n")
        body.append(
            "  Scores level at the target — tiebreaker round follows",
            style=f"bold {GOLD}",
        )

    if belote_gated is not None:
        # §8, gate off: the side is past the target on Belote points
        # play has not confirmed, so the scoreboard reading past the
        # target is not a win yet. Say so, or the game just deals again.
        body.append("\n\n")
        body.append(
            f"  {_team_abbr(belote_gated)} past the target on Belote alone"
            " — the game continues",
            style=f"bold {GOLD}",
        )

    return Panel(
        body,
        title=Text(
            f"Round #{getattr(round_, 'round_number', '?')} recap",
            style=f"bold {GOLD}",
        ),
        border_style=GOLD,
        box=ROUNDED,
        width=70,
    )


def _recap_breakdown(round_) -> dict:
    """Per-team point components used by the recap panel.

    A **projection of the round's** :class:`RoundScore` — the view knows
    no scoring rule of its own. The two Scoring rows are §7.2's two
    components as the table marked them, and the Outcome rows are the
    factual play tally the scorer worked from. One implementation of the
    scoring model therefore serves both the model and the panel, and a
    §9.6 knob reaches the recap the moment the scorer honours it.

    Returns a dict keyed by :class:`TeamSide` with:
        contract:     the **announced-points** component as marked —
                      what the contract itself is worth to this side,
                      carrying the double/redouble multiplier wherever
                      the table's §7.3 convention puts it.
        card_points:  the **made-points** component as marked — what
                      the trick pile is worth to this side: its share
                      of the pile (last-trick bonus included), the flat
                      160 of a failed or doubled round, or a
                      Slam-family / unannounced-sweep substitute.
        belote:       20 per K + Q pair the team actually **marks**,
                      from the scorer's own tally — so at a table with
                      ``belote_lost_when_contract_fails`` on, a failed
                      declarer's belote appears here under the defense.
        belote_held:  20 per pair the team **holds**, before any such
                      transfer. A play fact, which is why the Outcome
                      sub-table reads this one and the Scoring
                      sub-table reads ``belote``.
        round_points: honest play tally — the real trump-aware pile
                      captured plus last-trick (10) and the belote held.
                      Always the true captured total, independent of how
                      the contract converts it into score; the Outcome
                      sub-table renders it verbatim.
        trick_points: the real pile this side captured, last-trick
                      bonus excluded — except for the declarer of an
                      un-doubled sweep, whose pile the scorer replaced
                      with a flat 250 / 500 that absorbs the bonus too.
        last_trick:   10 if the team took the last trick, else 0 (and 0
                      when the substitute above already covers it).
        trick_count:  number of tricks won.

    The three scoring rows reconcile exactly: ``contract +
    card_points + belote`` is this side's round score.
    """
    score = getattr(round_, "round_score", None)
    contract = getattr(round_, "contract", None)
    # The trick tallies come from the core play state — the recap reads
    # them rather than counting tricks of its own.
    play_state = getattr(round_, "play_state", None)
    trick_counts = (
        play_state.trick_counts_by_side if play_state is not None else {}
    )

    if score is None or contract is None:
        # All passed, or the round is not scored yet: nothing to project.
        return {
            side: {
                "contract": 0,
                "card_points": 0,
                "belote": 0,
                "belote_held": 0,
                "round_points": 0,
                "trick_points": 0,
                "last_trick": 0,
                "trick_count": trick_counts.get(side, 0),
            }
            for side in TeamSide
        }

    rules = getattr(round_, "rules", None) or RuleConfig()
    multiplier = score.multiplier
    attacking_side = contract.player.position.team_side
    # Who *held* each pair, before any failure-transfer moved what it is
    # worth to the other side — the Outcome sub-table's question.
    held_counts = _belote_counts_in_round(round_)

    out = {}
    for side in TeamSide:
        mark = score.marks.get(side, Mark(0, 0))
        belote = score.belote_points.get(side, 0)
        belote_held = 20 * held_counts.get(side, 0)
        pile = score.card_points.get(side, 0)
        last_trick = 10 if side is score.last_trick_side else 0

        # Each component reduced by the very function the scorer used.
        # Splitting the mark in two before reducing is exact: both §7.3
        # conventions are linear in the components, so the two halves
        # always add back up to the mark the side actually wrote down.
        announced = marked_total(Mark(0, mark.announced), multiplier, rules)
        made = marked_total(Mark(mark.made, 0), multiplier, rules)

        # Outcome rows stay factual — what this side really captured —
        # with one exception: the declarer of an un-doubled sweep, whose
        # 162 pile the scorer replaced with the flat substitute its tag
        # names, and that substitute absorbs the last-trick bonus too.
        # A table with the substitute switched off marks the real pile,
        # and then ``mark.made`` *is* the pile, so the test below
        # correctly declines to substitute.
        if (
            score.unannounced_slam is not None
            and side is attacking_side
            and mark.made != pile
        ):
            trick_points, shown_last_trick = mark.made, 0
        else:
            trick_points, shown_last_trick = pile - last_trick, last_trick

        out[side] = {
            "contract": announced,
            "card_points": made,
            "belote": belote,
            "belote_held": belote_held,
            "round_points": trick_points + shown_last_trick + belote_held,
            "trick_points": trick_points,
            "last_trick": shown_last_trick,
            "trick_count": trick_counts.get(side, 0),
        }
    return out


def _section_rule(label: str, width: int = 44) -> Text:
    """A dim horizontal rule with a centered section label.

    Renders e.g. ``──────── Outcome ────────`` to split the recap
    panel into its Outcome / Scoring sub-tables. ``width`` is the
    dash-field length (excluding the 2-space left gutter).
    """
    tag = f" {label} "
    fill = max(0, width - len(tag))
    left = fill // 2
    right = fill - left
    rule = Text("  ")
    rule.append("─" * left, style=DIM)
    rule.append(tag, style=f"bold {FG}")
    rule.append("─" * right, style=DIM)
    return rule


def _column_divider() -> Text:
    """A dim rule under the two N-S / E-W number columns only.

    Anchors a sum row (the Outcome ``Total`` or the Scoring ``Round
    score``) without underlining the label gutter. Geometry matches
    the shared layout: a 24-char label gutter, then two 6-wide
    columns separated by two spaces.
    """
    divider = Text()
    divider.append(" " * 24, style=DIM)
    divider.append("─" * 6, style=DIM)
    divider.append("  ", style=DIM)
    divider.append("─" * 6, style=DIM)
    divider.append("\n")
    return divider


def _format_outcome_table(
    breakdown: dict,
    *,
    trump: Optional[Suit] = None,
    all_passed: bool = False,
    slam_label: Optional[str] = None,
) -> Text:
    """Render the per-team play tally — the factual results of play.

    Rows: Tricks won (count), Tricks points (trump-aware pile), Last
    trick (10 to whoever won trick 8), Belote (20 to the side *holding*
    K+Q of trump, even at a table that hands a failed declarer's belote
    to the defense) and a closing Total. Every value is the *real*
    amount each side captured in play, independent of how the contract
    converts it into score — so a winner-takes-all round still surfaces
    the points each side genuinely took. The Total is their per-side
    sum (trick points + last trick + belote held), the honest play
    tally; the Scoring sub-table then reports what actually scored.

    When ``all_passed`` is set (no contract was struck, so no cards
    were played) every cell renders as an em-dash, so the whole panel
    reads consistently.

    When ``slam_label`` is set (an :class:`UnannouncedSlam` member)
    the round was an unannounced Slam: the Tricks points row already
    carries the flat 250 substitute, and the label is appended to its
    right (e.g. ``← Grand Slam``) to explain why.
    """
    ns = breakdown.get(TeamSide.NS, {})
    ew = breakdown.get(TeamSide.EW, {})

    def _count_cell(value: int) -> Text:
        if all_passed:
            return Text(f"{'—':>6}", style=DIM)
        return Text(f"{value:>6}", style="bold")

    def _bonus_cell(value: int) -> Text:
        # Last trick / belote: the captured amount, em-dash when none.
        if all_passed or value == 0:
            return Text(f"{'—':>6}", style=DIM)
        return Text(f"{value:>6}", style="bold")

    # Header row: "                          N-S     E-W"
    header = Text()
    header.append(f"  {'':<22}", style=DIM)
    header.append(f"{_team_abbr(TeamSide.NS):>6}",
                  style=f"bold {_team_color(TeamSide.NS)}")
    header.append(f"  {_team_abbr(TeamSide.EW):>6}",
                  style=f"bold {_team_color(TeamSide.EW)}")
    header.append("\n")

    row_tricks = Text()
    row_tricks.append(f"  {'Tricks won':<22}", style=FG)
    row_tricks.append_text(_count_cell(ns.get("trick_count", 0)))
    row_tricks.append("  ")
    row_tricks.append_text(_count_cell(ew.get("trick_count", 0)))
    row_tricks.append("\n")

    row_points = Text()
    row_points.append(f"  {'Tricks points':<22}", style=FG)
    row_points.append_text(_count_cell(ns.get("trick_points", 0)))
    row_points.append("  ")
    row_points.append_text(_count_cell(ew.get("trick_points", 0)))
    if slam_label and not all_passed:
        # Explain the flat 250 substitute sitting in this row. The
        # UnannouncedSlam member stringifies to its display label.
        row_points.append(f"   ← {slam_label}", style=f"bold {GOLD}")
    row_points.append("\n")

    # Last-trick bonus (10 points to the team that wins trick 8).
    row_last = Text()
    row_last.append(f"  {'Last trick':<22}", style=FG)
    row_last.append_text(_bonus_cell(ns.get("last_trick", 0)))
    row_last.append("  ")
    row_last.append_text(_bonus_cell(ew.get("last_trick", 0)))
    row_last.append("\n")

    # Belote (suit glyph reflects the actual trump suit). The label
    # is hand-built so the trump glyph slots into the 24-char gutter.
    row_bel = Text()
    row_bel.append("  Belote (K + Q ", style=FG)
    # A glyph exists only for a real card suit — None (no contract) and
    # the suitless trump options all fall through to the em-dash.
    if isinstance(trump, Suit):
        row_bel.append(_suit_glyph(trump), style=_suit_color(trump))
    else:
        row_bel.append("—", style=DIM)
    row_bel.append(")      ", style=FG)
    # The pair each side *held* — a play fact, so a table that moves a
    # failed declarer's belote to the defense still shows it here under
    # the seat that announced it. Where it ends up is the Scoring table's
    # business.
    row_bel.append_text(_bonus_cell(ns.get("belote_held", 0)))
    row_bel.append("  ")
    row_bel.append_text(_bonus_cell(ew.get("belote_held", 0)))
    row_bel.append("\n")

    # Total — the honest play tally per side (trick points + last
    # trick + belote), surfaced as ``round_points`` by the breakdown.
    # ``_count_cell`` keeps a literal 0 for a side that captured
    # nothing and an em-dash only when the whole round was passed.
    row_total = Text()
    row_total.append(f"  {'Total':<22}", style=f"bold {FG}")
    row_total.append_text(_count_cell(ns.get("round_points", 0)))
    row_total.append("  ")
    row_total.append_text(_count_cell(ew.get("round_points", 0)))
    row_total.append("\n")

    out = Text()
    out.append_text(header)
    out.append_text(row_tricks)
    # Column rule sets the trick *count* apart from the point rows
    # that follow, mirroring the rule drawn before the Total row.
    out.append_text(_column_divider())
    out.append_text(row_points)
    out.append_text(row_last)
    out.append_text(row_bel)
    out.append_text(_column_divider())
    out.append_text(row_total)
    return out


def _format_recap_table(
    breakdown: dict,
    ns_round: int,
    ew_round: int,
    *,
    all_passed: bool = False,
) -> Text:
    """Render the Scoring sub-table inside the recap panel.

    Rows: Contract (the announced-points component the table marked),
    Round points (the made-points component plus the belote), then a
    divider and the engine-computed Round score.

    The two rows are §7.2's two halves of the mark, so the columns
    reconcile by construction: Contract + Round points = Round score,
    which the divider anchors. On a winner-takes-all round the made
    component is the flat 160 rather than a share of the pile; on a
    failed contract the declarer's is 0, so its row collapses to the
    belote it keeps — or an em-dash when it holds none.
    """
    ns = breakdown.get(TeamSide.NS, {})
    ew = breakdown.get(TeamSide.EW, {})

    def _num_cell(value: int, *, show_zero: bool = True) -> Text:
        t = Text()
        if value == 0 and not show_zero:
            t.append(f"{'—':>6}", style=DIM)
            return t
        t.append(f"{value:>6}", style="bold")
        return t

    def _round_points_cell(side: dict) -> Text:
        # The made-points component plus the belote — everything this
        # side marked that is not the contract itself.
        if all_passed:
            return Text(f"{'—':>6}", style=DIM)
        scored = side.get("card_points", 0) + side.get("belote", 0)
        return _num_cell(scored, show_zero=False)

    # Header row: "                          N-S     E-W"
    header = Text()
    header.append(f"  {'':<22}", style=DIM)
    header.append(f"{_team_abbr(TeamSide.NS):>6}",
                  style=f"bold {_team_color(TeamSide.NS)}")
    header.append(f"  {_team_abbr(TeamSide.EW):>6}",
                  style=f"bold {_team_color(TeamSide.EW)}")
    header.append("\n")

    # Contract row — the bonus each team gets from the contract.
    row_contract = Text()
    row_contract.append(f"  {'Contract':<22}", style=FG)
    row_contract.append_text(
        _num_cell(ns.get("contract", 0), show_zero=False)
    )
    row_contract.append("  ")
    row_contract.append_text(
        _num_cell(ew.get("contract", 0), show_zero=False)
    )
    row_contract.append("\n")

    # Round points row — the score-contributing part of the play tally
    # (belote only on a failed/doubled round, em-dash when none scored).
    row_points = Text()
    row_points.append(f"  {'Round points':<22}", style=FG)
    row_points.append_text(_round_points_cell(ns))
    row_points.append("  ")
    row_points.append_text(_round_points_cell(ew))
    row_points.append("\n")

    row_total = Text()
    row_total.append(f"  {'Round score':<22}", style=f"bold {GOLD}")
    row_total.append_text(_num_cell(ns_round))
    row_total.append("  ")
    row_total.append_text(_num_cell(ew_round))
    row_total.append("\n")

    out = Text()
    out.append_text(header)
    out.append_text(row_contract)
    out.append_text(row_points)
    out.append_text(_column_divider())
    out.append_text(row_total)
    return out


def _belote_counts_in_round(round_) -> dict[TeamSide, int]:
    """How many K + Q pairs each side *holds* this round.

    Belote belongs to whoever holds the pair, not to whichever team
    captures those cards in a trick — see the matching rule in
    :meth:`contrai_engine.model.round.Round.calculate_round_scores`. A
    side marks at most one pair outside all trump and up to four under
    the all-trump ``four`` regime, so the recap multiplies rather than
    tests.
    """
    return getattr(round_, "belote_counts_by_side", None) or {}


def _contract_made(round_) -> bool:
    """Canonical made/failed verdict for ``round_``.

    Reads the engine's :attr:`Round.contract_made` flag — the single
    source of truth. "round_score > 0" is *not* a safe proxy: a
    failed declarer can still score a non-zero Belote bonus. Falls
    back to the score heuristic only for legacy/stub rounds that
    predate the flag.
    """
    made = getattr(round_, "contract_made", None)
    if made is not None:
        return bool(made)
    contract = getattr(round_, "contract", None)
    if contract is None:
        return False
    scores = getattr(round_, "round_scores", {}) or {}
    return scores.get(contract.player.position.team_side, 0) > 0
