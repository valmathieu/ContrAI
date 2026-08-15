"""Rich-based terminal UI for the contrée game.

Implements the five-screen design (landing, bidding,
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

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

from contrai_core.bid import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    RedoubleBid,
)

from contrai_core import (
    Auction,
    BasePlayer,
    Card,
    Contract,
    Play,
    TeamSide,
    rules_for,
)
from contrai_engine.options import DebugOptions
from contrai_engine.view.bidding_rules import _illegal_bid_reason
from contrai_engine.view.formatting import (
    _format_card_compact,
    _format_contract_short,
    _position_color,
    _position_short,
    _suit_color,
    _suit_glyph,
)
from contrai_engine.view.layout import (
    _panel_event_log,
    _panel_game_score,
    _panel_prompt,
    _two_column,
)
from contrai_engine.view.parsing import _parse_bid_input, _parse_card_input
from contrai_engine.view.screens.bidding import (
    _ai_bid_announcement,
    _bid_rejection_text,
    _bidding_prompt_text,
    _panel_bidding_history,
)
from contrai_engine.view.screens.debug import (
    _autoplay_pause_text,
    _panel_debug_hands,
)
from contrai_engine.view.screens.endgame import (
    _end_game_prompt_text,
    _panel_game_over_banner,
    _panel_round_summary,
)
from contrai_engine.view.screens.landing import (
    _landing_prompt_text,
    _landing_subtitle,
    _landing_suit_ribbon,
    _landing_title,
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
    _trick_index,
)
from contrai_engine.view.theme import (
    DEFAULT_TARGET,
    DIM,
    FG,
    GOLD,
    GREEN_FG,
    RED,
    TARGET_OPTIONS,
    YELLOW,
)
from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from contrai_engine.model.game import Game, GameOverStatus
    from contrai_engine.model.round import Round

# A dedicated logger name (rather than ``__name__``, which would be
# "contrai_engine.view.rich_view") so the debug log file's narrative
# mirror reads as its own event stream — every line the on-screen event
# log ever shows, plus the closing game-over summary, independent of any
# other diagnostics this module might one day emit under its own name.
logger = logging.getLogger("contrai_engine.view.events")


# ---------------------------------------------------------------------------
# Round summary (UI-side history)
# ---------------------------------------------------------------------------


@dataclass
class RoundSummary:
    """One row of the end-game round-by-round table."""

    round_number: int
    contract: Optional[Contract]
    contract_side: Optional[TeamSide]
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

    def __init__(self, options: DebugOptions | None = None) -> None:
        """Create an unattached view: fresh console, empty per-game state.

        Args:
            options: Parsed debug-mode flags, or ``None`` for the
                all-off defaults — the back-compat anchor: constructing
                ``RichView()`` with no arguments reproduces today's
                runtime behavior exactly. No file/logging setup happens
                here; that is the CLI's job, once, before the view is
                constructed.
        """
        self.options: DebugOptions = options or DebugOptions()
        self.console: Console = Console()
        self.target_score: int = DEFAULT_TARGET
        self.history: list[RoundSummary] = []
        self.last_completed_trick: Optional[
            tuple[Sequence[Play], BasePlayer]
        ] = None
        self.game: Optional[Game] = None
        # Rolling narrative log shown below the hand. Captures the last
        # ``LOG_MAX`` events (deal, bids, plays, trick winners, redeal,
        # belote announcements). Survives across rounds so the end of
        # round N and the start of round N+1 share continuity.
        self.event_log: list[Text] = []

    # ------------------------------------------------------------------
    # Lifecycle wiring (called by the CLI)
    # ------------------------------------------------------------------

    def attach(self, game: Game, target_score: int) -> None:
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
            auction: The current auction state — its ``bids`` feed the
                renderer directly and its :meth:`Auction.legal_actions`
                drive the adaptive prompt hint.

        Returns:
            A :class:`Bid` that is guaranteed legal in ``auction`` —
            the loop re-prompts on both unparseable input and bids the
            auction rules reject, so :meth:`Auction.apply` downstream
            never sees an illegal human bid.
        """
        bidding_history = list(auction.bids)
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
                bidding_history=bidding_history,
                prompt_question=_bidding_prompt_text(auction, player),
                mandatory=False,
                notice=notice,
            )
            raw = self.console.input(
                Text("> ", style=f"bold {GREEN_FG}").markup
            )
            bid = _parse_bid_input(raw, player)
            if bid is None:
                notice = _bid_rejection_text(auction, player)
                continue
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
        plays: Sequence[Play],
        contract: Contract,
        playable_cards: list[Card],
    ) -> Card:
        """Prompt the human for a card. Loops until input parses.

        ``plays`` are the core ``(player, card)`` records of the trick
        so far — empty when the human is on lead.
        """
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
                current_plays=plays,
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
        self, plays: Sequence[Play], winner: BasePlayer, round_: "Round"
    ) -> None:
        """Record the winner in the log, render the trick-won state, wait
        for Enter (or a timed pause under autoplay).

        ``plays`` are the four core play records of the trick just
        completed.
        """
        trump = round_.contract.suit if round_ and round_.contract else None
        rules = rules_for(trump)
        trick_points = sum(rules.points(play.card) for play in plays)
        self._log(self._format_trick_won_log(winner, trick_points))
        prompt_question = _trick_won_prompt_text(winner)
        if self.options.autoplay:
            prompt_question = _autoplay_pause_text(prompt_question.plain)
        # State 3: full trick shown, winner highlighted, Press Enter.
        self._render_in_game(
            phase="trick_won",
            current_plays=plays,
            trick_winner=winner,
            prompt_question=prompt_question,
            mandatory=False,
        )
        self._wait_or_pause(GOLD, "CONTRAI_AUTOPLAY_PAUSE", 1.2)
        # Rotate: this is now the "last trick" for the next panel.
        self.last_completed_trick = (plays, winner)

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

    def on_all_pass_redeal(self) -> None:
        """Engine hook: every bid was a pass, the deal will be repeated."""
        line = Text("All passed — redealing.", style=f"bold {YELLOW}")
        self._log(line)

    def on_contract_established(self, round_: Round) -> None:
        """Engine hook: bidding ended on a contract — bookmark it in the log."""
        contract = getattr(round_, "contract", None)
        if contract is None:
            return
        line = Text()
        line.append("Contract set: ", style=f"bold {GOLD}")
        line.append_text(_format_contract_short(contract, suit_glyph=True))
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
        self._log(self._format_bid_log(player, bid))
        if getattr(player, "is_human", False):
            return
        self._render_in_game(
            phase="bidding",
            current_player=None,
            bidding_history=list(history),
            prompt_question=_ai_bid_announcement(player, bid),
            mandatory=False,
        )
        self._pause("CONTRAI_AI_BID_DELAY", 1.4)

    def on_card_played(
        self, player: BasePlayer, card: Card, plays: Sequence[Play]
    ) -> None:
        """Record the card in the event log; render+pause for AI players.

        ``plays`` are the core play records of the trick on the table,
        this card included.
        """
        self._log(self._format_card_log(player, card))
        if getattr(player, "is_human", False):
            return
        self._render_in_game(
            phase="playing",
            current_player=None,
            current_plays=plays,
            prompt_question=_ai_card_announcement(player, card),
            mandatory=False,
        )
        self._pause("CONTRAI_AI_CARD_DELAY", 0.9)

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
        self._pause("CONTRAI_AI_CARD_DELAY", 0.9)

    def show_round_recap(
        self,
        round_: "Round",
        running_scores: dict,
        *,
        is_final: bool = False,
        is_tiebreaker: bool = False,
    ) -> None:
        """Full-screen recap shown after each round; waits for Enter.

        Follows the trick-won UX pattern: clear, print the recap panel,
        block on input (or take a timed pause under autoplay). Called
        from the CLI loop after ``on_round_complete`` for *every* round
        — including the one that just clinched the game. When
        ``is_final`` is true the prompt switches to "see the final
        score" so the user knows the next screen is the game-over
        scoreboard, not another deal. When ``is_tiebreaker`` is true
        (both teams level at/above the target) the panel carries a
        sudden-death notice and the prompt deals the tiebreaker round.
        """
        self.console.clear()
        self.console.print(
            _panel_round_recap(
                round_,
                running_scores,
                self.target_score,
                tiebreaker=is_tiebreaker,
            )
        )
        if is_final:
            prompt_text = Text(
                "Press [Enter] to see the final score…", style=FG
            )
        elif is_tiebreaker:
            prompt_text = Text(
                "Press [Enter] to deal the tiebreaker round…", style=FG
            )
        else:
            prompt_text = Text(
                "Press [Enter] to deal the next round…", style=FG
            )
        if self.options.autoplay:
            prompt_text = _autoplay_pause_text(prompt_text.plain)
        self.console.print(_panel_prompt(prompt_text, mandatory=False))
        self._wait_or_pause(GOLD, "CONTRAI_AUTOPLAY_RECAP_PAUSE", 2.5)

    def on_round_complete(self, round_: "Round", running_scores: dict) -> None:
        """Append a row to the end-game history."""
        contract = round_.contract
        ns_pts = round_.round_scores.get(TeamSide.NS, 0)
        ew_pts = round_.round_scores.get(TeamSide.EW, 0)
        running_ns = running_scores.get(TeamSide.NS, 0)
        running_ew = running_scores.get(TeamSide.EW, 0)
        if contract is None:
            made = False
            contract_side = None
        else:
            contract_side = contract.player.position.team_side
            made = _contract_made(round_)
        self.history.append(
            RoundSummary(
                round_number=round_.round_number,
                contract=contract,
                contract_side=contract_side,
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

    def _render_landing_splash(self, selected_target: int) -> None:
        """Print the landing screen's title, subtitle, and setup panels.

        Shared by both the interactive loop and the autoplay branch of
        :meth:`show_landing` — only the prompt line and how it is
        waited on differ between the two.
        """
        self.console.clear()
        self.console.print(_landing_title())
        self.console.print(_landing_subtitle())
        self.console.print(_landing_suit_ribbon())
        self.console.print()
        self.console.print(_panel_game_setup(selected_target))
        self.console.print(_panel_players(self.options.autoplay))

    def show_landing(self, selected_target: int = DEFAULT_TARGET) -> int:
        """Render the landing screen and return the chosen target score.

        Under autoplay the screen renders once, pauses briefly, and
        returns ``selected_target`` unchanged — there is no human to
        type a choice, so the default/passed target stands.
        """
        if self.options.autoplay:
            self._render_landing_splash(selected_target)
            self.console.print(_panel_prompt(
                _autoplay_pause_text(
                    _landing_prompt_text(selected_target).plain
                ),
                mandatory=False,
            ))
            self._pause("CONTRAI_AUTOPLAY_LANDING_PAUSE", 1.2)
            return selected_target
        while True:
            self._render_landing_splash(selected_target)
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

    def _render_end_game_screen(self, status: GameOverStatus) -> None:
        """Print the end-game banner and round-by-round summary table.

        Shared by both the interactive loop and the autoplay branch of
        :meth:`show_end_game` — only the prompt line and how it is
        waited on differ between the two.
        """
        self.console.clear()
        self.console.print(_panel_game_over_banner(status))
        self.console.print(_panel_round_summary(self.history))

    def show_end_game(self, status: GameOverStatus) -> str:
        """Render the end-game scoreboard and return 'n'/'r'/'q'.

        Under autoplay the screen renders once, pauses briefly, logs a
        ``GAME OVER`` summary line, and returns ``"q"`` — one call from
        the CLI's game loop is one full unattended game.
        """
        if self.options.autoplay:
            self._render_end_game_screen(status)
            self.console.print(_panel_prompt(
                _autoplay_pause_text(_end_game_prompt_text().plain),
                mandatory=False,
            ))
            self._pause("CONTRAI_AUTOPLAY_ENDGAME_PAUSE", 2.0)
            logger.info(
                "GAME OVER — winner %s, final scores %s",
                status.winner,
                status.final_scores,
            )
            return "q"
        while True:
            self._render_end_game_screen(status)
            self.console.print(_panel_prompt(
                _end_game_prompt_text(),
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
        current_plays: Optional[Sequence[Play]] = None,
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
        Under debug mode, once a round exists, a face-up strip showing
        every seat's hand is printed below the middle row.
        """
        self.console.clear()
        round_ = self.game.current_round if self.game else None
        # Which of the eight tricks is on the table. Resolved once here
        # — the Round and Last-trick panels never see the trick itself,
        # so they cannot work it out on their own, and deriving it three
        # times would risk three answers.
        trick_index = _trick_index(round_, current_plays or ())
        # Top row: game score + round info
        scores = (
            self.game.scores if self.game
            else {side: 0 for side in TeamSide}
        )
        top_left = _panel_game_score(scores, self.target_score)
        top_right = _panel_round(round_, phase, trick_index)
        self.console.print(_two_column(top_left, top_right, left_width=24))
        # Middle row: last trick + current trick
        mid_left = _panel_last_trick(
            round_, self.last_completed_trick, trick_index
        )
        mid_right = _panel_current_trick(
            round_, current_plays, phase, current_player, trick_winner,
            bidding_history=bidding_history,
            trick_index=trick_index,
        )
        self.console.print(_two_column(mid_left, mid_right, left_width=24))
        # Debug strip: every seat's hand face up, plus the still-in-play
        # summary. ``round_`` is only ever truthy when ``self.game`` is
        # set (it is derived from it above), so ``self.game.players`` is
        # safe here without a separate None check.
        if self.options.debug and round_:
            self.console.print(
                _panel_debug_hands(
                    self.game.players,
                    round_.contract.suit if round_.contract else None,
                    seed=self.options.seed,
                )
            )
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
                human, current_plays, playable_cards, phase, round_,
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
    # Pacing and autoplay pauses
    # ------------------------------------------------------------------

    def _pause(self, env_var: str, default: float) -> None:
        """Sleep for a tunable pacing/autoplay delay.

        Args:
            env_var: Environment variable name that overrides the delay.
            default: Delay in seconds to use when ``env_var`` is unset —
                except under debug mode (:attr:`options`.debug), where
                the default collapses to zero so an unattended debug
                run races through with no artificial pacing. An
                explicit ``env_var`` value still wins over that
                zeroing, so pacing can be forced back on for observation.
        """
        time.sleep(
            _resolve_delay(env_var, 0.0 if self.options.debug else default)
        )

    def _wait_or_pause(
        self, prompt_style: str, env_var: str, default: float
    ) -> None:
        """Block for Enter, or take a timed autoplay pause instead.

        Under autoplay this delegates to :meth:`_pause`, so a Ctrl+C
        during the wait propagates uncaught (``time.sleep`` raises
        ``KeyboardInterrupt`` straight through) — an unattended run must
        stay interruptible. Outside autoplay this is the interactive
        prompt used at the trick-won and round-recap pauses: EOF/Ctrl+C
        are swallowed there, matching the pre-existing idiom at those
        two call sites specifically (unlike the bid/card/landing/
        end-game prompts elsewhere in this class, which let EOF/Ctrl+C
        propagate uncaught).

        Args:
            prompt_style: Rich style name for the "> " input marker
                shown while waiting interactively (unused under
                autoplay).
            env_var: Environment variable that overrides the autoplay
                pause duration.
            default: Autoplay pause duration in seconds when ``env_var``
                is unset.
        """
        if self.options.autoplay:
            self._pause(env_var, default)
            return
        try:
            self.console.input(Text("> ", style=f"bold {prompt_style}").markup)
        except (EOFError, KeyboardInterrupt):
            pass

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    def _log(self, line: Text) -> None:
        """Append a styled line and trim to ``LOG_MAX``.

        Every line is also mirrored, as plain text, to the
        ``contrai_engine.view.events`` logger at DEBUG level — an
        uncapped narrative history for the debug log file, independent
        of the ``LOG_MAX``-capped list kept here for the on-screen panel.
        """
        self.event_log.append(line)
        if len(self.event_log) > self.LOG_MAX:
            del self.event_log[: len(self.event_log) - self.LOG_MAX]
        logger.debug("%s", line.plain)

    def _format_bid_log(self, player: BasePlayer, bid: Bid) -> Text:
        """Build the log line for a single bid action."""
        label = _position_short(player.position)
        color = _position_color(player.position)
        t = Text()
        t.append(f"{label} ", style=f"bold {color}")
        if isinstance(bid, PassBid):
            t.append("passed.", style=DIM)
        elif isinstance(bid, RedoubleBid):
            t.append("redoubled.", style=f"bold {GOLD}")
        elif isinstance(bid, DoubleBid):
            t.append("doubled.", style=f"bold {GOLD}")
        elif isinstance(bid, ContractBid):
            t.append(f"bid {bid.value} ", style=FG)
            t.append(_suit_glyph(bid.suit), style=_suit_color(bid.suit))
            t.append(".", style=FG)
        return t

    def _format_card_log(self, player: BasePlayer, card: Card) -> Text:
        """Build the log line for a single card play."""
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
        """Build the log line for a completed trick: winner and points."""
        label = _position_short(winner.position)
        color = _position_color(winner.position)
        t = Text()
        t.append(f"{label} ", style=f"bold {color}")
        t.append(f"wins trick ({trick_points} pts).", style=f"bold {GOLD}")
        return t

