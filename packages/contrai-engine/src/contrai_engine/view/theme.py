"""Design tokens and shared constants for the Rich terminal UI.

Holds the color palette mapped from the handoff README's color table,
plus the small lookup tables (target-score options, position/team
labels, bid keyword aliases, the valid contract-value set) that the
formatting, parsing, and screen modules all consume. Pure data — no
rendering or game logic lives here.
"""

from __future__ import annotations

from contrai_core import ContractSuit, Position, Suit, TeamSide, TrumpVariant
from contrai_core.bid import ContractBid

# ---------------------------------------------------------------------------
# Design tokens (mapped from the handoff README's color table)
# ---------------------------------------------------------------------------

FG = "rgb(212,212,212)"
DIM = "rgb(106,106,106)"
BORDER = "rgb(122,122,122)"
BORDER_DIM = "rgb(68,68,68)"
TITLE = "rgb(200,200,200)"
RED = "rgb(224,108,117)"
RED_DIM = "rgb(122,58,63)"
BLUE = "rgb(127,182,255)"
ORANGE = "rgb(255,180,130)"
GREEN_BG = "rgb(46,90,42)"
GREEN_FG = "rgb(207,234,192)"
GREEN_CHECK = "rgb(58,122,58)"
YELLOW = "rgb(229,192,123)"
GOLD = "rgb(240,181,74)"
GOLD_BG = "rgb(58,43,16)"
GOLD_FG = "rgb(255,213,122)"
HINT = "rgb(61,61,64)"
RULE = "rgb(42,42,42)"
DOT = "rgb(58,58,58)"

# Valid target scores shown on the landing radio.
TARGET_OPTIONS = [
    (500, "Quick game", "~10 min"),
    (1000, "Short game", "~20 min"),
    (1500, "Standard", "~30 min"),
    (2000, "Long game", "~45 min"),
    (3000, "Marathon", "~60 min"),
]
DEFAULT_TARGET = 1500

# Position label mapping: seat -> single-letter UI label.
POSITION_SHORT: dict[Position, str] = {
    Position.NORTH: "N",
    Position.EAST: "E",
    Position.SOUTH: "S",
    Position.WEST: "W",
}

# Team side -> the scoreboard abbreviation. This is presentation, not
# identity: TeamSide is what every score dictionary is keyed by, so
# rewording a label here can no longer break a lookup.
TEAM_ABBR: dict[TeamSide, str] = {
    TeamSide.NS: "N-S",
    TeamSide.EW: "E-W",
}

# Bid keyword aliases for parsing.
PASS_WORDS = {"pass", "p"}
DOUBLE_WORDS = {"double", "d"}
REDOUBLE_WORDS = {"redouble", "r"}
SUIT_ALIASES = {
    "s": Suit.SPADES, "spades": Suit.SPADES, "spade": Suit.SPADES, "♠": Suit.SPADES,
    "h": Suit.HEARTS, "hearts": Suit.HEARTS, "heart": Suit.HEARTS, "♥": Suit.HEARTS,
    "d": Suit.DIAMONDS, "diamonds": Suit.DIAMONDS, "diamond": Suit.DIAMONDS, "♦": Suit.DIAMONDS,
    "c": Suit.CLUBS, "clubs": Suit.CLUBS, "club": Suit.CLUBS, "♣": Suit.CLUBS,
    "nt": TrumpVariant.NO_TRUMP,
    "notrump": TrumpVariant.NO_TRUMP,
    "no-trump": TrumpVariant.NO_TRUMP,
    "at": TrumpVariant.ALL_TRUMP,
    "alltrump": TrumpVariant.ALL_TRUMP,
    "all-trump": TrumpVariant.ALL_TRUMP,
}

# Contract-trump glyphs. The four card suits get their Unicode pip; the
# two variants get a two-letter tag rather than falling through to the
# enum value ("NoTrump", 7 cells), which overflows the 11-cell bid column
# of the bidding history.
TRUMP_GLYPH: dict[ContractSuit, str] = {
    Suit.SPADES: "♠",
    Suit.HEARTS: "♥",
    Suit.DIAMONDS: "♦",
    Suit.CLUBS: "♣",
    TrumpVariant.NO_TRUMP: "NT",
    TrumpVariant.ALL_TRUMP: "AT",
}

# Spelled-out trump names for the contract panel. English-only, matching
# contree-domain.md §10 (*sans atout* -> No Trump, *tout atout* -> All
# Trump).
TRUMP_LABEL: dict[ContractSuit, str] = {
    Suit.SPADES: "Spades",
    Suit.HEARTS: "Hearts",
    Suit.DIAMONDS: "Diamonds",
    Suit.CLUBS: "Clubs",
    TrumpVariant.NO_TRUMP: "No Trump",
    TrumpVariant.ALL_TRUMP: "All Trump",
}
# Derived from ``ContractBid.VALID_VALUES`` so the human-input parser
# stays in lockstep with the auction's canonical value ladder. The
# all-tricks ``SlamLevel`` members are handled by a separate parsing
# branch above, so only the numeric subset is needed here.
VALID_BID_VALUES = {v for v in ContractBid.VALID_VALUES if isinstance(v, int)}
