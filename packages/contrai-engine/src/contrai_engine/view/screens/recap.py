"""Round-recap screen rendering for the Rich terminal UI.

The between-rounds panel: contract + made/failed, an Outcome sub-table
(the factual play tally) and a Scoring sub-table (how the round scored),
closing with the running game totals. ``_recap_breakdown`` computes the
per-team point components both sub-tables read; the rest are pure
``(data) -> Panel/Text`` builders ``RichView.show_round_recap`` drives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from contrai_core import Suit
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_engine.view.formatting import (
    _format_contract_short,
    _format_trump_label,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.theme import (
    BLUE,
    DEFAULT_TARGET,
    DIM,
    FG,
    GOLD,
    GREEN_CHECK,
    ORANGE,
    RED,
    YELLOW,
)

if TYPE_CHECKING:
    from contrai_engine.model.round import Round


def _panel_round_recap(
    round_: "Round",
    running_scores: dict,
    target_score: int = DEFAULT_TARGET,
) -> Panel:
    """Between-rounds recap panel — what just happened, in one read.

    Two stacked sub-tables share the N-S / E-W columns. The
    **Outcome** table reports the factual play tally — tricks won,
    trick points (trump-aware pile), last trick (10) and belote (20)
    each side captured — closing with a Total of those points. The
    **Scoring** table then summarizes how the round scored: contract
    bonus / penalty, round points (the score-contributing part of the
    tally — belote only on a chuté/contré round), then the round-score
    total. A final Running line carries the game-level totals and the
    target.
    """
    body = Text()
    body.append("\n")
    contract = getattr(round_, "contract", None)
    ns_round = round_.round_scores.get("North-South", 0)
    ew_round = round_.round_scores.get("East-West", 0)
    running_ns = running_scores.get("North-South", 0)
    running_ew = running_scores.get("East-West", 0)

    # Contract line
    body.append("  Contract:  ", style=DIM)
    if contract is None:
        body.append("All passed — no contract", style=f"bold {YELLOW}")
        body.append("\n\n")
    else:
        body.append_text(_format_contract_short(contract, verbose=True))
        body.append("\n")
        # Trump recall — the contract label omits the suit, so spell
        # it out here the same way the in-game Round panel does, but
        # without the ★ flourish (the recap keeps this line plain).
        body.append("  Trump:     ", style=DIM)
        body.append_text(_format_trump_label(contract.suit, star=False))
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
            capot_label=getattr(round_, "unannounced_capot", None),
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
    body.append(f"{running_ns:>6}", style=f"bold {BLUE}")
    body.append(f"  {running_ew:>6}", style=f"bold {ORANGE}")
    body.append(f"     target {target_score}", style=DIM)

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

    Returns a dict keyed by team name with:
        contract:     contract-related bonus credited to this team
                      (attacker base on numeric un-doubled made,
                      160+C*mult to the winning side on numeric
                      failed *and* on numeric doubled/redoubled made
                      — winner-takes-all; base*mult on Slam family
                      for the side winning the contract; 0 otherwise).
        card_points:  sum of card.get_points(trump) across the
                      team's tricks (trump-aware) for numeric
                      contracts, *or* the flat substitute
                      ``slam_card_substitute * multiplier`` credited
                      to the side winning a Slam-family contract.
                      The ``card_points_substituted`` flag tells the
                      renderer which kind it is.
        card_points_substituted:
                      True iff this round uses a Slam-family flat
                      substitute instead of the actual trick pile.
                      Drives the row label ("Tricks won (cards)" vs
                      "Tricks won (subst.)").
        round_points: honest play tally — the real trump-aware pile
                      captured plus last-trick (10) and belote (20).
                      Always the true captured total, independent of
                      how the contract converts it into score; the
                      Outcome sub-table renders it verbatim.
        dix_de_der:   10 if the team took the last trick, else 0.
        belote:       20 if the team *holds* both K and Q of trump
                      (``belote_holder``), else 0.
        trick_count:  number of tricks won.
        cards_count:  True when ``card_points`` contributes to the
                      team's round score (and should render as a
                      number). False → em-dash.
        dix_count:    True when ``dix_de_der`` contributes; False →
                      em-dash. (Always False for Slam family and for
                      any doubled/failed numeric round — the flat
                      winner-takes-all bonus already covers the pile.)
        belote_count: True when ``belote`` contributes — i.e. iff
                      this team holds the pair. Belote is always
                      preserved, win or lose, in every scoring shape.

    Each component is the *contribution to round_score* — so
    contract + card_points + dix_de_der + belote always equals
    the engine's round_score for that team.
    """
    contract = getattr(round_, "contract", None)
    trump = contract.suit if contract else None
    team_tricks = getattr(round_, "team_tricks", {}) or {}
    last_trick_team = None
    last_trick_winner = getattr(round_, "last_trick_winner", None)
    if last_trick_winner is not None and last_trick_winner.team is not None:
        last_trick_team = last_trick_winner.team.name

    belote_team = _belote_team_in_round(round_)

    attacking_team = (
        contract.team.name if contract is not None else None
    )
    contract_made = contract is not None and _contract_made(round_)
    # Unannounced-capot marker set by the engine (None or an
    # UnannouncedSlam member). When present, the declaring team's 162
    # pile is shown as the flat 250 substitute with the der folded in.
    unannounced_capot = getattr(round_, "unannounced_capot", None)
    if contract is not None:
        base = contract.get_base_points()
        mult = contract.get_multiplier()
        is_slam_family = contract.is_slam_family()
        slam_substitute = contract.get_slam_card_substitute()
    else:
        base = 0
        mult = 1
        is_slam_family = False
        slam_substitute = 0

    out = {}
    for team_name in ("North-South", "East-West"):
        tricks = team_tricks.get(team_name, [])
        raw_card_pts = sum(
            card.get_points(trump)
            for tr in tricks
            for _, card in tr.get_plays()
        )
        raw_dix = 10 if team_name == last_trick_team else 0
        raw_belote = 20 if team_name == belote_team else 0

        is_attacker = (team_name == attacking_team)
        is_winner = (is_attacker == contract_made)
        contract_row = 0
        card_points_value = raw_card_pts
        card_points_substituted = False
        cards_count = True
        dix_count = True
        # Outcome-row display values. Default to the real captured
        # pile / der; the unannounced-capot branch swaps the pile for
        # the flat 250 substitute and folds the der in (shows 0).
        display_trick_points = raw_card_pts
        display_last_trick = raw_dix
        # Belote (+20) is always preserved for the team holding the
        # pair, win or lose — so it counts iff this team is the
        # holder, in every scoring shape.
        belote_count = (team_name == belote_team)

        if contract is None:
            # All passed — nothing scores.
            cards_count = False
            dix_count = False
        elif is_slam_family:
            # Slam family: the 162 of trick-card points is replaced
            # by a flat substitute equal to the contract base. The
            # at-risk amount on each half (contract / substitute)
            # scales with the multiplier and goes to the side that
            # wins the contract. Belote (+20) still applies on top
            # for whichever team holds it. Dix de der does NOT — the
            # substitute already covers the 162.
            card_points_substituted = True
            dix_count = False
            if is_winner:
                contract_row = base * mult
                card_points_value = slam_substitute * mult
                cards_count = True
            else:
                card_points_value = 0
                cards_count = False
        elif mult == 1:
            # Numeric, un-doubled: the two sides share the pile.
            if contract_made:
                # Made → declarer adds the contract value on top of
                # its card pile; both sides keep cards/der/belote.
                if is_attacker:
                    contract_row = base
                if is_attacker and unannounced_capot is not None:
                    # Unannounced capot: the declarer's 162 pile
                    # (der included) is replaced by the flat 250
                    # substitute, mirroring the announced-Slam shape.
                    card_points_value = 250
                    card_points_substituted = True
                    dix_count = False
                    display_trick_points = 250
                    display_last_trick = 0
            else:
                # Failed → defender takes the whole pile + contract;
                # the declarer keeps only its belote.
                cards_count = False
                dix_count = False
                if not is_attacker:
                    contract_row = 160 + base
        else:
            # Numeric, doubled / redoubled: winner-takes-all. The
            # flat 160 + C×M replaces the cards/der pile for both
            # sides; the loser scores only its belote.
            cards_count = False
            dix_count = False
            if is_winner:
                contract_row = 160 + base * mult

        out[team_name] = {
            "contract": contract_row,
            "card_points": card_points_value if cards_count else 0,
            "card_points_substituted": card_points_substituted,
            # Honest play tally for the Outcome sub-table: the real
            # trump-aware pile this team captured plus the last-trick
            # (10) and belote (20) it earned in play. Independent of
            # how the contract converts these into score — so it still
            # reflects real captured points in a winner-takes-all round
            # where the Scoring rows are dashed out. The display values
            # equal the raw ones except on an unannounced capot, where
            # the pile reads 250 and the der is folded in (0).
            "round_points": display_trick_points + display_last_trick + raw_belote,
            # Factual components the Outcome sub-table renders one per
            # row. ``trick_points`` is the real pile and ``last_trick``
            # the real der (10/0), both independent of the scoring
            # formula; ``belote`` below is already factual (the holder
            # keeps it in every shape).
            "trick_points": display_trick_points,
            "last_trick": display_last_trick,
            "dix_de_der": raw_dix if dix_count else 0,
            "belote": raw_belote if belote_count else 0,
            "trick_count": len(tricks),
            "cards_count": cards_count,
            "dix_count": dix_count,
            "belote_count": belote_count,
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
    capot_label: Optional[str] = None,
) -> Text:
    """Render the per-team play tally — the factual results of play.

    Rows: Tricks won (count), Tricks points (trump-aware pile), Last
    trick (10 to whoever won trick 8), Belote (20 to the side holding
    K+Q of trump) and a closing Total. Every value is the *real*
    amount each side captured in play, independent of how the contract
    converts it into score — so a winner-takes-all round still surfaces
    the points each side genuinely took. The Total is their per-side
    sum (trick points + last trick + belote), the honest play tally;
    the Scoring sub-table then reports how much of it actually scored.

    When ``all_passed`` is set (no contract was struck, so no cards
    were played) every cell renders as an em-dash, so the whole panel
    reads consistently.

    When ``capot_label`` is set (an :class:`UnannouncedSlam` member)
    the round was an unannounced capot: the Tricks points row already
    carries the flat 250 substitute, and the label is appended to its
    right (e.g. ``← Grand Slam``) to explain why.
    """
    ns = breakdown.get("North-South", {})
    ew = breakdown.get("East-West", {})

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
    header.append(f"{'N-S':>6}", style=f"bold {BLUE}")
    header.append(f"  {'E-W':>6}", style=f"bold {ORANGE}")
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
    if capot_label and not all_passed:
        # Explain the flat 250 substitute sitting in this row. The
        # UnannouncedSlam member stringifies to its display label.
        row_points.append(f"   ← {capot_label}", style=f"bold {GOLD}")
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
    if trump is not None and trump != Suit.NO_TRUMP:
        row_bel.append(_suit_glyph(trump), style=_suit_color(trump))
    else:
        row_bel.append("—", style=DIM)
    row_bel.append(")      ", style=FG)
    row_bel.append_text(_bonus_cell(ns.get("belote", 0)))
    row_bel.append("  ")
    row_bel.append_text(_bonus_cell(ew.get("belote", 0)))
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
    # Blank line sets the trick *count* apart from the point rows that
    # follow (a column rule here would wrongly read as a sub-total).
    out.append("\n")
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

    Rows: Contract (the bonus a team earns from the contract being
    made or failed), Round points (the part of the play tally that
    actually scored), then a divider and the engine-computed Round
    score.

    Round points is the score-contributing roll-up, not the raw tally:
    ``card_points + dix_de_der + belote`` — i.e. ``Round score −
    Contract`` by the :meth:`_recap_breakdown` invariant. On a
    winner-takes-all round (chuté or contré) the captured pile and
    last trick stop counting, so the row collapses to just the belote
    the holder keeps, or an em-dash when no belote is held. For engine
    data the columns therefore reconcile: Contract + Round points =
    Round score, which the divider anchors.
    """
    ns = breakdown.get("North-South", {})
    ew = breakdown.get("East-West", {})

    def _num_cell(value: int, *, show_zero: bool = True) -> Text:
        t = Text()
        if value == 0 and not show_zero:
            t.append(f"{'—':>6}", style=DIM)
            return t
        t.append(f"{value:>6}", style="bold")
        return t

    def _round_points_cell(side: dict) -> Text:
        # The score-contributing part only: cards + der + belote, each
        # already zeroed by the breakdown when it doesn't count. A
        # chuté/contré round leaves belote alone, so this is belote
        # (or an em-dash when the side holds none).
        if all_passed:
            return Text(f"{'—':>6}", style=DIM)
        scored = (
            side.get("card_points", 0)
            + side.get("dix_de_der", 0)
            + side.get("belote", 0)
        )
        return _num_cell(scored, show_zero=False)

    # Header row: "                          N-S     E-W"
    header = Text()
    header.append(f"  {'':<22}", style=DIM)
    header.append(f"{'N-S':>6}", style=f"bold {BLUE}")
    header.append(f"  {'E-W':>6}", style=f"bold {ORANGE}")
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
    # (belote only on a chuté/contré round, em-dash when none scored).
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


def _belote_team_in_round(round_) -> Optional[str]:
    """Return the team *holding* both K and Q of trump this round.

    Belote belongs to whoever holds the pair (``belote_holder``),
    not to whichever team captures those cards in a trick — see
    contree-domain.md §6.5 and the matching rule in
    :meth:`contrai_engine.model.round.Round.calculate_round_scores`.
    """
    holder = getattr(round_, "belote_holder", None)
    if holder is None or getattr(holder, "team", None) is None:
        return None
    return holder.team.name


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
    return scores.get(contract.team.name, 0) > 0
