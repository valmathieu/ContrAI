"""Rich-based terminal UI for the contrée game.

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

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from contrai_core.bid import Bid

from contrai_core import (
    Auction,
    BasePlayer,
    Card,
    Contract,
    Trick,
)
from contrai_engine.model.player import wire_to_bid
from contrai_engine.view.bidding_rules import (
    _bid_to_legacy,
    _double_available_to,
    _illegal_bid_reason,
    _min_legal_contract_value,
    _redouble_available_to,
)
from contrai_engine.view.formatting import (
    _format_card_compact,
    _format_contract_short,
    _position_color,
    _position_short,
    _suit_color,
    _suit_glyph,
    _team_abbr,
    _team_color,
)
from contrai_engine.view.layout import (
    _panel_event_log,
    _panel_prompt,
    _two_column,
)
from contrai_engine.view.parsing import _parse_bid_input, _parse_card_input
from contrai_engine.view.screens.bidding import (
    _ai_bid_announcement,
    _bidding_prompt_text,
    _panel_bidding_history,
)
from contrai_engine.view.screens.landing import (
    _landing_prompt_text,
    _landing_subtitle,
    _landing_suit_ribbon,
    _landing_title,
    _panel_game_score,
    _panel_game_setup,
    _panel_players,
)
from contrai_engine.view.screens.recap import (
    _contract_made,
    _panel_round_recap,
)
from contrai_engine.view.screens.trick import (
    _ai_card_announcement,
    _card_prompt_text,
    _panel_current_trick,
    _panel_hand,
    _panel_last_trick,
    _panel_round,
    _trick_won_prompt_text,
)
from contrai_engine.view.state_helpers import (
    _resolve_delay,
    _sort_hand_for_display,
)
from contrai_engine.view.theme import (
    BLUE,
    BORDER,
    DEFAULT_TARGET,
    DIM,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_CHECK,
    GREEN_FG,
    ORANGE,
    RED,
    RULE,
    TARGET_OPTIONS,
    TEAM_ABBR,
    TITLE,
    YELLOW,
)
from rich.box import DOUBLE, ROUNDED, SQUARE
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from contrai_engine.model.game import Game
    from contrai_engine.model.round import Round


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
            A :class:`Bid` that is guaranteed legal in ``auction`` —
            the loop re-prompts on both unparseable input and bids the
            auction rules reject, so :meth:`Auction.apply` downstream
            never sees an illegal human bid.
        """
        legacy_bids = [
            (bid.player, _bid_to_legacy(bid)) for bid in auction.bids
        ]
        # A rejection from the previous iteration. Rendered *inside* the
        # next frame's Prompt panel rather than ``console.print``ed after
        # the input — otherwise the loop's ``console.clear()`` pushes the
        # standalone message up into scrollback, where it's invisible
        # until the player scrolls back.
        notice: Optional[Text] = None
        while True:
            self._render_in_game(
                phase="bidding",
                current_player=player,
                bidding_history=legacy_bids,
                prompt_question=_bidding_prompt_text(legacy_bids, player),
                mandatory=False,
                notice=notice,
            )
            raw = self.console.input(
                Text("> ", style=f"bold {GREEN_FG}").markup
            )
            parsed = _parse_bid_input(raw)
            if parsed is None:
                notice = Text(
                    "✗ Unrecognized bid. Try '80 h', 'pass', "
                    "'double', 'redouble'.",
                    style=RED,
                )
                continue
            bid = wire_to_bid(player, parsed)
            # Syntactic parsing only checks the *shape* of the input;
            # the auction owns the rules (precedence, the Double freeze,
            # can't-double-your-own-side, …). Validate here so an
            # illegal-but-parseable bid re-prompts instead of escaping to
            # Auction.apply, where it would raise IllegalBidError and
            # crash the CLI.
            if not auction.is_legal(bid):
                notice = Text(
                    f"✗ {_illegal_bid_reason(bid, auction)}",
                    style=RED,
                )
                continue
            return bid

    def request_card_action(
        self,
        player: BasePlayer,
        trick: Trick,
        contract: Contract,
        playable_cards: list[Card],
    ) -> Card:
        """Prompt the human for a card. Loops until input parses."""
        trump_suit = contract.suit if contract else None
        # See ``request_bid_action``: the rejection rides inside the next
        # frame's Prompt panel so the ``console.clear()`` on re-render
        # can't bury it in scrollback.
        notice: Optional[Text] = None
        while True:
            sorted_hand = _sort_hand_for_display(list(player.hand), trump_suit)
            self._render_in_game(
                phase="playing",
                current_player=player,
                current_trick=trick,
                playable_cards=playable_cards,
                prompt_question=_card_prompt_text(
                    playable_cards, len(sorted_hand)
                ),
                mandatory=True,
                notice=notice,
            )
            raw = self.console.input(
                Text("> ", style=f"bold {YELLOW}").markup
            )
            card = _parse_card_input(raw, sorted_hand, playable_cards)
            if card is None:
                notice = Text(
                    f"✗ Pick a number between 1 and {len(sorted_hand)} "
                    "matching a green-highlighted card.",
                    style=RED,
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
            prompt_question=_trick_won_prompt_text(winner),
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
            prompt_question=_ai_bid_announcement(player, legacy_bid),
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
            prompt_question=_ai_card_announcement(player, card),
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
        self.console.print(
            _panel_round_recap(round_, running_scores, self.target_score)
        )
        if is_final:
            prompt_text = Text(
                "Press [Enter] to see the final score…", style=FG
            )
        else:
            prompt_text = Text(
                "Press [Enter] to deal the next round…", style=FG
            )
        self.console.print(_panel_prompt(prompt_text, mandatory=False))
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
            made = _contract_made(round_)
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
            self.console.print(_landing_title())
            self.console.print(_landing_subtitle())
            self.console.print(_landing_suit_ribbon())
            self.console.print()
            self.console.print(_panel_game_setup(selected_target))
            self.console.print(_panel_players())
            self.console.print(_panel_prompt(
                _landing_prompt_text(selected_target),
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
            self.console.print(_panel_prompt(
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
        notice: Optional[Text] = None,
    ) -> None:
        """Clear the screen and print all in-game panels stacked.

        ``notice`` is an optional rejection/error line (e.g. an illegal
        bid or out-of-range card index) rendered inside the Prompt panel
        so it survives the ``console.clear()`` that opens every frame.
        """
        self.console.clear()
        round_ = self.game.current_round if self.game else None
        # Top row: game score + round info
        scores = (
            self.game.scores if self.game
            else {"North-South": 0, "East-West": 0}
        )
        top_left = _panel_game_score(scores, self.target_score)
        top_right = _panel_round(round_, phase)
        self.console.print(_two_column(top_left, top_right, left_width=24))
        # Middle row: last trick + current trick
        mid_left = _panel_last_trick(round_, self.last_completed_trick)
        mid_right = _panel_current_trick(
            round_, current_trick, phase, current_player, trick_winner,
            bidding_history=bidding_history,
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
            hand_panel = _panel_hand(
                human, current_trick, playable_cards, phase, round_,
                interactive=is_human_turn,
            )
        else:
            hand_panel = None
        # Bidding history for state 1, if any non-pass bids
        if phase == "bidding" and bidding_history:
            history_panel = _panel_bidding_history(bidding_history)
            self.console.print(history_panel)
        if hand_panel is not None:
            self.console.print(hand_panel)
        # Event log: a rolling narrative of the last few engine events.
        self.console.print(_panel_event_log(self.event_log, self.LOG_MAX))
        self.console.print(
            _panel_prompt(prompt_question, mandatory, notice=notice)
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


    # ------------------------------------------------------------------
    # Prompt text builders (continued)
    # ------------------------------------------------------------------

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
        # SlamLevel.__str__ yields "Slam" / "Solo Slam"; numerics "80"…"180".
        value_str = str(row.contract.value)
        t.append(value_str, style="bold")
        t.append(" ", style=FG)
        t.append(_suit_glyph(row.contract.suit),
                 style=_suit_color(row.contract.suit))
        if row.contract.redouble:
            t.append(" redoubled", style=GOLD)
        elif row.contract.double:
            t.append(" doubled", style=GOLD)
        return t
