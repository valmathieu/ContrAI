"""Stateless text/glyph/label builders for the Rich terminal UI.

Pure ``(data) -> str | Text`` formatters with no game state and no I/O:
seat/team labels and colors, suit glyphs and colors, the compact card
label, and the shared contract / trump / legacy-bid labels. The screen
modules and ``RichView`` compose these into panels.
"""

from __future__ import annotations

from typing import Optional

from contrai_core import (
    BasePlayer,
    Card,
    Contract,
    ContractSuit,
    Position,
    Rank,
    Suit,
    TeamSide,
    TrumpVariant,
)
from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
)
from rich.text import Text

from contrai_engine.view.theme import (
    BLUE,
    DIM,
    FG,
    GOLD,
    ORANGE,
    POSITION_SHORT,
    RED,
    RED_DIM,
    TEAM_ABBR,
    TRUMP_GLYPH,
    TRUMP_LABEL,
)


def _position_short(position: Position) -> str:
    """Single-letter seat label: ``Position.NORTH`` -> ``"N"``."""
    return POSITION_SHORT[position]


def _position_color(position: Position) -> str:
    """Seat color by team: blue for N/S seats, orange for E/W."""
    return _team_color(position.team_side)


def _team_color(side: TeamSide) -> str:
    """Team color: blue for North-South, orange for East-West."""
    return BLUE if side is TeamSide.NS else ORANGE


def _team_abbr(side: TeamSide) -> str:
    """Scoreboard abbreviation: ``TeamSide.NS`` -> ``"N-S"``."""
    return TEAM_ABBR[side]


def _suit_glyph(suit: ContractSuit) -> str:
    """Contract-trump glyph: ``♠``/``♥``/``♦``/``♣``, or ``NT``/``AT``."""
    return TRUMP_GLYPH[suit]


# Short rank labels for the hand row and trick diamond. The engine's
# Rank.value strings spell "Jack"/"Queen"/"King"/"Ace" in full; the
# mockup uses single-letter abbreviations so 8 cards fit a 70-col row.
RANK_SHORT = {
    Rank.SEVEN: "7",
    Rank.EIGHT: "8",
    Rank.NINE: "9",
    Rank.TEN: "10",
    Rank.JACK: "J",
    Rank.QUEEN: "Q",
    Rank.KING: "K",
    Rank.ACE: "A",
}


def _rank_short(rank: Rank) -> str:
    """Short rank label from ``RANK_SHORT``: ``Rank.JACK`` -> ``"J"``."""
    return RANK_SHORT.get(rank, rank.value)


def _suit_color(suit: ContractSuit) -> str:
    """Suit color: red for hearts/diamonds, plain foreground otherwise."""
    return RED if suit in (Suit.HEARTS, Suit.DIAMONDS) else FG


def _suit_color_dim(suit: Suit) -> str:
    """Dimmed variant of :func:`_suit_color` for de-emphasized cards."""
    return RED_DIM if suit in (Suit.HEARTS, Suit.DIAMONDS) else DIM


def _format_card_compact(card: Card) -> Text:
    """Render a card as ``"K♠"`` style — bold, with suit color."""
    t = Text()
    t.append(_rank_short(card.rank), style="bold")
    t.append(_suit_glyph(card.suit), style=f"bold {_suit_color(card.suit)}")
    return t


def _seat_letter(player: Optional[BasePlayer]) -> Optional[Text]:
    """Single-letter seat label colored by the player's team, or ``None``.

    Used to name the players behind a contract: the taker whose bid set
    it, and the double / redouble caller. Each letter keeps the
    seat's team color (blue for N-S, orange for E-W).
    """
    if player is None or getattr(player, "position", None) is None:
        return None
    return Text(
        _position_short(player.position),
        style=f"bold {_position_color(player.position)}",
    )


