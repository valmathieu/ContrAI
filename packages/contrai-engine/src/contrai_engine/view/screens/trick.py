"""Mid-trick / trick-won screen rendering for the Rich terminal UI.

The in-game table: the Round info panel, the last-trick and current-trick
panels, the 4-player card diamond (with the live winner highlight and the
belote badge), the human's hand row, and the per-play prompts. Pure
builders; ``RichView._render_in_game`` feeds them state and prints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from contrai_core import BasePlayer, Card, Suit, Trick
from rich.align import Align
from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_engine.view.formatting import (
    _format_card_compact,
    _format_contract_short,
    _format_trump_label,
    _position_color,
    _position_short,
    _rank_short,
    _suit_color,
    _suit_color_dim,
    _suit_glyph,
)
from contrai_engine.view.screens.bidding import _render_bidding_diamond
from contrai_engine.view.state_helpers import (
    _belote_by_position,
    _current_winner,
    _explain_constraint,
    _sort_hand_for_display,
)
from contrai_engine.view.theme import (
    BLUE,
    BORDER,
    BORDER_DIM,
    DIM,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_BG,
    GREEN_FG,
    ORANGE,
    TITLE,
    YELLOW,
)

if TYPE_CHECKING:
    from contrai_engine.model.round import Round


def _panel_round(round_: Optional["Round"], phase: str) -> Panel:
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
        ns_pts, ew_pts = _round_running_points(round_)
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


def _round_running_points(round_: Optional["Round"]) -> tuple[int, int]:
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


def _panel_last_trick(
    round_: Optional["Round"],
    last_completed_trick: Optional[tuple[Trick, BasePlayer]],
) -> Panel:
    if not last_completed_trick:
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
    trick, winner = last_completed_trick
    trump = round_.contract.suit if round_ and round_.contract else None
    body = _render_diamond(
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
    round_: Optional["Round"],
    trick: Optional[Trick],
    phase: str,
    current_player: Optional[BasePlayer],
    trick_winner: Optional[BasePlayer],
    bidding_history: Optional[list] = None,
) -> Panel:
    title_suffix = ""
    if round_ and phase in ("playing", "trick_won"):
        trick_idx = len(round_.tricks) + (0 if phase == "trick_won" else 1)
        trick_idx = min(max(1, trick_idx), 8)
        title_suffix = f" (#{trick_idx})"

    if phase == "bidding":
        # Reuse the table slot for the auction: each seat shows the
        # player's latest bid so the human can read announces off
        # the diamond the same way they read cards during play.
        body = _render_bidding_diamond(
            bidding_history or [],
            pending_position=(
                current_player.position
                if current_player is not None
                else None
            ),
            width=42,
        )
        body.append("\n")
        if current_player is not None and current_player.is_human:
            body.append("→ Your bid", style=f"bold {YELLOW}")
        elif current_player is not None:
            body.append(f"→ {current_player.position} to bid", style=DIM)
        return Panel(
            body,
            title=Text("Bidding", style=f"bold {TITLE}"),
            border_style=BORDER,
            box=ROUNDED,
            width=46,
            height=8,
        )

    if trick is None:
        body = Text("(none)", style=DIM, justify="center")
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
    body = _render_diamond(
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
            cell = _render_card_cell(idx, card, is_playable, cell_phase)
            cards_row.append_text(cell)
            cards_row.append(" ")

    body = Text()
    body.append("\n")
    pad = max(0, (66 - cards_row.cell_len) // 2)
    body.append(" " * pad)
    body.append_text(cards_row)
    body.append("\n")

    if not sorted_hand:
        # The cards row already reads "(no cards left)"; a second
        # "(hand empty)" line would just be redundant.
        hint = Text("", justify="center")
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


def _render_card_cell(
    idx: int, card: Card, is_playable: bool, phase: str
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


def _card_prompt_text(playable_cards: list[Card], hand_size: int) -> Text:
    t = Text()
    t.append("Your turn. ", style=f"bold {YELLOW}")
    if playable_cards and len(playable_cards) == 1:
        t.append("Only one legal play. ", style=f"bold {YELLOW}")
    t.append(f"Choose card [1-{hand_size}]:", style=f"bold {YELLOW}")
    return t


def _ai_card_announcement(player: BasePlayer, card: Card) -> Text:
    """Prompt text shown during an AI's brief post-play pause."""
    label = _position_short(player.position)
    t = Text()
    t.append(f"{label} plays ", style=FG)
    t.append_text(_format_card_compact(card))
    t.append(".", style=FG)
    return t


def _trick_won_prompt_text(winner: BasePlayer) -> Text:
    t = Text()
    label = _position_short(winner.position)
    if winner.is_human:
        t.append("You won the trick. ", style=f"bold {GOLD}")
        t.append("Press [Enter] to continue…", style=FG)
    else:
        t.append(f"{label} won the trick. ", style=FG)
        t.append("Press [Enter] to continue…", style=DIM)
    return t
