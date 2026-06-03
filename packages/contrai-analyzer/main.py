"""
Main entry point for the contrée Probability Dashboard.

Tabbed Streamlit application providing suit-agnostic hand analysis:
  Tab 1 — Hand Input + Bidding suggestion
  Tab 2 — Partner support probabilities
  Tab 3 — Opponent threat analysis
  Tab 4 — contrée point distribution chart
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from src.models.deck import SuitSlot, Rank, Card
from src.models.hand import Hand
from src.engine.probability_engine import ProbabilityEngine
from src.bidding.evaluator import BiddingEvaluator


# ---------------------------------------------------------------------------
# Rank ordering helpers
# ---------------------------------------------------------------------------

TRUMP_RANK_ORDER: list[str] = ["J", "9", "A", "10", "K", "Q", "8", "7"]
NON_TRUMP_RANK_ORDER: list[str] = ["A", "10", "K", "Q", "J", "9", "8", "7"]

_LABEL_TO_RANK: dict[str, Rank] = {r.value: r for r in Rank}


def _parse_cards(rank_labels: list[str], slot: SuitSlot) -> list[Card]:
    """Convert a list of rank-label strings into Card objects for `slot`."""
    return [Card(_LABEL_TO_RANK[label], slot) for label in rank_labels]


def _prob_bar(label: str, prob: float, color: str = "#4A90D9") -> None:
    """Render a labelled probability progress bar."""
    st.markdown(
        f"<small>{label}</small>",
        unsafe_allow_html=True,
    )
    col_bar, col_val = st.columns([4, 1])
    with col_bar:
        st.progress(prob)
    with col_val:
        st.markdown(
            f"<div style='text-align:right; padding-top:4px;'><b>{prob * 100:.1f}%</b></div>",
            unsafe_allow_html=True,
        )


def _slot_header(slot: SuitSlot) -> str:
    """Formatted slot header string."""
    return f"{slot.emoji} **{slot.label}**"


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

def main() -> None:
    """Initialize and render the contrée dashboard."""

    st.set_page_config(
        page_title="La Contrée · Dashboard",
        page_icon="🃏",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.title("🃏 La Contrée — Probability Dashboard")
    st.caption(
        "Suit-agnostic analysis · Hypergeometric probabilities · "
        "Partner support · Opponent threats · Point distribution"
    )

    # ------------------------------------------------------------------
    # Card selection state (persisted across tab switches via session_state)
    # ------------------------------------------------------------------

    if "trump_sel" not in st.session_state:
        st.session_state["trump_sel"] = []
    if "blue_sel" not in st.session_state:
        st.session_state["blue_sel"] = []
    if "green_sel" not in st.session_state:
        st.session_state["green_sel"] = []
    if "purple_sel" not in st.session_state:
        st.session_state["purple_sel"] = []

    # Collect all selected cards
    trump_cards  = _parse_cards(st.session_state["trump_sel"],  SuitSlot.TRUMP)
    blue_cards   = _parse_cards(st.session_state["blue_sel"],   SuitSlot.BLUE)
    green_cards  = _parse_cards(st.session_state["green_sel"],  SuitSlot.GREEN)
    purple_cards = _parse_cards(st.session_state["purple_sel"], SuitSlot.PURPLE)

    all_selected: list[Card] = trump_cards + blue_cards + green_cards + purple_cards
    total = len(all_selected)

    # Card count badge
    if total == 8:
        badge = f"✅ **{total}/8 cards selected**"
        badge_color = "green"
    elif total > 8:
        badge = f"🚫 **{total}/8 — too many cards selected**"
        badge_color = "red"
    else:
        badge = f"⚠️ **{total}/8 cards selected**"
        badge_color = "orange"

    st.markdown(
        f"<div style='background:rgba(0,0,0,0.06);padding:6px 14px;"
        f"border-radius:8px;display:inline-block;'>{badge}</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    # ------------------------------------------------------------------
    # Build Hand and engines (only when exactly 8 cards)
    # ------------------------------------------------------------------

    hand: Hand | None = None
    engine: ProbabilityEngine | None = None
    evaluator: BiddingEvaluator | None = None

    if total == 8:
        try:
            hand = Hand(all_selected)
            engine = ProbabilityEngine(hand)
            evaluator = BiddingEvaluator(hand)
        except ValueError as exc:
            st.error(f"Invalid hand: {exc}")

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    tab1, tab2, tab3, tab4 = st.tabs(
        ["🃏 Hand Input", "🤝 Partner Support", "⚔️ Threats", "📊 Points"]
    )

    # ==================================================================
    # TAB 1 — Hand Input
    # ==================================================================

    with tab1:
        st.subheader("Build your hand")

        for slot, key, rank_order in [
            (SuitSlot.TRUMP,  "trump_sel",  TRUMP_RANK_ORDER),
            (SuitSlot.BLUE,   "blue_sel",   NON_TRUMP_RANK_ORDER),
            (SuitSlot.GREEN,  "green_sel",  NON_TRUMP_RANK_ORDER),
            (SuitSlot.PURPLE, "purple_sel", NON_TRUMP_RANK_ORDER),
        ]:
            label_col, sel_col = st.columns([1, 6])
            with label_col:
                st.markdown(
                    f"<div style='padding-top:28px;font-size:1.1em;'>"
                    f"{slot.emoji} <b>{slot.label}</b></div>",
                    unsafe_allow_html=True,
                )
            with sel_col:
                st.multiselect(
                    label=slot.label,
                    options=rank_order,
                    key=key,
                    label_visibility="collapsed",
                    help=f"Select ranks for the {slot.label} slot",
                )

        if total > 8:
            st.warning("You have selected more than 8 cards. Remove some to enable analysis.")

        st.divider()

        # Bidding section —————————————————————————————————————————————
        st.subheader("Bidding")

        if hand and evaluator:
            suggestions = evaluator.evaluate()

            if suggestions:
                bid = suggestions[0]
                bid_color = (
                    "🟢" if bid.value >= 130
                    else "🟡" if bid.value >= 100
                    else "🟠"
                )
                st.success(
                    f"{bid_color} **Recommended bid: {bid.value}** "
                    f"— {bid.reasoning}"
                )
            else:
                st.info("**Pass** — Hand does not meet minimum bidding requirements.")

            # Opponent risk
            risk_slot, risk_prob = evaluator.opponent_bidding_risk()
            risk_color = (
                "#d62728" if risk_prob > 0.5
                else "#ff7f0e" if risk_prob > 0.25
                else "#2ca02c"
            )
            st.markdown(
                f"<div style='margin-top:10px;padding:10px 16px;"
                f"border-left:4px solid {risk_color};"
                f"border-radius:4px;background:rgba(0,0,0,0.04);'>"
                f"⚔️ <b>Opponent bid risk:</b> "
                f"Slot {risk_slot.emoji} <b>{risk_slot.label}</b> "
                f"— <b style='color:{risk_color}'>{risk_prob * 100:.1f}%</b> "
                f"chance an opponent can open 80+</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("Select exactly 8 cards above to see bidding analysis.")

    # ==================================================================
    # TAB 2 — Partner Support
    # ==================================================================

    with tab2:
        if not (hand and engine):
            st.info("Complete your 8-card hand in the **Hand Input** tab to see partner analysis.")
        else:
            st.subheader("🤝 Partner Support")
            st.caption("Hypergeometric probabilities that your partner holds key cards.")

            col_trump, col_aces, col_extra = st.columns(3)

            # ── Trump strength ──────────────────────────────────────
            with col_trump:
                st.markdown("#### 🃏 Trump support")
                my_trumps = hand.count_suit(SuitSlot.TRUMP)
                unknown_trumps = 8 - my_trumps

                st.metric("My trump cards", my_trumps, help="Cards you hold in the trump slot")
                st.metric("Unknown trumps remaining", unknown_trumps)

                st.markdown("**P(partner holds ≥ n trumps)**")
                for n in [1, 2, 3]:
                    prob = engine.prob_partner_has_at_least_n_trumps(n)
                    _prob_bar(f"≥ {n} trump{'s' if n > 1 else ''}", prob, color="#FFD700")

            # ── Missing aces in non-trump slots ─────────────────────
            with col_aces:
                st.markdown("#### Missing aces")
                st.caption("P(partner holds the Ace) for each slot where you don't have it")

                any_missing = False
                for slot in [SuitSlot.BLUE, SuitSlot.GREEN, SuitSlot.PURPLE]:
                    if not hand.has_card(Rank.ACE, slot):
                        any_missing = True
                        prob = engine.prob_partner_has_ace(slot)
                        _prob_bar(f"Ace {slot.emoji} {slot.label}", prob)
                    else:
                        st.markdown(
                            f"<small>✅ You hold the Ace {slot.emoji} {slot.label}</small>",
                            unsafe_allow_html=True,
                        )

                if not any_missing:
                    st.success("You hold all non-trump Aces!")

            # ── Trump ace + top trumps ───────────────────────────────
            with col_extra:
                st.markdown("#### 🃏 Trump key cards")

                # Trump ace
                if not hand.has_card(Rank.ACE, SuitSlot.TRUMP):
                    prob_a = engine.prob_partner_has_trump_ace()
                    _prob_bar("Partner has Trump Ace", prob_a, color="#FFD700")
                else:
                    st.markdown(
                        "<small>✅ You hold the Trump Ace</small>",
                        unsafe_allow_html=True,
                    )

                # Missing top trumps (J / 9)
                missing_top = []
                if not hand.has_card(Rank.JACK, SuitSlot.TRUMP):
                    missing_top.append("J")
                if not hand.has_card(Rank.NINE, SuitSlot.TRUMP):
                    missing_top.append("9")

                if missing_top:
                    st.markdown("**P(partner holds ≥ 1 missing top trump)**")
                    prob_top = engine.prob_partner_has_at_least_one_of(len(missing_top))
                    _prob_bar(
                        f"Missing: {', '.join(missing_top)} of trump",
                        prob_top,
                        color="#FFD700",
                    )
                else:
                    st.markdown(
                        "<small>✅ You hold both J and 9 of trump</small>",
                        unsafe_allow_html=True,
                    )

    # ==================================================================
    # TAB 3 — Threats
    # ==================================================================

    with tab3:
        if not (hand and engine and evaluator):
            st.info("Complete your 8-card hand in the **Hand Input** tab to see threat analysis.")
        else:
            st.subheader("⚔️ Opponent Threats")
            st.caption("Probabilities that opponents hold cards that can counter your hand.")

            col_trump_t, col_aces_t, col_bid_t = st.columns(3)

            # ── Opponent trump threats ───────────────────────────────
            with col_trump_t:
                st.markdown("#### Trump control")

                # J + 9 both in opponent hand
                prob_j9 = engine.prob_opponent_has_both_j_and_9()
                has_j = hand.has_card(Rank.JACK,  SuitSlot.TRUMP)
                has_9 = hand.has_card(Rank.NINE,  SuitSlot.TRUMP)

                if has_j and has_9:
                    st.markdown(
                        "<small>✅ You hold both J and 9 of trump — no J+9 threat</small>",
                        unsafe_allow_html=True,
                    )
                else:
                    _prob_bar("Opponent holds J + 9 of trump", prob_j9, color="#d62728")

                # Trump ace threat
                if not hand.has_card(Rank.ACE, SuitSlot.TRUMP):
                    prob_a_opp = engine.prob_opponent_has_ace(SuitSlot.TRUMP)
                    _prob_bar("Opponent holds Trump Ace", prob_a_opp, color="#d62728")
                else:
                    st.markdown(
                        "<small>✅ You hold the Trump Ace</small>",
                        unsafe_allow_html=True,
                    )

                # Third-ace threat in trump
                prob_3rd = engine.prob_opponent_threat_third_ace(SuitSlot.TRUMP)
                if prob_3rd > 0:
                    _prob_bar("Opponent has 3rd Ace in trump (Ace + 2+)", prob_3rd, color="#ff7f0e")

            # ── Opponent holds missing aces ──────────────────────────
            with col_aces_t:
                st.markdown("#### Non-trump Aces held by opponents")
                for slot in [SuitSlot.BLUE, SuitSlot.GREEN, SuitSlot.PURPLE]:
                    if not hand.has_card(Rank.ACE, slot):
                        prob = engine.prob_opponent_has_ace(slot)
                        _prob_bar(f"Opp has Ace {slot.emoji} {slot.label}", prob, color="#d62728")
                    else:
                        st.markdown(
                            f"<small>✅ You hold the Ace {slot.emoji} {slot.label}</small>",
                            unsafe_allow_html=True,
                        )

            # ── Opponent bidding risk ────────────────────────────────
            with col_bid_t:
                st.markdown("#### Bidding risk")

                risk_slot, risk_prob = evaluator.opponent_bidding_risk()
                risk_label_color = (
                    "#d62728" if risk_prob > 0.5
                    else "#ff7f0e" if risk_prob > 0.25
                    else "#2ca02c"
                )

                st.markdown(
                    f"<div style='padding:12px;border-radius:8px;"
                    f"border:2px solid {risk_label_color};"
                    f"text-align:center;'>"
                    f"<div style='font-size:2em;'>{risk_slot.emoji}</div>"
                    f"<div><b>{risk_slot.label}</b> is the most dangerous slot</div>"
                    f"<div style='font-size:1.4em;color:{risk_label_color};'>"
                    f"<b>{risk_prob * 100:.1f}%</b></div>"
                    f"<div><small>chance opponent can open 80+</small></div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # Show all three non-trump slot risks for comparison
                st.write("")
                st.markdown("**All non-trump slot risks**")
                for slot in [SuitSlot.BLUE, SuitSlot.GREEN, SuitSlot.PURPLE]:
                    p = engine.prob_opponent_can_bid_slot(slot)
                    _prob_bar(f"{slot.emoji} {slot.label}", p, color="#ff7f0e")

    # ==================================================================
    # TAB 4 — Points
    # ==================================================================

    with tab4:
        if not (hand and engine):
            st.info("Complete your 8-card hand in the **Hand Input** tab to see point distribution.")
        else:
            st.subheader("📊 Contrée Point Distribution")
            st.caption(
                "My exact points vs expected partner / opponent points per slot. "
                "Total deck = 162 pts (excl. last-trick bonus)."
            )

            # Build data
            my_pts_by_slot = {
                slot: sum(c.point_value for c in hand.get_suit_cards(slot))
                for slot in SuitSlot
            }
            partner_pts = engine.expected_points_by_slot("partner")
            opp_pts     = engine.expected_points_by_slot("opponents")

            slot_labels = [f"{s.emoji} {s.label}" for s in SuitSlot]
            slot_colors = [s.color for s in SuitSlot]

            data_rows = []
            for slot in SuitSlot:
                lbl = f"{slot.emoji} {slot.label}"
                data_rows.append({"Slot": lbl, "Player": "Me (exact)",          "Points": my_pts_by_slot[slot]})
                data_rows.append({"Slot": lbl, "Player": "Partner (expected)",  "Points": round(partner_pts[slot], 1)})
                data_rows.append({"Slot": lbl, "Player": "Opponents (expected)", "Points": round(opp_pts[slot], 1)})

            df = pd.DataFrame(data_rows)

            fig = px.bar(
                df,
                x="Slot",
                y="Points",
                color="Player",
                barmode="group",
                color_discrete_map={
                    "Me (exact)":           "#2ecc71",
                    "Partner (expected)":   "#3498db",
                    "Opponents (expected)": "#e74c3c",
                },
                title="Expected Contrée Points by Slot",
                text_auto=".0f",
            )
            fig.update_layout(
                yaxis_title="Points",
                xaxis_title="Slot",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                legend_title_text="Player",
                font=dict(size=13),
                bargap=0.15,
                bargroupgap=0.05,
            )
            fig.update_traces(textposition="outside")

            st.plotly_chart(fig, width="stretch")

            # Summary totals table
            st.markdown("#### Totals")
            my_total      = sum(my_pts_by_slot.values())
            partner_total = sum(partner_pts.values())
            opp_total     = sum(opp_pts.values())

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Me", f"{my_total} pts")
            c2.metric("Partner (expected)", f"{partner_total:.0f} pts")
            c3.metric("Opponents (expected)", f"{opp_total:.0f} pts")
            c4.metric("Deck total", "162 pts", help="Excluding last-trick 10-pt bonus")

            # Per-slot breakdown table
            st.markdown("#### Breakdown by slot")
            breakdown = {
                "Slot": slot_labels,
                "Me": [my_pts_by_slot[s] for s in SuitSlot],
                "Partner ~": [f"{partner_pts[s]:.1f}" for s in SuitSlot],
                "Opponents ~": [f"{opp_pts[s]:.1f}" for s in SuitSlot],
            }
            st.dataframe(pd.DataFrame(breakdown), hide_index=True, width="stretch")


if __name__ == "__main__":
    main()
