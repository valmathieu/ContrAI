"""Human-input parsers for the Rich terminal UI.

Turn the raw strings a player types at the prompt into engine-shaped
values: a bid (``"Pass"`` / ``"Double"`` / ``"Redouble"`` / a
``(value, suit)`` tuple) or the selected :class:`~contrai_core.card.Card`.
Both return ``None`` on unrecognized input so the prompt loops can
re-ask rather than crash. Syntactic validation only — the auction and
round rules own legality.
"""

from __future__ import annotations

from typing import Optional

from contrai_core import Card, Suit
from contrai_core.bid import SlamLevel

from contrai_engine.view.theme import (
    DOUBLE_WORDS,
    PASS_WORDS,
    REDOUBLE_WORDS,
    SUIT_ALIASES,
    VALID_BID_VALUES,
)


def _parse_bid_input(raw: str) -> Optional[str | tuple[int | SlamLevel, Suit]]:
    """Parse a human bid string. Returns engine bid representation or None.

    Accepted forms:
        pass / p               -> 'Pass'
        double / d             -> 'Double'
        redouble / r           -> 'Redouble'
        "80 h" / "100 hearts" / "150nt"   -> (value, Suit)
        "slam s" / "slams"                -> (SlamLevel.SLAM, Suit)
        "solo slam h" / "soloslam h"      -> (SlamLevel.SOLO_SLAM, Suit)
    """
    s = raw.strip().lower()
    if not s:
        return None
    if s in PASS_WORDS:
        return "Pass"
    if s in DOUBLE_WORDS:
        return "Double"
    if s in REDOUBLE_WORDS:
        return "Redouble"

    # Try "<value><sep><suit>" with optional whitespace; also accept
    # the value and suit being glued together ("100h", "slams").
    parts = s.replace(",", " ").split()

    # Accept the two-word form "solo slam <suit>" by collapsing the
    # first two tokens into the canonical "soloslam" wire form.
    if len(parts) == 3 and parts[0] == "solo" and parts[1] == "slam":
        parts = ["soloslam", parts[2]]

    if len(parts) == 1:
        token = parts[0]
        # Split alpha tail (suit) from leading value.
        i = 0
        while i < len(token) and (token[i].isdigit() or token[i] == "-"):
            i += 1
        if i == 0:
            # All-alpha: maybe "soloslams" -> soloslam + s, or "slams" -> slam + s
            if token.startswith("soloslam") and len(token) > len("soloslam"):
                parts = ["soloslam", token[len("soloslam"):]]
            elif token.startswith("slam") and len(token) > len("slam"):
                parts = ["slam", token[len("slam"):]]
            else:
                return None
        else:
            parts = [token[:i], token[i:]]

    if len(parts) != 2:
        return None
    raw_value, raw_suit = parts
    suit = SUIT_ALIASES.get(raw_suit)
    if suit is None:
        return None

    if raw_value == "slam":
        return (SlamLevel.SLAM, suit)
    if raw_value == "soloslam":
        return (SlamLevel.SOLO_SLAM, suit)
    try:
        value = int(raw_value)
    except ValueError:
        return None
    if value not in VALID_BID_VALUES:
        return None
    return (value, suit)


def _parse_card_input(
    raw: str, sorted_hand: list[Card], playable: list[Card]
) -> Optional[Card]:
    """Parse a card-selection number; validate it's in playable. None on error."""
    s = raw.strip()
    if not s.isdigit():
        return None
    idx = int(s) - 1
    if idx < 0 or idx >= len(sorted_hand):
        return None
    card = sorted_hand[idx]
    if card not in playable:
        return None
    return card