def _format_contract_short(
    contract: Contract, *, verbose: bool = False, suit_glyph: bool = False
) -> Text:
    """Short label: ``"100 by E  ×2 by S"``.

    Names the players, not just the team: the contract-setter follows
    ``by`` as a single team-colored seat letter, and any double / redouble
    shows its multiplier with the caller's seat
    (``×2 by S`` / ``×4 by N``).

    Args:
        contract: The materialized contract to render.
        verbose: When ``True``, spell the double / redouble markers
            out as ``doubled`` / ``redoubled`` instead of the compact
            ``×2`` / ``×4`` glyphs. The recap panel uses this so the
            after-round summary reads in full prose; the in-game panel
            and event log keep the compact form.
        suit_glyph: When ``True``, slot the trump glyph right after the
            value (``"100 ♥ by E"``). The event-log 'Contract set' line
            uses this because it carries no other trump hint; the round
            panel and recap keep the default — they already show the
            trump on a dedicated line.
    """
    double_label = "redoubled" if verbose else "×4"
    single_label = "doubled" if verbose else "×2"
    t = Text()
    # SlamLevel.__str__ already yields "Slam" / "Solo Slam"; numerics
    # stringify to "80" … "180".
    value_str = str(contract.value)
    t.append(value_str, style="bold")
    if suit_glyph:
        t.append(" ", style=FG)
        t.append(
            _suit_glyph(contract.suit),
            style=f"bold {_suit_color(contract.suit)}",
        )
    t.append(" by ", style=DIM)
    taker = _seat_letter(getattr(contract, "player", None))
    if taker is not None:
        t.append_text(taker)
    else:
        # Defensive fallback: name the team if the player is missing.
        side = contract.team.players[0].position.team_side
        t.append(_team_abbr(side), style=f"bold {_team_color(side)}")
    # Double / redouble: multiplier plus the player who called it.
    if contract.redouble:
        caller = _seat_letter(getattr(contract, "redouble_player", None))
        t.append(f"  {double_label}", style=GOLD)
        if caller is not None:
            t.append(" by ", style=DIM)
            t.append_text(caller)
    elif contract.double:
        caller = _seat_letter(getattr(contract, "double_player", None))
        t.append(f"  {single_label}", style=GOLD)
        if caller is not None:
            t.append(" by ", style=DIM)
            t.append_text(caller)
    return t


def _format_trump_label(suit: Optional[ContractSuit]) -> Text:
    """``"♥ Hearts"`` with the glyph in suit color, ``"No Trump"`` alone.

    A trump naming no suit has no pip to show, so its two-letter tag would
    only repeat the label ("NT No Trump"); the spelled-out name stands on
    its own instead.

    Args:
        suit: The trump to render, or ``None`` for an em-dash.
    """
    if suit is None:
        return Text("—", style=DIM)
    if isinstance(suit, TrumpVariant):
        return Text(TRUMP_LABEL[suit], style="bold")
    t = Text()
    t.append(_suit_glyph(suit), style=_suit_color(suit))
    t.append(" ", style=FG)
    t.append(TRUMP_LABEL[suit], style="bold")
    return t


def _bid_label(bid: Bid) -> Text:
    """Compact bid label for the bidding history and diamond.

    Renders a :class:`~contrai_core.bid.Bid` to the short glyphs the
    auction panels use: ``Pass`` for a pass, ``×2`` / ``×4`` for a
    double / redouble, and ``"<value> <suit-glyph>"`` for a numeric
    or Slam-family contract.
    """
    if isinstance(bid, PassBid):
        return Text("Pass", style=DIM)
    if isinstance(bid, DoubleBid):
        return Text("×2", style=GOLD)
    if isinstance(bid, RedoubleBid):
        return Text("×4", style=GOLD)
    if isinstance(bid, ContractBid):
        t = Text()
        t.append(str(bid.value), style="bold")
        t.append(" ", style=FG)
        t.append(_suit_glyph(bid.suit), style=_suit_color(bid.suit))
        return t
    return Text(str(bid), style=DIM)
