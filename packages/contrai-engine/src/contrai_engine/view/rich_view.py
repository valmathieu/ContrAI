"""Rich-based terminal UI for the Contrée game.

Implements the five-screen design from
``ContrAI CLI/design_handoff_contrai_tui/`` (landing, bidding,
mid-trick, trick-won, game-over). Plugs into the engine through the
existing view hook points:

- ``Round.manage_bidding(view)`` calls ``view.request_bid_action(...)``
- ``Round.play_trick(view)`` calls ``view.request_card_action(...)``
- After each trick, ``Round.play_trick`` calls
  ``view.on_trick_complete(...)`` (added for this view).

The view owns all rendering and human input. Per-round summaries used
by the end-game scoreboard are tracked here, not in ``Game``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from contrai_core.bid import Bid, ContractBid, DoubleBid, PassBid, RedoubleBid

from contrai_core import (
    Auction,
    BasePlayer,
    Card,
    Contract,
    Rank,
    Suit,
    Trick,
)
from contrai_engine.model.player import wire_to_bid
from rich.align import Align
from rich.box import DOUBLE, ROUNDED, SQUARE
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from pyfiglet import Figlet
    _HAS_PYFIGLET = True
except ImportError:
    _HAS_PYFIGLET = False

if TYPE_CHECKING:
    from contrai_engine.model.game import Game
    from contrai_engine.model.round import Round


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

# Position label mapping: full engine name -> single-letter UI label.
POSITION_SHORT = {"North": "N", "East": "E", "South": "S", "West": "W"}

# Team -> abbreviation used in scoreboards.
TEAM_ABBR = {"North-South": "N-S", "East-West": "E-W"}

# Bid keyword aliases for parsing.
PASS_WORDS = {"pass", "p", "passe"}
DOUBLE_WORDS = {"double", "d"}
REDOUBLE_WORDS = {"redouble", "r"}
SUIT_ALIASES = {
    "s": Suit.SPADES, "spades": Suit.SPADES, "spade": Suit.SPADES, "♠": Suit.SPADES,
    "h": Suit.HEARTS, "hearts": Suit.HEARTS, "heart": Suit.HEARTS, "♥": Suit.HEARTS,
    "d": Suit.DIAMONDS, "diamonds": Suit.DIAMONDS, "diamond": Suit.DIAMONDS, "♦": Suit.DIAMONDS,
    "c": Suit.CLUBS, "clubs": Suit.CLUBS, "club": Suit.CLUBS, "♣": Suit.CLUBS,
    "nt": Suit.NO_TRUMP, "notrump": Suit.NO_TRUMP, "no-trump": Suit.NO_TRUMP,
    "sa": Suit.NO_TRUMP,  # French "Sans Atout"
}
VALID_BID_VALUES = {80, 90, 100, 110, 120, 130, 140, 150, 160}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _position_short(position: str) -> str:
    return POSITION_SHORT.get(position, position[:1])


def _position_color(position: str) -> str:
    return BLUE if position in ("North", "South") else ORANGE


def _team_color(team_name: str) -> str:
    return BLUE if team_name == "North-South" else ORANGE


def _team_abbr(team_name: str) -> str:
    return TEAM_ABBR.get(team_name, team_name)


def _suit_glyph(suit: Suit) -> str:
    return Card.SUIT_SYMBOLS.get(suit, suit.value)


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
    return RANK_SHORT.get(rank, rank.value)


def _suit_color(suit: Suit) -> str:
    return RED if suit in (Suit.HEARTS, Suit.DIAMONDS) else FG


def _suit_color_dim(suit: Suit) -> str:
    return RED_DIM if suit in (Suit.HEARTS, Suit.DIAMONDS) else DIM


def _format_card_compact(card: Card) -> Text:
    """Render a card as ``"K♠"`` style — bold, with suit color."""
    t = Text()
    t.append(_rank_short(card.rank), style="bold")
    t.append(_suit_glyph(card.suit), style=f"bold {_suit_color(card.suit)}")
    return t


def _sort_hand_for_display(cards: list[Card], trump_suit: Optional[Suit]) -> list[Card]:
    """Sort cards trump-first then by suit; within each suit by rank.

    Mockup convention: trump cards on the far left (in trump order),
    then non-trump suits in spades/hearts/diamonds/clubs preference,
    skipping suits with no cards. Within a suit, highest rank first.
    """
    suit_order = [Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS]
    if trump_suit and trump_suit in suit_order:
        suit_order.remove(trump_suit)
        suit_order.insert(0, trump_suit)

    sorted_cards: list[Card] = []
    for suit in suit_order:
        in_suit = [c for c in cards if c.suit == suit]
        in_suit.sort(key=lambda c: c.get_order(trump_suit), reverse=True)
        sorted_cards.extend(in_suit)
    return sorted_cards


def _current_winner(
    plays: list[tuple[BasePlayer, Card]], trump_suit: Optional[Suit]
) -> Optional[BasePlayer]:
    """Return the player currently winning the (possibly incomplete) trick.

    Thin wrapper around :meth:`contrai_core.trick.Trick.get_current_winner`
    that accepts a raw ``plays`` list (the shape ``_render_diamond`` already
    uses) instead of forcing a Trick allocation at every render.
    """
    if not plays:
        return None
    # Synthesize a minimal Trick for the delegate. Cheap: no game logic
    # depends on the wrapper instance — only the plays list is read.
    proxy = Trick()
    for p, c in plays:
        proxy.plays.append((p, c))
    return proxy.get_current_winner(trump_suit)


def _explain_constraint(
    player: BasePlayer,
    trick: Trick,
    playable: list[Card],
    trump_suit: Optional[Suit],
) -> Text:
    """Build the hint line under the hand explaining *why* this is playable."""
    plays = trick.get_plays() if trick else []
    if not plays:
        return Text("your lead — anything goes", style=GREEN_FG)

    led_suit = plays[0][1].suit
    has_led = any(c.suit == led_suit for c in player.hand)

    hint = Text("↑ playable ", style=GREEN_FG)
    if has_led:
        hint.append("(must follow ", style=GREEN_FG)
        hint.append(_suit_glyph(led_suit), style=_suit_color(led_suit))
        hint.append(")", style=GREEN_FG)
        return hint

    # No card of led suit. See if we're forced to trump.
    if trump_suit and all(c.suit == trump_suit for c in playable):
        # Identify the partner / opponent that led, for the message.
        leader = plays[0][0]
        leader_label = _position_short(leader.position)
        hint.append("(must trump — ", style=GREEN_FG)
        hint.append(f"{leader_label} led ", style=GREEN_FG)
        hint.append(_format_card_compact(plays[0][1]))
        hint.append(")", style=GREEN_FG)
        return hint

    hint.append("(free discard)", style=GREEN_FG)
    return hint


def _format_contract_short(contract: Contract) -> Text:
    """Short label: ``"100 by E-W"`` with team in team color."""
    t = Text()
    if contract.value == "Slam":
        value_str = "Slam"
    elif contract.value == "SoloSlam":
        value_str = "Solo Slam"
    else:
        value_str = str(contract.value)
    t.append(value_str, style="bold")
    t.append(" by ", style=DIM)
    team_abbr = _team_abbr(contract.team.name)
    t.append(team_abbr, style=f"bold {_team_color(contract.team.name)}")
    if contract.redouble:
        t.append(" (×4)", style=GOLD)
    elif contract.double:
        t.append(" (×2)", style=GOLD)
    return t


def _format_trump_label(suit: Optional[Suit]) -> Text:
    """``"♥ Hearts ★"`` with red glyph and gold star."""
    if suit is None:
        return Text("—", style=DIM)
    t = Text()
    t.append(_suit_glyph(suit), style=_suit_color(suit))
    t.append(" ", style=FG)
    label = "No Trump" if suit == Suit.NO_TRUMP else suit.value
    t.append(label, style="bold")
    t.append(" ★", style=GOLD)
    return t


def _parse_bid_input(raw: str) -> Optional[str | tuple[int | str, Suit]]:
    """Parse a human bid string. Returns engine bid representation or None.

    Accepted forms:
        pass / p / passe       -> 'Pass'
        double / d             -> 'Double'
        redouble / r           -> 'Redouble'
        "80 h" / "100 hearts" / "150nt"   -> (value, Suit)
        "slam s" / "slams"                -> ("Slam", Suit)
        "solo slam h" / "soloslam h"      -> ("SoloSlam", Suit)
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
        return ("Slam", suit)
    if raw_value == "soloslam":
        return ("SoloSlam", suit)
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


