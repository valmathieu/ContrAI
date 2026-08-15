"""Opening-hand strength analysis for Contrée.

The package holds everything the Streamlit dashboard computes with, so the UI
layer (``main.py``) stays pure glue:

* :mod:`contrai_analyzer.models` — the suit-agnostic card model built on
  ``SuitSlot``, which slots a hand by each suit's *role* relative to trump.
* :mod:`contrai_analyzer.engine` — hypergeometric distribution math over that
  model.
* :mod:`contrai_analyzer.bidding` — the bidding truth table turning those
  probabilities into a suggested contract.

This package is deliberately independent of ``contrai_core``: ``SuitSlot``
answers a different question than core's ``Suit``, and merging the two would
cost the combinatorial math its suit symmetry.
"""

from contrai_analyzer.bidding.evaluator import BiddingEvaluator
from contrai_analyzer.engine.probability_engine import ProbabilityEngine
from contrai_analyzer.models.deck import Card, Rank, SuitSlot
from contrai_analyzer.models.hand import Hand

__all__ = [
    "BiddingEvaluator",
    "Card",
    "Hand",
    "ProbabilityEngine",
    "Rank",
    "SuitSlot",
]
