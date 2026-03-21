"""
Main entry point for the La Contrée Probability Dashboard.

This module initializes the Streamlit application, handles user interaction
for card selection, and orchestrates the probability and bidding engines
to display a comprehensive analytical dashboard.
"""

import streamlit as st
import plotly.express as px
import pandas as pd

from src.models.deck import Deck, Card, Suit, Rank
from src.models.hand import Hand
from src.engine.probability_engine import ProbabilityEngine
from src.bidding.evaluator import BiddingEvaluator

def get_card_color(suit: Suit) -> str:
    """Return a color for the suit."""
    if suit in [Suit.HEARTS, Suit.DIAMONDS]:
        return "red"
    return "black"

def format_card(card: Card) -> str:
    """Format card string with color for Streamlit markdown."""
    color = get_card_color(card.suit)
    # Mapping suit to symbol
    symbols = {
        Suit.HEARTS: "♥",
        Suit.DIAMONDS: "♦",
        Suit.CLUBS: "♣",
        Suit.SPADES: "♠"
    }
    return f":{color}[{card.rank.value} {symbols[card.suit]}]"

def main() -> None:
    """
    Initialize the Streamlit dashboard for La Contrée.
    Configures the page layout, handles state, and renders all components.
    """
    st.set_page_config(
        page_title="La Contrée Probability Dashboard",
        page_icon="🃏",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    st.title("🃏 La Contrée - Probability & Bidding Dashboard")
    st.markdown(
        "Evaluate your opening hand, assess partner support, and anticipate opponent threats "
        "using advanced combinatorics and hypergeometric probability distributions."
    )

    # Initialize deck and get all formatted options
    deck = Deck()
    all_cards = deck.get_all_cards()
    
    # Base UI Layout
    col1, col2 = st.columns([1, 2])

    with col1:
        st.header("My Hand")
        st.write("Select exactly 8 cards from the deck.")
        
        # Streamlit multiselect
        selected_card_strs = st.multiselect(
            "Your Cards",
            options=[str(c) for c in all_cards],
            max_selections=8,
            help="Choose 8 unique cards to represent your hand."
        )

    # Convert selected strings back to Card objects
    selected_cards = [c for c in all_cards if str(c) in selected_card_strs]

    with col2:
        st.header("Hand Diagnostic")
        
        if len(selected_cards) == 8:
            try:
                hand = Hand(selected_cards)
                
                # Show cards beautifully
                formatted_hand = "  |  ".join([format_card(c) for c in hand.cards])
                st.markdown(f"**Current Hand:** {formatted_hand}")
                
                # --- BIDDING EVALUATION ---
                evaluator = BiddingEvaluator(hand)
                suggestions = evaluator.evaluate()
                
                st.subheader("Bidding Suggestions")
                if suggestions:
                    best_bid = suggestions[0]
                    st.success(f"**Recommended Bid:** {best_bid.value} {best_bid.suit.value}")
                    st.write(f"*Reasoning:* {best_bid.reasoning}")
                    
                    if len(suggestions) > 1:
                        with st.expander("Other Possible Bids"):
                            for s in suggestions[1:]:
                                st.write(f"- **{s.value} {s.suit.value}**: {s.reasoning}")
                else:
                    st.warning("No optimal bid found (Pass). Hand does not meet minimum truth table requirements.")
                
                # --- PROBABILITY ENGINE ---
                engine = ProbabilityEngine(hand)
                
                # Determine assumed trump for probabilities (use best bid suit, or default to Hearts)
                assumed_trump = suggestions[0].suit if suggestions else Suit.HEARTS
                
                col_p, col_t = st.columns(2)
                
                with col_p:
                    st.subheader("Partner Probabilities")
                    # Probability partner has a specific missing card (e.g. Ace of non-trump suit)
                    # We'll calculate prob of partner having ANY specific missing Ace.
                    missing_aces = 4 - hand.count_rank(Rank.ACE)
                    prob_partner_any_missing_ace = engine.prob_partner_has_at_least_one_of(missing_aces)
                    
                    st.metric(
                        label="Support (Partner has at least 1 missing Ace)", 
                        value=f"{prob_partner_any_missing_ace * 100:.1f}%"
                    )
                    
                    # Missing top trumps (J, 9)
                    missing_top_trumps = 0
                    if not hand.has_card(Rank.JACK, assumed_trump):
                        missing_top_trumps += 1
                    if not hand.has_card(Rank.NINE, assumed_trump):
                        missing_top_trumps += 1
                        
                    prob_partner_top_trump = engine.prob_partner_has_at_least_one_of(missing_top_trumps)
                    st.metric(
                        label=f"Support (Partner has missing J/9 of {assumed_trump.value})",
                        value=f"{prob_partner_top_trump * 100:.1f}%" if missing_top_trumps > 0 else "N/A (You hold them)"
                    )
                
                with col_t:
                    st.subheader("Threat Assessment")
                    # Threat: At least one opponent has 3rd Ace in assumed trump suit
                    threat_prob = engine.prob_opponent_threat_third_ace(assumed_trump)
                    st.metric(
                        label=f"Danger (Opponent 3rd Ace in {assumed_trump.value})", 
                        value=f"{threat_prob * 100:.1f}%"
                    )
                    
                    # Overall threat across all non-trump suits where we hold exactly 3 cards
                    threats = []
                    for suit in Suit:
                        if suit != assumed_trump and hand.count_suit(suit) == 3:
                            p = engine.prob_opponent_threat_third_ace(suit)
                            threats.append(f"{suit.value}: {p * 100:.1f}%")
                    
                    if threats:
                        st.write("**3rd Ace Threat in other 3-card suits:**")
                        for t in threats:
                            st.write(f"- {t}")

            except Exception as e:
                st.error(f"Error evaluating hand: {e}")
                
        else:
            st.info(f"Please select exactly 8 cards. Currently selected: {len(selected_cards)}/8.")

    st.divider()
    
    st.header("Distribution Visualizer")
    if len(selected_cards) == 8:
        # Expected distribution of remaining cards among the 3 other players
        data = []
        players = ["Partner", "Opponent 1", "Opponent 2"]
        
        for suit in Suit:
            remaining_in_suit = 8 - hand.count_suit(suit)
            # Expected cards per player = total remaining in suit * (8/24) = total / 3
            expected_per_player = remaining_in_suit / 3.0
            
            for player in players:
                data.append({
                    "Suit": suit.value,
                    "Player": player,
                    "Expected Cards": expected_per_player
                })
                
        df = pd.DataFrame(data)
        
        fig = px.bar(
            df, 
            x="Suit", 
            y="Expected Cards", 
            color="Player", 
            title="Expected Card Distribution Among Unseen Hands",
            barmode="group",
            color_discrete_sequence=["#1f77b4", "#ff7f0e", "#d62728"]
        )
        
        fig.update_layout(
            yaxis_title="Expected Number of Cards",
            xaxis_title="Suit",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)"
        )
        
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Visualizer will be rendered once 8 cards are selected.")

if __name__ == "__main__":
    main()