def _belote_by_position(round_) -> dict[str, str]:
    """Project ``round_.belote_state`` (player → kind) onto positions.

    Returns an empty dict when no round is active, the round has no
    belote_state, or none has been triggered yet. Used to render the
    persistent ★ Belote/Rebelote badge in the trick diamond.
    """
    if round_ is None:
        return {}
    state = getattr(round_, "belote_state", None) or {}
    return {player.position: kind for player, kind in state.items()}


def _resolve_delay(env_var: str, default: float) -> float:
    """Read a float pacing value from the environment with a default.

    Pacing for AI actions is tunable so the user can dial the game
    speed without code edits. Garbage values fall back to ``default``
    rather than raising — this is UI pacing, not a correctness path.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


def _bid_to_legacy(bid: Bid):
    """Convert a Bid object to the legacy tuple/string history shape."""
    if isinstance(bid, PassBid):
        return "Pass"
    if isinstance(bid, DoubleBid):
        return "Double"
    if isinstance(bid, RedoubleBid):
        return "Redouble"
    if isinstance(bid, ContractBid):
        return (bid.value, bid.suit)
    return "Pass"


def _redouble_available_to(history: list, player: BasePlayer) -> bool:
    """True if *player* may currently redouble — narrows the prompt hint.

    Mirrors :class:`contrai_core.bid.RedoubleBid.is_valid_after` for the
    legacy-format history this view receives, without re-deriving
    Contract objects. The rule: the most recent non-pass bid is a
    Double, no passes have occurred since it, and the previous
    ContractBid was made by *player*'s team.
    """
    if not history or player is None or getattr(player, "team", None) is None:
        return False

    # Walk backwards looking for the most recent Double; abort if we
    # see a Pass first or a Redouble has already fired.
    saw_double = False
    contract_team = None
    for bid_player, bid in reversed(history):
        if bid == "Pass":
            if not saw_double:
                # Pass before any Double — Double slot is closed.
                return False
            # Pass after the Double we already found — also closes the window.
            return False
        if bid == "Redouble":
            return False
        if bid == "Double":
            saw_double = True
            continue
        if isinstance(bid, tuple):
            # That's the ContractBid the Double refers to.
            contract_team = getattr(bid_player, "team", None)
            break

    if not saw_double or contract_team is None:
        return False
    return contract_team == player.team


def _bid_legacy_label(bid: str | tuple) -> Text:
    """Legacy bid label for the bidding-history line."""
    if bid == "Pass":
        return Text("Pass", style=DIM)
    if bid == "Double":
        return Text("×2", style=GOLD)
    if bid == "Redouble":
        return Text("×4", style=GOLD)
    if isinstance(bid, tuple):
        value, suit = bid
        t = Text()
        t.append(str(value), style="bold")
        t.append(" ", style=FG)
        t.append(_suit_glyph(suit), style=_suit_color(suit))
        return t
    return Text(str(bid), style=DIM)


# ---------------------------------------------------------------------------
# Round summary (UI-side history)
# ---------------------------------------------------------------------------


@dataclass
class RoundSummary:
    """One row of the end-game round-by-round table."""

    round_number: int
    contract: Optional[Contract]
    contract_team_name: Optional[str]
    contract_made: bool
    ns_pts: int
    ew_pts: int
    running_ns: int
    running_ew: int


# ---------------------------------------------------------------------------
# RichView
# ---------------------------------------------------------------------------


class RichView:
    """Rich-based terminal UI.

    Stateful: holds the live ``console``, the per-round history used by
    the end-game scoreboard, the previous trick (for the "Last trick"
    panel), and a reference to the active ``Game`` so render helpers
    can reach team scores without each call passing them.
    """

    LOG_MAX = 5

    def __init__(self) -> None:
        self.console: Console = Console()
        self.target_score: int = DEFAULT_TARGET
        self.history: list[RoundSummary] = []
        self.last_completed_trick: Optional[tuple[Trick, BasePlayer]] = None
        self.game: Optional["Game"] = None
        # Rolling narrative log shown below the hand. Captures the last
        # ``LOG_MAX`` events (deal, bids, plays, trick winners, redeal,
        # belote announcements). Survives across rounds so the end of
        # round N and the start of round N+1 share continuity.
        self.event_log: list[Text] = []

    # ------------------------------------------------------------------
    # Lifecycle wiring (called by the CLI)
    # ------------------------------------------------------------------

    def attach(self, game: "Game", target_score: int) -> None:
        """Bind a new game session. Resets per-game state."""
        self.game = game
        self.target_score = target_score
        self.history = []
        self.last_completed_trick = None
        self.event_log = []

    def reset_for_rematch(self) -> None:
        """Drop per-game state but keep the console and target."""
        self.game = None
        self.history = []
        self.last_completed_trick = None
        self.event_log = []

    # ------------------------------------------------------------------
    # Engine hooks
    # ------------------------------------------------------------------

    def request_bid_action(
        self, player: BasePlayer, auction: Auction
    ) -> Bid:
        """Prompt the human for a bid. Loops until input parses.

        Args:
            player: The human player whose turn it is.
            auction: The current auction state — projected to the
                legacy ``(player, wire_bid)`` shape internally for the
                renderer, which still consumes that format.

        Returns:
            The parsed :class:`Bid`. Round validates legality via
            :meth:`Auction.apply` and raises if the bid is illegal.
        """
        legacy_bids = [
            (bid.player, _bid_to_legacy(bid)) for bid in auction.bids
        ]
        while True:
            self._render_in_game(
                phase="bidding",
                current_player=player,
                bidding_history=legacy_bids,
                prompt_question=self._bidding_prompt_text(legacy_bids, player),
                mandatory=False,
            )
            raw = self.console.input(
                Text("> ", style=f"bold {GREEN_FG}").markup
            )
            parsed = _parse_bid_input(raw)
            if parsed is None:
                self.console.print(
                    Text(
                        "  ✗ Unrecognized bid. Try '80 h', 'pass', "
                        "'double', 'redouble'.",
                        style=RED,
                    )
                )
                continue
            return wire_to_bid(player, parsed)

    def request_card_action(
        self,
        player: BasePlayer,
        trick: Trick,
        contract: Contract,
        playable_cards: list[Card],
    ) -> Card:
        """Prompt the human for a card. Loops until input parses."""
        trump_suit = contract.suit if contract else None
        while True:
            sorted_hand = _sort_hand_for_display(list(player.hand), trump_suit)
            self._render_in_game(
                phase="playing",
                current_player=player,
                current_trick=trick,
                playable_cards=playable_cards,
                prompt_question=self._card_prompt_text(
                    playable_cards, len(sorted_hand)
                ),
                mandatory=True,
            )
            raw = self.console.input(
                Text("> ", style=f"bold {YELLOW}").markup
            )
            card = _parse_card_input(raw, sorted_hand, playable_cards)
            if card is None:
                self.console.print(
                    Text(
                        f"  ✗ Pick a number between 1 and {len(sorted_hand)} "
                        "matching a green-highlighted card.",
                        style=RED,
                    )
                )
                continue
            return card

    def on_trick_complete(
        self, trick: Trick, winner: BasePlayer, round_: "Round"
    ) -> None:
        """Record the winner in the log, render the trick-won state, wait for Enter."""
        trump = round_.contract.suit if round_ and round_.contract else None
        trick_points = sum(card.get_points(trump) for _, card in trick.get_plays())
        self._log(self._format_trick_won_log(winner, trick_points))
        # State 3: full trick shown, winner highlighted, Press Enter.
        self._render_in_game(
            phase="trick_won",
            current_trick=trick,
            trick_winner=winner,
            prompt_question=self._trick_won_prompt_text(winner),
            mandatory=False,
        )
        try:
            self.console.input(Text("> ", style=f"bold {GOLD}").markup)
        except (EOFError, KeyboardInterrupt):
            pass
        # Rotate: this is now the "last trick" for the next panel.
        self.last_completed_trick = (trick, winner)

    def on_round_dealt(self, round_: "Round") -> None:
        """Engine hook: cards have just been dealt for a new round."""
        dealer = (
            _position_short(round_.dealer.position)
            if round_ and round_.dealer
            else "—"
        )
        line = Text()
        line.append(f"Round #{round_.round_number}: ", style=f"bold {YELLOW}")
        line.append(f"{dealer} deals.", style=FG)
        self._log(line)

    def on_all_pass_redeal(self, round_: "Round") -> None:
        """Engine hook: every bid was a pass, the deal will be repeated."""
        line = Text("All passed — redealing.", style=f"bold {YELLOW}")
        self._log(line)

    def on_contract_established(self, round_: "Round") -> None:
        """Engine hook: bidding ended on a contract — bookmark it in the log."""
        contract = getattr(round_, "contract", None)
        if contract is None:
            return
        line = Text()
        line.append("Contract set: ", style=f"bold {GOLD}")
        line.append_text(_format_contract_short(contract))
        line.append(".", style=DIM)
        self._log(line)

    def on_bid_made(
        self, player: BasePlayer, bid: Bid, history: list
    ) -> None:
        """Record the bid in the event log; render+pause for AI players.

        Humans already drove the render through ``request_bid_action``;
        the engine calls this hook after their input has been recorded,
        so we skip the redundant frame for them. AI bids otherwise pass
        without a frame — this hook gives the user time to read the
        bidding history.
        """
        legacy_bid = _bid_to_legacy(bid)
        self._log(self._format_bid_log(player, legacy_bid))
        if getattr(player, "is_human", False):
            return
        legacy_history = [(b.player, _bid_to_legacy(b)) for b in history]
        self._render_in_game(
            phase="bidding",
            current_player=None,
            bidding_history=legacy_history,
            prompt_question=self._ai_bid_announcement(player, legacy_bid),
            mandatory=False,
        )
        time.sleep(_resolve_delay("CONTRAI_AI_BID_DELAY", default=1.4))

    def on_card_played(
        self, player: BasePlayer, card: Card, trick: Trick
    ) -> None:
        """Record the card in the event log; render+pause for AI players."""
        self._log(self._format_card_log(player, card))
        if getattr(player, "is_human", False):
            return
        self._render_in_game(
            phase="playing",
            current_player=None,
            current_trick=trick,
            prompt_question=self._ai_card_announcement(player, card),
            mandatory=False,
        )
        time.sleep(_resolve_delay("CONTRAI_AI_CARD_DELAY", default=0.9))

    def on_belote_announced(
        self, player: BasePlayer, kind: str, round_: "Round"
    ) -> None:
        """Belote / rebelote announcement: log + brief pause.

        The persistent ★ badge under the player's seat is rendered by
        ``_render_diamond`` from ``round_.belote_state``, so this hook
        only needs to record the moment and pace it visibly. The pause
        uses the card delay so it fits the per-play rhythm."""
        trump = round_.contract.suit if round_ and round_.contract else None
        line = Text()
        label = _position_short(player.position)
        color = _position_color(player.position)
        line.append(f"{label} ", style=f"bold {color}")
        line.append("announces ", style=FG)
        line.append(
            "Belote" if kind == "belote" else "Rebelote",
            style=f"bold {GOLD}",
        )
        if trump is not None:
            line.append(" (", style=DIM)
            line.append(_suit_glyph(trump), style=_suit_color(trump))
            line.append(").", style=DIM)
        else:
            line.append(".", style=DIM)
        self._log(line)
        time.sleep(_resolve_delay("CONTRAI_AI_CARD_DELAY", default=0.9))

    def show_round_recap(
        self, round_: "Round", running_scores: dict, *, is_final: bool = False
    ) -> None:
        """Full-screen recap shown after each round; waits for Enter.

        Follows the trick-won UX pattern: clear, print the recap panel,
        block on input. Called from the CLI loop after
        ``on_round_complete`` for *every* round — including the one
        that just clinched the game. When ``is_final`` is true the
        prompt switches to "see the final score" so the user knows the
        next screen is the game-over scoreboard, not another deal.
        """
        self.console.clear()
        self.console.print(self._panel_round_recap(round_, running_scores))
        if is_final:
            prompt_text = Text(
                "Press [Enter] to see the final score…", style=FG
            )
        else:
            prompt_text = Text(
                "Press [Enter] to deal the next round…", style=FG
            )
        self.console.print(self._panel_prompt(prompt_text, mandatory=False))
        try:
            self.console.input(Text("> ", style=f"bold {GOLD}").markup)
        except (EOFError, KeyboardInterrupt):
            pass

    def on_round_complete(self, round_: "Round", running_scores: dict) -> None:
        """Append a row to the end-game history."""
        contract = round_.contract
        ns_pts = round_.round_scores.get("North-South", 0)
        ew_pts = round_.round_scores.get("East-West", 0)
        running_ns = running_scores.get("North-South", 0)
        running_ew = running_scores.get("East-West", 0)
        if contract is None:
            made = False
            contract_team_name = None
        else:
            contract_team_name = contract.team.name
            made = round_.round_scores.get(contract_team_name, 0) > 0
        self.history.append(
            RoundSummary(
                round_number=round_.round_number,
                contract=contract,
                contract_team_name=contract_team_name,
                contract_made=made,
                ns_pts=ns_pts,
                ew_pts=ew_pts,
                running_ns=running_ns,
                running_ew=running_ew,
            )
        )
        # Reset last-trick for the next round.
        self.last_completed_trick = None

    # ------------------------------------------------------------------
    # CLI flow screens
    # ------------------------------------------------------------------

    def show_landing(self, selected_target: int = DEFAULT_TARGET) -> int:
        """Render the landing screen and return the chosen target score."""
        while True:
            self.console.clear()
            self.console.print(self._landing_title())
            self.console.print(self._landing_subtitle())
            self.console.print(self._landing_suit_ribbon())
            self.console.print()
            self.console.print(self._panel_game_setup(selected_target))
            self.console.print(self._panel_players())
            self.console.print(self._panel_prompt(
                self._landing_prompt_text(selected_target),
                mandatory=False,
            ))
            raw = self.console.input(
                Text("> ", style=f"bold {GREEN_FG}").markup
            ).strip()
            if not raw:
                return selected_target
            try:
                target = int(raw)
            except ValueError:
                self.console.print(
                    Text(
                        f"  ✗ Pick one of "
                        f"{', '.join(str(v) for v, _, _ in TARGET_OPTIONS)}.",
                        style=RED,
                    )
                )
                self.console.input(Text("  Press Enter…", style=DIM).markup)
                continue
            if target not in {v for v, _, _ in TARGET_OPTIONS}:
                self.console.print(
                    Text(
                        f"  ✗ {target} is not on the list. Pick one of "
                        f"{', '.join(str(v) for v, _, _ in TARGET_OPTIONS)}.",
                        style=RED,
                    )
                )
                self.console.input(Text("  Press Enter…", style=DIM).markup)
                continue
            return target

    def show_end_game(self, status: dict) -> str:
        """Render the end-game scoreboard and return 'n'/'r'/'q'."""
        while True:
            self.console.clear()
            self.console.print(self._panel_game_over_banner(status))
            self.console.print(self._panel_round_summary())
            self.console.print(self._panel_prompt(
                self._end_game_prompt_text(),
                mandatory=False,
            ))
            raw = self.console.input(
                Text("> ", style=f"bold {GREEN_FG}").markup
            ).strip().lower()
            if raw in ("n", "new"):
                return "n"
            if raw in ("r", "rematch"):
                return "r"
            if raw in ("q", "quit", "exit"):
                return "q"
            self.console.print(
                Text("  ✗ Pick [n] new game, [r] rematch, or [q] quit.",
                     style=RED)
            )
            self.console.input(Text("  Press Enter…", style=DIM).markup)

    # ------------------------------------------------------------------
    # Top-level in-game render
    # ------------------------------------------------------------------

    def _render_in_game(
        self,
        *,
        phase: str,
        current_player: Optional[BasePlayer] = None,
        current_trick: Optional[Trick] = None,
        playable_cards: Optional[list[Card]] = None,
        bidding_history: Optional[list] = None,
        trick_winner: Optional[BasePlayer] = None,
        prompt_question: Text = Text(""),
        mandatory: bool = False,
    ) -> None:
        """Clear the screen and print all in-game panels stacked."""
        self.console.clear()
        round_ = self.game.current_round if self.game else None
        # Top row: game score + round info
        top_left = self._panel_game_score()
        top_right = self._panel_round(round_, phase)
        self.console.print(_two_column(top_left, top_right, left_width=24))
        # Middle row: last trick + current trick
        mid_left = self._panel_last_trick(round_)
        mid_right = self._panel_current_trick(
            round_, current_trick, phase, current_player, trick_winner
        )
        self.console.print(_two_column(mid_left, mid_right, left_width=24))
        # Hand panel — always rendered when a human is seated, so the
        # slot stays put across AI bid frames, AI play frames, and the
        # trick-won pause. ``interactive`` is true only when the human
        # is the actively-acting player; otherwise the row is shown in
        # neutral styling (no green playable pills, no constraint hint).
        human = self._find_human_player()
        if human is not None:
            is_human_turn = (
                current_player is not None and current_player is human
            )
            hand_panel = self._panel_hand(
                human, current_trick, playable_cards, phase, round_,
                interactive=is_human_turn,
            )
        else:
            hand_panel = None
        # Bidding history for state 1, if any non-pass bids
        if phase == "bidding" and bidding_history:
            history_panel = self._panel_bidding_history(bidding_history)
            self.console.print(history_panel)
        if hand_panel is not None:
            self.console.print(hand_panel)
        # Event log: a rolling narrative of the last few engine events.
        self.console.print(self._panel_event_log())
        self.console.print(self._panel_prompt(prompt_question, mandatory))

    # ------------------------------------------------------------------
    # Landing screen pieces
    # ------------------------------------------------------------------

    def _landing_title(self) -> Text:
        """Centered block-ASCII CONTRAI title."""
        if _HAS_PYFIGLET:
            ascii_art = Figlet(font="ansi_shadow", width=70).renderText("CONTRAI")
        else:
            ascii_art = "CONTRAI"
        t = Text()
        for line in ascii_art.splitlines():
            t.append(line.center(70), style=f"bold {YELLOW}")
            t.append("\n")
        return t

    def _landing_subtitle(self) -> Text:
        return Text("Belote · Contrée · CLI edition".center(70), style=DIM)

    def _landing_suit_ribbon(self) -> Text:
        ribbon = Text()
        glyphs = [(Suit.SPADES, FG), (Suit.HEARTS, RED),
                  (Suit.DIAMONDS, RED), (Suit.CLUBS, FG)]
        # Build "  ♠   ♥   ♦   ♣  " then center it.
        segments = []
        for suit, color in glyphs:
            segments.append((suit, color))
        # Render with 3 spaces between glyphs.
        inner = Text()
        for i, (suit, color) in enumerate(segments):
            if i > 0:
                inner.append("   ")
            inner.append(_suit_glyph(suit), style=f"bold {color}")
        # Centered within 70 cols.
        total = inner.cell_len
        pad = max(0, (70 - total) // 2)
        ribbon.append(" " * pad)
        ribbon.append_text(inner)
        return ribbon

    def _panel_game_setup(self, selected: int) -> Panel:
        """Five radio rows for target score, highlight the selected one."""
        rows = Text()
        rows.append("Target score", style=f"bold {FG}")
        rows.append(" ", style=FG)
        rows.append(
            "(first team to reach the target wins the game)\n\n",
            style=DIM,
        )
        for value, label, estimate in TARGET_OPTIONS:
            is_sel = value == selected
            line = Text()
            if is_sel:
                radio = "(●)"
                line.append(f" {radio} ", style=f"bold {GOLD_FG} on {GOLD_BG}")
                line.append(f"{value:<4}  ", style=f"bold {GOLD_FG} on {GOLD_BG}")
                line.append(f"{label:<10}", style=f"{GOLD_FG} on {GOLD_BG}")
                line.append(f"  ·  {estimate}", style=f"{GOLD_FG} on {GOLD_BG}")
                if value == DEFAULT_TARGET:
                    line.append("   ← default", style=f"bold {GOLD} on {GOLD_BG}")
                # Pad to fill the panel width with the gold background.
                used = line.cell_len
                line.append(" " * max(0, 60 - used), style=f"on {GOLD_BG}")
            else:
                line.append(" ( ) ", style=DIM)
                line.append(f"{value:<4}  ", style=f"bold {FG}")
                line.append(f"{label:<10}", style=FG)
                line.append(f"  ·  {estimate}", style=DIM)
            rows.append_text(line)
            rows.append("\n")
        return Panel(
            rows,
            title=Text("Game setup", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=70,
        )

    def _panel_players(self) -> Panel:
        """Players block. Hardcoded for v1 — South=human, others=AI medium.

        TODO: replace with a configurable seat picker when we expose
        difficulty / player config on the landing screen.
        """
        seats = [
            ("N", "North", "AI · medium", BLUE, False),
            ("E", "East", "AI · medium", ORANGE, False),
            ("S", "You", "human", GREEN_FG, True),
            ("W", "West", "AI · medium", ORANGE, False),
        ]
        # Two columns of two: render as a 2-row, 2-col Table.
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(ratio=1)
        table.add_column(ratio=1)
        rows = []
        for label, name, role, color, is_human in seats:
            cell = Text()
            cell.append(label, style=f"bold {color}")
            cell.append(" ", style=FG)
            if is_human:
                cell.append(name, style=f"bold {color}")
            else:
                cell.append(name, style=FG)
            cell.append(f" ({role})", style=DIM)
            rows.append(cell)
        table.add_row(rows[0], rows[1])  # N, E
        table.add_row(rows[2], rows[3])  # S, W
        return Panel(
            table,
            title=Text("Players", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=70,
        )

    def _landing_prompt_text(self, selected: int) -> Text:
        t = Text()
        t.append(
            "Target score? [500 / 1000 / 1500 / 2000 / 3000] (default ",
            style=FG,
        )
        t.append(str(selected), style=f"bold {GOLD}")
        t.append(")", style=FG)
        return t

    # ------------------------------------------------------------------
    # In-game panels
    # ------------------------------------------------------------------

    def _panel_game_score(self) -> Panel:
        scores = self.game.scores if self.game else {"North-South": 0, "East-West": 0}
        body = Text()
        ns = scores.get("North-South", 0)
        ew = scores.get("East-West", 0)
        body.append(f"{'N-S':<8}", style=f"bold {BLUE}")
        body.append(f"{ns:>10}\n", style=FG)
        body.append(f"{'E-W':<8}", style=f"bold {ORANGE}")
        body.append(f"{ew:>10}\n", style=FG)
        body.append("·" * 18, style=DOT)
        body.append("\n")
        body.append(f"{'Target':<8}", style=DIM)
        body.append(f"{self.target_score:>10}", style=f"bold {YELLOW}")
        return Panel(
            body,
            title=Text("Game score", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=22,
            height=6,
        )

    def _panel_round(
        self, round_: Optional["Round"], phase: str
    ) -> Panel:
        body = Text()
        contract = round_.contract if round_ else None
        trump_active = contract is not None
        # Contract line
        body.append("Contract: ", style=DIM)
        if contract is None:
            body.append("—\n", style=FG)
        else:
            body.append_text(_format_contract_short(contract))
            body.append("\n")
        # Trump line
        body.append("Trump:    ", style=DIM)
        body.append_text(_format_trump_label(contract.suit if contract else None))
        body.append("\n")
        # Phase / trick
        if phase == "bidding":
            body.append("Phase:    ", style=DIM)
            body.append("Bidding in progress\n", style=f"bold {YELLOW}")
            dealer_name = round_.dealer.position if round_ and round_.dealer else "—"
            body.append("Dealer:   ", style=DIM)
            body.append(dealer_name, style=FG)
        else:
            tricks_done = len(round_.tricks) if round_ else 0
            current_idx = tricks_done + (1 if phase == "playing" else 0)
            current_idx = min(current_idx, 8)
            body.append("Trick:    ", style=DIM)
            body.append(f"{current_idx} of 8\n", style=FG)
            # Round running points (cards collected by each team so far).
            ns_pts, ew_pts = self._round_running_points(round_)
            body.append("Round pts: ", style=DIM)
            body.append("N-S ", style=f"bold {BLUE}")
            body.append(str(ns_pts), style="bold")
            body.append("  ·  ", style=DIM)
            body.append("E-W ", style=f"bold {ORANGE}")
            body.append(str(ew_pts), style="bold")

        border_color = YELLOW if trump_active else BORDER
        title_color = YELLOW if trump_active else TITLE
        round_label = (
            f"Round #{round_.round_number}"
            if round_ is not None and getattr(round_, "round_number", None)
            else "Round"
        )
        title = Text(round_label, style=f"bold {title_color}")
        if trump_active:
            title.append(" ★", style=GOLD)
        return Panel(
            body,
            title=title,
            border_style=border_color,
            box=ROUNDED,
            width=46,
            height=6,
        )

    def _round_running_points(self, round_: Optional["Round"]) -> tuple[int, int]:
        if not round_ or not round_.contract:
            return 0, 0
        trump = round_.contract.suit
        ns, ew = 0, 0
        for team_name, tricks in round_.team_tricks.items():
            pts = 0
            for trick in tricks:
                for _, card in trick.get_plays():
                    pts += card.get_points(trump)
            if team_name == "North-South":
                ns = pts
            elif team_name == "East-West":
                ew = pts
        return ns, ew

    def _panel_last_trick(self, round_: Optional["Round"]) -> Panel:
        if not self.last_completed_trick:
            body = Text("(none)", style=DIM, justify="center")
            body = Align.center(body, vertical="middle")
            return Panel(
                body,
                title=Text("Last trick", style=DIM),
                border_style=BORDER_DIM,
                box=ROUNDED,
                width=22,
                height=8,
            )
        trick, winner = self.last_completed_trick
        trump = round_.contract.suit if round_ and round_.contract else None
        body = self._render_diamond(
            trick,
            trump,
            pending_position=None,
            winner_position=winner.position if winner else None,
            dimmed=True,
            width=18,
            belote_by_position=_belote_by_position(round_),
        )
        body.append("\n")
        body.append("Won: ", style=DIM)
        body.append(_position_short(winner.position), style=f"bold {GOLD}")
        # Last trick number is the just-completed trick — that's the
        # length of tricks (the freshly appended one we are echoing).
        last_idx = len(round_.tricks) if round_ else 0
        title = Text(
            f"Last trick (#{last_idx})" if last_idx else "Last trick",
            style=DIM,
        )
        return Panel(
            body,
            title=title,
            border_style=BORDER_DIM,
            box=ROUNDED,
            width=22,
            height=8,
        )

    def _panel_current_trick(
        self,
        round_: Optional["Round"],
        trick: Optional[Trick],
        phase: str,
        current_player: Optional[BasePlayer],
        trick_winner: Optional[BasePlayer],
    ) -> Panel:
        title_suffix = ""
        if round_ and phase in ("playing", "trick_won"):
            trick_idx = len(round_.tricks) + (0 if phase == "trick_won" else 1)
            trick_idx = min(max(1, trick_idx), 8)
            title_suffix = f" (#{trick_idx})"

        if phase == "bidding" or trick is None:
            body = Text("(bidding…)" if phase == "bidding" else "(none)",
                        style=DIM, justify="center")
            body = Align.center(body, vertical="middle")
            return Panel(
                body,
                title=Text(f"Current trick{title_suffix}", style=f"bold {TITLE}"),
                border_style=BORDER,
                box=ROUNDED,
                width=46,
                height=8,
            )

        trump = round_.contract.suit if round_ and round_.contract else None
        pending_position = (
            current_player.position
            if current_player is not None and phase == "playing"
            else None
        )
        winner_position = trick_winner.position if trick_winner else None
        body = self._render_diamond(
            trick,
            trump,
            pending_position=pending_position,
            winner_position=winner_position,
            dimmed=False,
            width=42,
            belote_by_position=_belote_by_position(round_),
        )
        body.append("\n")
        if phase == "trick_won" and trick_winner is not None:
            body.append("Won: ", style=DIM)
            body.append(_position_short(trick_winner.position),
                        style=f"bold {GOLD}")
        elif current_player is not None and current_player.is_human:
            body.append("→ Your turn", style=f"bold {YELLOW}")
        elif current_player is not None:
            body.append(f"→ {current_player.position}'s turn", style=DIM)
        return Panel(
            body,
            title=Text(f"Current trick{title_suffix}", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=46,
            height=8,
        )

    def _render_diamond(
        self,
        trick: Trick,
        trump: Optional[Suit],
        *,
        pending_position: Optional[str],
        winner_position: Optional[str],
        dimmed: bool,
        width: int,
        belote_by_position: Optional[dict[str, str]] = None,
    ) -> Text:
        """Render the 4-player diamond: N top, E right, S bottom, W left.

        ``belote_by_position`` maps a position string (``"North"`` etc.)
        to either ``"belote"`` or ``"rebelote"`` for seats that have
        announced. The badge persists for the rest of the round.
        """
        belote_by_position = belote_by_position or {}

        def _belote_badge(pos: str) -> Optional[Text]:
            # The seat badge always reads "★ Belote" once the holder
            # has played either the K or the Q of trump. The belote /
            # rebelote distinction is narrative-only and lives in the
            # event log; under the seat we just signal "this player
            # has the K+Q pair".
            if belote_by_position.get(pos) is None:
                return None
            t = Text()
            t.append("★ ", style=f"bold {GOLD}")
            t.append("Belote", style=f"bold {GOLD}")
            return t

        plays = trick.get_plays() if trick else []
        plays_by_pos: dict[str, tuple[BasePlayer, Card]] = {}
        led_position: Optional[str] = None
        for i, (player, card) in enumerate(plays):
            plays_by_pos[player.position] = (player, card)
            if i == 0:
                led_position = player.position

        # Live winner (only if there's at least one play and no explicit winner).
        live_winner_pos = winner_position
        if live_winner_pos is None and plays:
            lw = _current_winner(plays, trump)
            if lw is not None:
                live_winner_pos = lw.position

        def slot(pos: str) -> Text:
            t = Text()
            label = _position_short(pos)
            pcolor = _position_color(pos)
            if pos == pending_position:
                t.append(f"{label} ", style=f"bold {pcolor}")
                t.append("?", style=f"bold {YELLOW}")
                return t
            play = plays_by_pos.get(pos)
            if play is None:
                t.append(f"{label} ", style=f"bold {DIM if dimmed else pcolor}")
                t.append("·", style=DIM)
                return t
            _, card = play
            rank_label = _rank_short(card.rank)
            is_winner = pos == live_winner_pos
            if is_winner and not dimmed:
                t.append(f"{label} ", style=f"bold {GOLD_FG} on {GOLD_BG}")
                t.append(rank_label, style=f"bold {GOLD_FG} on {GOLD_BG}")
                t.append(_suit_glyph(card.suit),
                         style=f"bold {GOLD_FG} on {GOLD_BG}")
                t.append(" ★", style=f"bold {GOLD} on {GOLD_BG}")
            elif is_winner and dimmed:
                t.append(f"{label} ", style=f"bold {GOLD_FG}")
                t.append(rank_label, style=f"bold {GOLD_FG}")
                t.append(_suit_glyph(card.suit), style=f"bold {GOLD_FG}")
                t.append(" ★", style=f"bold {GOLD}")
            else:
                fg_label = DIM if dimmed else pcolor
                rank_style = DIM if dimmed else "bold"
                suit_style = (_suit_color_dim(card.suit) if dimmed
                              else f"bold {_suit_color(card.suit)}")
                t.append(f"{label} ", style=f"bold {fg_label}")
                t.append(rank_label, style=rank_style)
                t.append(_suit_glyph(card.suit), style=suit_style)
            if pos == led_position and not dimmed:
                t.append(" (led)", style=DIM)
            return t

        # Build rows of fixed-width text. Belote badges (when any seat
        # has announced) are inserted as a centered line below the seat
        # that owns them.
        out = Text()
        # Row 1: blank
        out.append("\n")
        # Row 2: N centered
        n = slot("North")
        pad_left = max(0, (width - n.cell_len) // 2)
        out.append(" " * pad_left)
        out.append_text(n)
        out.append("\n")
        # N's belote badge (centered)
        n_badge = _belote_badge("North")
        if n_badge is not None:
            pad = max(0, (width - n_badge.cell_len) // 2)
            out.append(" " * pad)
            out.append_text(n_badge)
            out.append("\n")
        # Row 3: W left, E right
        w = slot("West")
        e = slot("East")
        used = w.cell_len + e.cell_len
        gap = max(2, width - used)
        out.append_text(w)
        out.append(" " * gap)
        out.append_text(e)
        out.append("\n")
        # W/E badges share a row (left-aligned for W, right-aligned for E).
        w_badge = _belote_badge("West")
        e_badge = _belote_badge("East")
        if w_badge is not None or e_badge is not None:
            wb_len = w_badge.cell_len if w_badge else 0
            eb_len = e_badge.cell_len if e_badge else 0
            badge_gap = max(2, width - wb_len - eb_len)
            if w_badge is not None:
                out.append_text(w_badge)
            else:
                out.append(" " * wb_len)
            out.append(" " * badge_gap)
            if e_badge is not None:
                out.append_text(e_badge)
            out.append("\n")
        # Row 4: S centered
        s = slot("South")
        pad_left = max(0, (width - s.cell_len) // 2)
        out.append(" " * pad_left)
        out.append_text(s)
        # S's belote badge (centered)
        s_badge = _belote_badge("South")
        if s_badge is not None:
            out.append("\n")
            pad = max(0, (width - s_badge.cell_len) // 2)
            out.append(" " * pad)
            out.append_text(s_badge)
        return out

    def _panel_hand(
        self,
        player: BasePlayer,
        trick: Optional[Trick],
        playable_cards: Optional[list[Card]],
        phase: str,
        round_: Optional["Round"],
        *,
        interactive: bool = True,
    ) -> Panel:
        """Render the human's hand row.

        ``interactive`` is true only when the human is the actively-
        acting player and the view is gathering their input. In every
        other in-game frame (AI bidding, AI playing, the trick-won
        pause) the panel still appears — keeping the slot stable in
        the layout — but cards are rendered with neutral styling: no
        green playable pills, no constraint hint, just the row plus a
        size readout.

        An empty hand (after the last trick of the round) still
        produces a panel; the row reads ``(no cards left)`` so the
        slot doesn't pop in and out at the trick-won frame for the
        eighth trick.
        """
        trump_suit = round_.contract.suit if round_ and round_.contract else None
        sorted_hand = _sort_hand_for_display(list(player.hand), trump_suit)

        cards_row = Text()
        if not sorted_hand:
            cards_row.append("(no cards left)", style=DIM)
        else:
            # In non-interactive frames we render every card with the
            # bidding-style "yellow numbers, bold rank+suit" treatment.
            # Passing a phase that isn't "playing" routes the cell
            # renderer down the neutral branch.
            cell_phase = phase if interactive else "neutral"
            playable_set = (
                set(id(c) for c in (playable_cards or sorted_hand))
                if interactive
                else set()
            )
            for idx, card in enumerate(sorted_hand, start=1):
                is_playable = id(card) in playable_set
                cell = self._render_card_cell(idx, card, is_playable, cell_phase)
                cards_row.append_text(cell)
                cards_row.append(" ")

        body = Text()
        body.append("\n")
        pad = max(0, (66 - cards_row.cell_len) // 2)
        body.append(" " * pad)
        body.append_text(cards_row)
        body.append("\n")

        if not sorted_hand:
            hint = Text("(hand empty)", style=DIM, justify="center")
        elif phase == "bidding":
            hint = Text(
                "(no card-play obligation yet — bidding phase)",
                style=DIM, justify="center",
            )
        elif phase == "playing" and interactive and trick is not None:
            hint = _explain_constraint(player, trick, playable_cards or [], trump_suit)
            hint.justify = "center"
        else:
            hint = Text(f"{len(sorted_hand)} cards remaining",
                        style=DIM, justify="center")
        body.append_text(hint)
        title = Text(f"Your hand ({player.position})", style=f"bold {TITLE}")
        return Panel(
            body,
            title=title,
            border_style=BORDER,
            box=ROUNDED,
            width=70,
            height=5,
        )

    def _find_human_player(self) -> Optional[BasePlayer]:
        """Return the human player at the table, or ``None`` if absent.

        Used by the in-game render to decide whether to draw the hand
        panel. We look up the human from the attached game rather than
        the per-frame ``current_player`` so the panel stays visible
        across frames where the engine has no human in focus (AI
        actions, trick-won pauses).
        """
        if self.game is None:
            return None
        for p in self.game.players:
            if getattr(p, "is_human", False):
                return p
        return None

    def _render_card_cell(
        self, idx: int, card: Card, is_playable: bool, phase: str
    ) -> Text:
        """Render a single card cell: ``[n] R♠`` with optional pill."""
        rank_label = _rank_short(card.rank)
        t = Text()
        if phase == "playing" and is_playable:
            t.append(f"[{idx}] ", style=f"bold white on {GREEN_BG}")
            t.append(rank_label, style=f"bold {GREEN_FG} on {GREEN_BG}")
            t.append(_suit_glyph(card.suit), style=f"bold {GREEN_FG} on {GREEN_BG}")
        elif phase == "playing" and not is_playable:
            t.append(f"[{idx}] ", style=DIM)
            t.append(rank_label, style=DIM)
            t.append(_suit_glyph(card.suit), style=_suit_color_dim(card.suit))
        else:
            t.append(f"[{idx}] ", style=f"bold {YELLOW}")
            t.append(rank_label, style="bold")
            t.append(_suit_glyph(card.suit), style=f"bold {_suit_color(card.suit)}")
        return t

    def _panel_bidding_history(self, bids: list) -> Panel:
        """One-line history of bids so far. Useful for the human deciding.

        Inserts a ` - ` separator between successive 4-bid blocks so the
        bidding rounds are visually distinct:
            S Pass  E Pass  N 80 ♥  W Pass - S 100 ♥  E Pass  N 130 ♥  W ×2
        """
        body = Text()
        if not bids:
            body.append("(no bids yet)", style=DIM)
        else:
            for i, (player, bid) in enumerate(bids):
                if i > 0:
                    # Two-space gap between bids; bump to a dashed
                    # separator at every 4-bid (bidding-round) boundary.
                    if i % 4 == 0:
                        body.append(" - ", style=DIM)
                    else:
                        body.append("  ", style=DIM)
                body.append(_position_short(player.position),
                            style=f"bold {_position_color(player.position)}")
                body.append(" ", style=FG)
                body.append_text(_bid_legacy_label(bid))
        return Panel(
            body,
            title=Text("Bidding so far", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=70,
        )

    def _panel_prompt(self, question: Text, mandatory: bool) -> Panel:
        body = Text()
        if mandatory:
            q = question.copy()
            q.stylize(f"bold {YELLOW}")
            body.append_text(q)
        else:
            body.append_text(question)
        body.append("\n")
        return Panel(
            body,
            title=Text("Prompt", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=70,
            height=4,
        )

    # ------------------------------------------------------------------
    # Prompt text builders
    # ------------------------------------------------------------------

    def _bidding_prompt_text(
        self,
        history: list,
        next_player: Optional[BasePlayer] = None,
    ) -> Text:
        t = Text()
        # Find what last non-self event was — for "West passed.".
        if history:
            last_player, last_bid = history[-1]
            label = _position_short(last_player.position)
            if last_bid == "Pass":
                t.append(f"{label} passed. ", style=FG)
            elif last_bid == "Double":
                t.append(f"{label} doubled. ", style=f"bold {GOLD}")
            elif last_bid == "Redouble":
                t.append(f"{label} redoubled. ", style=f"bold {GOLD}")
            elif isinstance(last_bid, tuple):
                value, suit = last_bid
                t.append(f"{label} bid {value} ", style=FG)
                t.append(_suit_glyph(suit), style=_suit_color(suit))
                t.append(". ", style=FG)
        t.append("Your bid? ", style=FG)
        # Adaptive example. When the last non-pass bid was Double AND
        # the next bidder is on the contracting team, redouble is the
        # only meaningful active option — surface it explicitly.
        if next_player is not None and _redouble_available_to(history, next_player):
            t.append("(pass / redouble)", style=DIM)
        else:
            t.append("(e.g. '80 H' / 'pass' / 'double')", style=DIM)
        return t

    def _card_prompt_text(
        self, playable_cards: list[Card], hand_size: int
    ) -> Text:
        t = Text()
        t.append("Your turn. ", style=f"bold {YELLOW}")
        if playable_cards and len(playable_cards) == 1:
            t.append("Only one legal play. ", style=f"bold {YELLOW}")
        t.append(f"Choose card [1-{hand_size}]:", style=f"bold {YELLOW}")
        return t

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    def _log(self, line: Text) -> None:
        """Append a styled line and trim to ``LOG_MAX``."""
        self.event_log.append(line)
        if len(self.event_log) > self.LOG_MAX:
            del self.event_log[: len(self.event_log) - self.LOG_MAX]

    def _format_bid_log(self, player: BasePlayer, bid) -> Text:
        """Build the log line for a single bid action."""
        label = _position_short(player.position)
        color = _position_color(player.position)
        t = Text()
        t.append(f"{label} ", style=f"bold {color}")
        if bid == "Pass":
            t.append("passed.", style=DIM)
        elif bid == "Double":
            t.append("doubled.", style=f"bold {GOLD}")
        elif bid == "Redouble":
            t.append("redoubled.", style=f"bold {GOLD}")
        elif isinstance(bid, tuple):
            value, suit = bid
            t.append(f"bid {value} ", style=FG)
            t.append(_suit_glyph(suit), style=_suit_color(suit))
            t.append(".", style=FG)
        return t

    def _format_card_log(self, player: BasePlayer, card: Card) -> Text:
        label = _position_short(player.position)
        color = _position_color(player.position)
        t = Text()
        t.append(f"{label} ", style=f"bold {color}")
        t.append("plays ", style=FG)
        t.append_text(_format_card_compact(card))
        t.append(".", style=FG)
        return t

    def _format_trick_won_log(
        self, winner: BasePlayer, trick_points: int
    ) -> Text:
        label = _position_short(winner.position)
        color = _position_color(winner.position)
        t = Text()
        t.append(f"{label} ", style=f"bold {color}")
        t.append(f"wins trick ({trick_points} pts).", style=f"bold {GOLD}")
        return t

    def _panel_round_recap(
        self,
        round_: "Round",
        running_scores: dict,
    ) -> Panel:
        """Between-rounds recap panel — what just happened, in one read.

        Lays out the round's points as a two-column table so the user
        can trace how the round total was built: trump-aware card
        points per team, then dix-de-der and belote bonuses, then the
        final round score the contract bonus / penalty produced.
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
            body.append_text(_format_contract_short(contract))
            body.append("\n")
            # Made/failed badge
            contract_team = contract.team.name
            made = round_.round_scores.get(contract_team, 0) > 0
            body.append("  Result:    ", style=DIM)
            if made:
                body.append("✓ Contract made", style=f"bold {GREEN_CHECK}")
            else:
                body.append("✗ Contract failed", style=f"bold {RED}")
            body.append("\n\n")

        # Per-team breakdown table. Card points (trump-aware) from
        # the tricks each team won, plus dix-de-der and belote
        # bonuses, then the actual round score from the engine.
        breakdown = self._recap_breakdown(round_)
        trump = contract.suit if contract is not None else None
        body.append_text(
            self._format_recap_table(breakdown, ns_round, ew_round, trump)
        )
        body.append("\n")

        # Running totals + target.
        body.append("  Running    ", style=DIM)
        body.append(f"{running_ns:>6}", style=f"bold {BLUE}")
        body.append(f"  {running_ew:>6}", style=f"bold {ORANGE}")
        body.append(f"     target {self.target_score}", style=DIM)

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

    def _recap_breakdown(self, round_) -> dict:
        """Per-team point components used by the recap panel.

        Returns a dict keyed by team name with:
            contract:     contract-related bonus credited to this team
                          (attacker base on normal made, 160+base*mult
                          on doubled/redoubled made, (160+base)*mult on
                          failed for the defender; 0 otherwise).
            card_points:  sum of card.get_points(trump) across the
                          team's tricks (trump-aware so trump K/Q/J/9
                          count their trump values).
            dix_de_der:   10 if the team took the last trick, else 0.
            belote:       20 if the team holds both K and Q of trump
                          in its tricks, else 0.
            trick_count:  number of tricks won.
            cards_count:  True when the engine actually folds card /
                          dix / belote values into the round score for
                          this team. False when the engine substitutes
                          a flat bonus (doubled-made attacker, failed
                          defender) — the recap then shows em-dashes
                          for those rows so the visible addition
                          matches the engine-computed round score.

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

        belote_team = self._belote_team_in_round(round_)
        round_scores = getattr(round_, "round_scores", {}) or {}

        attacking_team = (
            contract.team.name if contract is not None else None
        )
        contract_made = (
            attacking_team is not None
            and round_scores.get(attacking_team, 0) > 0
        )
        if contract is not None:
            base = contract.get_base_points()
            mult = contract.get_multiplier()
            is_slam_family = contract.is_slam_family()
        else:
            base = 0
            mult = 1
            is_slam_family = False

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
            contract_row = 0
            cards_count = True
            belote_count = True

            if contract is None:
                # All passed — nothing scores.
                cards_count = False
                belote_count = False
            elif is_slam_family:
                # Slam family uses the symmetric grid: at_risk goes to the
                # side that wins the contract (attacker if made, defender
                # if failed). Card points and dix de der do NOT add. The
                # belote bonus (+20) is layered on top for whichever team
                # holds it, independent of which side wins.
                cards_count = False
                belote_count = True
                at_risk = base * mult
                if is_attacker:
                    contract_row = at_risk if contract_made else 0
                else:
                    contract_row = at_risk if not contract_made else 0
            elif is_attacker:
                if contract_made:
                    if mult > 1:
                        # Doubled or redoubled made → flat bonus,
                        # the engine ignores the attacker's cards.
                        contract_row = 160 + base * mult
                        cards_count = False
                        belote_count = False
                    else:
                        contract_row = base
                else:
                    # Failed → attacker gets 0; cards don't add.
                    contract_row = 0
                    cards_count = False
                    belote_count = False
            else:
                # Defender.
                if not contract_made:
                    contract_row = (160 + base) * mult
                    cards_count = False
                    belote_count = False

            out[team_name] = {
                "contract": contract_row,
                "card_points": raw_card_pts if cards_count else 0,
                "dix_de_der": raw_dix if cards_count else 0,
                "belote": raw_belote if belote_count else 0,
                "trick_count": len(tricks),
                "cards_count": cards_count,
            }
        return out

    def _format_recap_table(
        self,
        breakdown: dict,
        ns_round: int,
        ew_round: int,
        trump: Optional[Suit] = None,
    ) -> Text:
        """Render the per-team breakdown table inside the recap panel.

        Rows: Contract (the bonus a team earns from the contract being
        made or failed), Tricks won (cards), Dix de der, Belote, then
        a divider and the engine-computed Round score. The four
        component rows always sum to the Round score — when the engine
        substitutes a flat formula (doubled-made attacker, failed
        defender), the cards / dix / belote rows display em-dashes so
        the addition stays honest.
        """
        ns = breakdown.get("North-South", {})
        ew = breakdown.get("East-West", {})

        def _num_cell(value: int, *, show_zero: bool = True, sign: bool = False) -> Text:
            t = Text()
            if value == 0 and not show_zero:
                t.append(f"{'—':>6}", style=DIM)
                return t
            if sign and value > 0:
                t.append(f"{('+' + str(value)):>6}", style="bold")
            else:
                t.append(f"{value:>6}", style="bold")
            return t

        def _dash_cell() -> Text:
            return Text(f"{'—':>6}", style=DIM)

        def _cards_cell(side: dict, key: str) -> Text:
            """Number cell that shows '—' when the engine ignores this
            team's card-based contributions (doubled-made attacker,
            failed defender)."""
            if not side.get("cards_count", True):
                return _dash_cell()
            return _num_cell(side.get(key, 0), show_zero=False, sign=True)

        # Header row: "                          N-S     E-W"
        header = Text()
        header.append(f"  {'':<22}", style=DIM)
        header.append(f"{'N-S':>6}", style=f"bold {BLUE}")
        header.append(f"  {'E-W':>6}", style=f"bold {ORANGE}")
        header.append("\n")

        # Contract row — the bonus each team gets from the contract.
        row_contract = Text()
        row_contract.append(f"  Contract              ", style=FG)
        row_contract.append_text(
            _num_cell(ns.get("contract", 0), show_zero=False, sign=True)
        )
        row_contract.append("  ")
        row_contract.append_text(
            _num_cell(ew.get("contract", 0), show_zero=False, sign=True)
        )
        row_contract.append("\n")

        # Card points row (sum across the team's tricks). Trick count
        # is shown even when card points don't contribute — it's an
        # honest fact independent of the score formula.
        ns_tricks = ns.get("trick_count", 0)
        ew_tricks = ew.get("trick_count", 0)
        row_cards = Text()
        row_cards.append(
            f"  Tricks won (cards)    ", style=FG,
        )
        if ns.get("cards_count", True):
            row_cards.append_text(_num_cell(ns.get("card_points", 0)))
        else:
            row_cards.append_text(_dash_cell())
        row_cards.append("  ")
        if ew.get("cards_count", True):
            row_cards.append_text(_num_cell(ew.get("card_points", 0)))
        else:
            row_cards.append_text(_dash_cell())
        row_cards.append(f"   ({ns_tricks}/{ew_tricks} tricks)", style=DIM)
        row_cards.append("\n")

        # Dix-de-der.
        row_dxd = Text()
        row_dxd.append(f"  Dix de der            ", style=FG)
        row_dxd.append_text(_cards_cell(ns, "dix_de_der"))
        row_dxd.append("  ")
        row_dxd.append_text(_cards_cell(ew, "dix_de_der"))
        row_dxd.append("\n")

        # Belote (suit glyph reflects the actual trump suit).
        row_bel = Text()
        row_bel.append(f"  Belote (K + Q ", style=FG)
        if trump is not None and trump != Suit.NO_TRUMP:
            row_bel.append(_suit_glyph(trump), style=_suit_color(trump))
        else:
            row_bel.append("—", style=DIM)
        # Keep the column alignment stable: 2 cols for the glyph block.
        row_bel.append(")      ", style=FG)
        row_bel.append_text(_cards_cell(ns, "belote"))
        row_bel.append("  ")
        row_bel.append_text(_cards_cell(ew, "belote"))
        row_bel.append("\n")

        # Divider sits under the two numeric columns only, signalling
        # "the next row is the round-score subtotal". Label area stays
        # blank so the eye lands on the numbers.
        divider = Text()
        divider.append(" " * 24, style=DIM)
        divider.append("─" * 6, style=DIM)
        divider.append("  ", style=DIM)
        divider.append("─" * 6, style=DIM)
        divider.append("\n")

        row_total = Text()
        row_total.append(f"  Round score           ", style=f"bold {GOLD}")
        row_total.append_text(_num_cell(ns_round, sign=True))
        row_total.append("  ")
        row_total.append_text(_num_cell(ew_round, sign=True))
        row_total.append("\n")

        out = Text()
        out.append_text(header)
        out.append_text(row_contract)
        out.append_text(row_cards)
        out.append_text(row_dxd)
        out.append_text(row_bel)
        out.append_text(divider)
        out.append_text(row_total)
        return out

    @staticmethod
    def _belote_team_in_round(round_) -> Optional[str]:
        """Return the team name that took both K and Q of trump this round."""
        contract = getattr(round_, "contract", None)
        if contract is None or contract.suit == Suit.NO_TRUMP:
            return None
        trump = contract.suit
        for team_name, tricks in getattr(round_, "team_tricks", {}).items():
            has_king = False
            has_queen = False
            for trick in tricks:
                for _, card in trick.get_plays():
                    if card.suit == trump and card.rank == Rank.KING:
                        has_king = True
                    if card.suit == trump and card.rank == Rank.QUEEN:
                        has_queen = True
            if has_king and has_queen:
                return team_name
        return None

    def _panel_event_log(self) -> Panel:
        """Bottom panel showing the last ``LOG_MAX`` events."""
        body = Text()
        if not self.event_log:
            body.append("(no events yet)", style=DIM)
        else:
            for i, line in enumerate(self.event_log):
                if i > 0:
                    body.append("\n")
                body.append_text(line)
        return Panel(
            body,
            title=Text("Log", style=f"bold {TITLE}"),
            border_style=BORDER_DIM,
            box=ROUNDED,
            width=70,
            height=self.LOG_MAX + 2,
        )

    # ------------------------------------------------------------------
    # Prompt text builders (continued)
    # ------------------------------------------------------------------

    def _ai_bid_announcement(
        self, player: BasePlayer, bid
    ) -> Text:
        """Prompt text shown during an AI's brief post-bid pause."""
        label = _position_short(player.position)
        t = Text()
        if bid == "Pass":
            t.append(f"{label} passes.", style=DIM)
        elif bid == "Double":
            t.append(f"{label} doubles.", style=f"bold {GOLD}")
        elif bid == "Redouble":
            t.append(f"{label} redoubles.", style=f"bold {GOLD}")
        elif isinstance(bid, tuple):
            value, suit = bid
            t.append(f"{label} bids {value} ", style=FG)
            t.append(_suit_glyph(suit), style=_suit_color(suit))
            t.append(".", style=FG)
        else:
            t.append(f"{label} is thinking…", style=DIM)
        return t

    def _ai_card_announcement(
        self, player: BasePlayer, card: Card
    ) -> Text:
        """Prompt text shown during an AI's brief post-play pause."""
        label = _position_short(player.position)
        t = Text()
        t.append(f"{label} plays ", style=FG)
        t.append_text(_format_card_compact(card))
        t.append(".", style=FG)
        return t

    def _trick_won_prompt_text(self, winner: BasePlayer) -> Text:
        t = Text()
        label = _position_short(winner.position)
        if winner.is_human:
            t.append("You won the trick. ", style=f"bold {GOLD}")
            t.append("Press [Enter] to continue…", style=FG)
        else:
            t.append(f"{label} won the trick. ", style=FG)
            t.append("Press [Enter] to continue…", style=DIM)
        return t

    def _end_game_prompt_text(self) -> Text:
        t = Text()
        t.append("Game over.  ", style=FG)
        t.append("[n]", style=f"bold {YELLOW}")
        t.append(" new game  ·  ", style=FG)
        t.append("[r]", style=f"bold {YELLOW}")
        t.append(" rematch  ·  ", style=FG)
        t.append("[q]", style=f"bold {YELLOW}")
        t.append(" quit", style=FG)
        return t

    # ------------------------------------------------------------------
    # End-game panels
    # ------------------------------------------------------------------

    def _panel_game_over_banner(self, status: dict) -> Panel:
        winner_name = status.get("winner") or "—"
        winner_abbr = _team_abbr(winner_name) if winner_name != "—" else "—"
        final = status.get("final_scores", {})
        ns = final.get("North-South", 0)
        ew = final.get("East-West", 0)
        is_ns_winner = winner_name == "North-South"

        body = Text()
        body.append("\n")
        # Winner banner row: gold pill spanning full inner width.
        banner = f"★   {winner_abbr}   WINS   ★"
        pad = max(0, (66 - len(banner)) // 2)
        body.append(" " * pad)
        body.append(banner, style=f"bold {GOLD_FG} on {GOLD_BG}")
        body.append("\n\n")
        body.append("Final score".center(66), style=DIM)
        body.append("\n")
        # Score line: "1620   vs   1420"
        ns_str = str(ns)
        ew_str = str(ew)
        score_line = Text()
        if is_ns_winner:
            score_line.append(ns_str, style=f"bold {GOLD}")
        else:
            score_line.append(ns_str, style=f"bold {BLUE}")
        score_line.append("   vs   ", style=DIM)
        if not is_ns_winner:
            score_line.append(ew_str, style=f"bold {GOLD}")
        else:
            score_line.append(ew_str, style=f"bold {ORANGE}")
        pad2 = max(0, (66 - score_line.cell_len) // 2)
        body.append(" " * pad2)
        body.append_text(score_line)
        body.append("\n")
        # Team labels
        label_line = Text()
        label_line.append("N-S".rjust(len(ns_str)), style=f"bold {BLUE}")
        label_line.append("       ", style=DIM)
        label_line.append("E-W".ljust(len(ew_str)), style=f"bold {ORANGE}")
        pad3 = max(0, (66 - label_line.cell_len) // 2)
        body.append(" " * pad3)
        body.append_text(label_line)

        return Panel(
            body,
            title=Text("Game over", style=f"bold {GOLD}"),
            border_style=GOLD,
            box=DOUBLE,
            width=70,
        )

    def _panel_round_summary(self) -> Panel:
        table = Table(
            show_header=True,
            header_style=f"bold {DIM}",
            border_style=RULE,
            box=SQUARE,
            expand=True,
        )
        table.add_column("#", justify="right", style=DIM, width=3)
        table.add_column("Contract", justify="left")
        table.add_column("Made", justify="center", width=5)
        table.add_column("N-S pts", justify="right")
        table.add_column("E-W pts", justify="right")
        table.add_column("Running N-S / E-W", justify="right", style=DIM)

        for row in self.history:
            num = str(row.round_number)
            contract_cell = self._format_summary_contract(row)
            made_cell = (
                Text("✓", style=f"bold {GREEN_CHECK}")
                if row.contract_made
                else Text("✗", style=f"bold {RED}")
            )
            if row.contract is None:
                made_cell = Text("—", style=DIM)
            ns_cell = (Text(str(row.ns_pts), style=f"bold {BLUE}")
                       if row.ns_pts > 0
                       else Text("·", style=DIM))
            ew_cell = (Text(str(row.ew_pts), style=f"bold {ORANGE}")
                       if row.ew_pts > 0
                       else Text("·", style=DIM))
            running = f"{row.running_ns} / {row.running_ew}"
            table.add_row(num, contract_cell, made_cell, ns_cell, ew_cell,
                          Text(running, style=DIM))

        return Panel(
            table,
            title=Text("Round-by-round summary", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=70,
        )

    def _format_summary_contract(self, row: RoundSummary) -> Text:
        t = Text()
        if row.contract is None:
            t.append("all passed", style=DIM)
            return t
        team_abbr = _team_abbr(row.contract_team_name or "")
        team_color = _team_color(row.contract_team_name or "")
        t.append(team_abbr, style=f"bold {team_color}")
        t.append(" ", style=FG)
        if row.contract.value == "Slam":
            value_str = "Slam"
        elif row.contract.value == "SoloSlam":
            value_str = "Solo Slam"
        else:
            value_str = str(row.contract.value)
        t.append(value_str, style="bold")
        t.append(" ", style=FG)
        t.append(_suit_glyph(row.contract.suit),
                 style=_suit_color(row.contract.suit))
        if row.contract.redouble:
            t.append(" surcoinché", style=GOLD)
        elif row.contract.double:
            t.append(" coinché", style=GOLD)
        return t


# ---------------------------------------------------------------------------
# Layout helper
# ---------------------------------------------------------------------------


def _two_column(left, right, *, left_width: int) -> Table:
    """Place two panels side-by-side with a fixed-width left column.

    A ``Table.grid`` keeps the row exactly as tall as the panels (unlike
    ``rich.layout.Layout``, which expands to fill the console height).
    """
    grid = Table.grid(expand=False, padding=(0, 1))
    grid.add_column(width=left_width, no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_row(left, right)
    return grid
