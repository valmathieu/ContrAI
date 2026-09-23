"""Human-input parsers for the Rich terminal UI.

Turn the raw strings a player types at the prompt into engine-shaped
values: a :class:`~contrai_core.bid.Bid` attached to the acting player,
or the selected :class:`~contrai_core.card.Card`. Both return ``None`` on
unrecognized input so the prompt loops can re-ask rather than crash.
Syntactic validation only — the auction and round rules own legality.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple, Optional, Sequence

from contrai_core import BasePlayer, Card
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
    SlamLevel,
)

from contrai_engine.view.theme import (
    DOUBLE_WORDS,
    PASS_WORDS,
    REDOUBLE_WORDS,
    SUIT_ALIASES,
    VALID_BID_VALUES,
)


def _parse_bid_input(raw: str, player: BasePlayer) -> Optional[Bid]:
    """Parse a human bid string into a :class:`Bid` for ``player``.

    Construction is always safe here: numeric values are checked against
    ``VALID_BID_VALUES`` and the Slam sentinels are always valid, so the
    resulting :class:`ContractBid` never trips ``__post_init__``. Legality
    within the auction is a separate concern the caller checks with
    :meth:`Auction.is_legal`.

    Accepted forms:
        pass / p               -> PassBid
        double / d             -> DoubleBid
        redouble / r           -> RedoubleBid
        "80 h" / "100 hearts" / "150nt"   -> ContractBid(value, Suit)
        "slam s" / "slams"                -> ContractBid(SlamLevel.SLAM, Suit)
        "solo slam h" / "soloslam h"      -> ContractBid(SlamLevel.SOLO_SLAM, Suit)
    """
    s = raw.strip().lower()
    if not s:
        return None
    if s in PASS_WORDS:
        return PassBid(player)
    if s in DOUBLE_WORDS:
        return DoubleBid(player)
    if s in REDOUBLE_WORDS:
        return RedoubleBid(player)

    # Try "<value><sep><suit>" with optional whitespace; also accept
    # the value and suit being glued together ("100h", "slams").
    parts = s.replace(",", " ").split()

    # Accept the two-word form "solo slam <suit>" by collapsing the
    # first two tokens into the canonical "soloslam" token.
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
        return ContractBid(player, SlamLevel.SLAM, suit)
    if raw_value == "soloslam":
        return ContractBid(player, SlamLevel.SOLO_SLAM, suit)
    try:
        value = int(raw_value)
    except ValueError:
        return None
    if value not in VALID_BID_VALUES:
        return None
    return ContractBid(player, value, suit)


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


class RoundPick(NamedTuple):
    """What the replay picker was asked for.

    Attributes:
        number: The record's number for the chosen round.
        grid: ``True`` to show the round as a trick grid, ``False`` to
            step through it.
    """

    number: int
    grid: bool = False


#: A picker answer: an optional ``g`` / ``grid`` and a round number. No
#: sign is accepted, so a negative answer needs no guard of its own.
_ROUND_PICK: Final = re.compile(r"(?:(g|grid)\s*)?(\d+)")


def _parse_round_pick(
    raw: str, steppable: Sequence[int]
) -> Optional[RoundPick]:
    """Parse the replay picker's answer. ``None`` on anything else.

    ``7`` steps round 7; ``g 7`` (or ``g7``, ``grid 7``) shows it as a
    trick grid. The number is checked against the rounds that can
    actually be replayed rather than against the record's full list, so a
    round the table shows but the driver refuses is rejected here instead
    of failing halfway through a replay.

    Args:
        raw: What the viewer typed.
        steppable: The record round numbers that can be replayed.

    Returns:
        The pick, or ``None`` if the answer named no replayable round.
    """

    match = _ROUND_PICK.fullmatch(raw.strip().lower())
    if match is None:
        return None
    number = int(match.group(2))
    if number not in steppable:
        return None
    return RoundPick(number, grid=match.group(1) is not None)


#: The replay step keys, each with the word that spells it out.
#: ``[Enter]`` maps to the next action, the same way it means "go on"
#: everywhere else in this interface.
_REPLAY_KEYS: Final[dict[str, str]] = {
    "": "n",
    "n": "n",
    "next": "n",
    "t": "t",
    "trick": "t",
    "r": "r",
    "round": "r",
    "a": "a",
    "auction": "a",
    "p": "p",
    "back": "p",
    "q": "q",
    "quit": "q",
}


def _parse_replay_key(
    raw: str, *, can_go_back: bool, can_skip_auction: bool = False
) -> Optional[str]:
    """Parse a replay step key. ``None`` on anything the screen cannot do.

    ``p`` is refused at a round's first stop, and ``a`` once the auction
    is over, rather than silently doing nothing: a key that looks like it
    worked is worse than one that says it did not.

    Args:
        raw: What the viewer typed.
        can_go_back: Whether a previous stop exists to return to.
        can_skip_auction: Whether the round is still bidding.

    Returns:
        One of ``"n"``, ``"t"``, ``"r"``, ``"a"``, ``"p"``, ``"q"``, or
        ``None``.
    """

    key = _REPLAY_KEYS.get(raw.strip().lower())
    if key is None:
        return None
    if key == "p" and not can_go_back:
        return None
    if key == "a" and not can_skip_auction:
        return None
    return key
