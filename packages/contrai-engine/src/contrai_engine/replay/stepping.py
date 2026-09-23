"""Turn a replayed game into one the keyboard walks through.

:class:`SteppingView` is a decorator over whatever view a replay is
rendering to, in the shape
:class:`~contrai_engine.recording.RecordingView` established: it forwards
every attribute it does not intercept, and the hooks it does intercept it
forwards *first* and then blocks on.

**Why it stops after the action rather than before it.** Spec §5.2
imagined the recorded seats blocking inside ``choose_bid`` /
``choose_card``, which cannot work: those methods hold no reference to a
view, and the engine's pull hooks (``request_bid_action`` /
``request_card_action``) are reached only for a seat whose ``is_human`` is
true — which a :class:`~contrai_engine.replay.player.RecordedPlayer`
never is. The push hooks are the only seam, and they turn out to be the
better one: each stop shows the action that just landed, which is the
frame a viewer wants to read.

**Why going back is a restart.** The engine is forward-only — ``Round``
keeps only the latest ``PlayState`` — so there is no earlier frame to
return to. There does not need to be: a ``RecordedPlayer`` derives its
action index from live engine state rather than from a cursor, so
replaying the round again from its recorded deal reproduces it exactly,
and ``resume_at`` simply says how many stops to pass through in silence.
A round replays in milliseconds, which is what makes ``p`` affordable.

**What ``quiet`` is for.** Picking round *k* out of a record means
replaying the rounds before it, because
:class:`~contrai_engine.replay.deal.ScriptedDealSource` indexes on the
game's own round counter and cannot seek. Those rounds must not reach the
screen, so while ``quiet`` is set the wrapper forwards nothing — except
``attach`` and ``on_round_complete``, which render nothing and carry
state the stepped round needs: the attached game, and the reset of the
view's one-trick history.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

#: The keys :meth:`SteppingView._stop` understands, as the view returns
#: them — the same bare-string convention ``RichView.show_end_game``
#: already answers the end-game screen with.
NEXT_KEY = "n"
TRICK_KEY = "t"
ROUND_KEY = "r"
AUCTION_KEY = "a"
GRID_KEY = "g"
BACK_KEY = "p"
OUT_KEY = "q"


class StepMode(Enum):
    """How far the replay runs before it stops again."""

    ACTION = "action"
    TRICK = "trick"
    ROUND = "round"
    AUCTION = "auction"

    def __str__(self) -> str:
        """Render as the mode token, e.g. ``"trick"``.

        Returns:
            The mode's value.
        """

        return self.value


#: Which mode each continuing key selects. ``p`` and ``q`` leave the
#: round instead of setting a mode, and ``g`` never reaches the gate —
#: :func:`read_step_key` serves it — so none of the three appears here.
_MODES: dict[str, StepMode] = {
    NEXT_KEY: StepMode.ACTION,
    TRICK_KEY: StepMode.TRICK,
    ROUND_KEY: StepMode.ROUND,
    AUCTION_KEY: StepMode.AUCTION,
}


def read_step_key(
    view: Any,
    round_: Any,
    bids: Any,
    *,
    can_go_back: bool,
    can_skip_auction: bool = False,
) -> str:
    """Read the viewer's key at a stop, serving any trick-grid requests.

    ``g`` is not a way to move on: it shows the round so far as a trick
    grid, repaints the screen it was pressed on, and asks again — as
    many times as the viewer likes. So it is served here, below every
    caller, rather than returned to one: the step gate inside a round
    and the driver's own prompts after it (the recap, a divergence) all
    read their key through this.

    Args:
        view: The view to prompt on and to draw the grid with.
        round_: The round on screen, as far as it has gone.
        bids: Its auction as far as it has gone — the round's own
            ``auction`` is set only once bidding ends.
        can_go_back: Whether ``[p]`` is offered.
        can_skip_auction: Whether ``[a]`` is offered.

    Returns:
        The first key that is not ``g``.
    """

    while True:
        key = view.show_replay_step(
            can_go_back=can_go_back, can_skip_auction=can_skip_auction
        )
        if key != GRID_KEY:
            return key
        view.show_replay_grid(round_, bids)
        view.redraw_screen()


class ReplayInterrupt(Exception):
    """The viewer left the round before it ended.

    Control flow, not a fault: deliberately **not** a
    :class:`~contrai_engine.replay.exceptions.ReplayError`, because that
    family means the record and the engine stopped agreeing about what
    game is being played, and a driver catching it must not mistake a
    keypress for a divergence.

    Attributes:
        resume_at: How many stops to pass through in silence when the
            round is replayed again, or ``None`` to leave it for good.
    """

    def __init__(self, resume_at: int | None) -> None:
        """Record where, if anywhere, the round should resume.

        Args:
            resume_at: The silent-stop count for the next attempt, or
                ``None`` to abandon the round.
        """

        super().__init__(resume_at)
        self.resume_at = resume_at


class SteppingView:
    """A view that pauses a replay wherever the viewer asked it to.

    Attributes:
        quiet: While set, nothing is forwarded but ``attach`` and
            ``on_round_complete``, and no stop is counted — the state a
            round-picker needs while it replays the rounds before the one
            on screen.
    """

    def __init__(self, inner: Any, *, resume_at: int = 0) -> None:
        """Wrap ``inner`` and arm the step gate.

        Args:
            inner: The view to forward to and render through. It must
                answer ``show_replay_deal(round_)``,
                ``show_replay_contract(round_)``,
                ``show_replay_grid(round_, bids)``, ``redraw_screen()``
                and
                ``show_replay_step(can_go_back=..., can_skip_auction=...)``.
            resume_at: How many stops to pass through in silence before
                prompting — how a restarted round returns to where it
                was.
        """

        self._inner = inner
        self._resume_at = resume_at
        self._stops = 0
        self._mode = StepMode.ACTION
        # Whether the round on screen is still bidding — the only time
        # skipping the auction means anything, so the only time ``a`` is
        # offered.
        self._in_auction = False
        # What the trick grid needs at a stop: the round on screen, and
        # its bids so far — ``Round.auction`` is set only once bidding
        # ends, so a grid asked for mid-auction has to be handed them.
        self._round: Any = None
        self._bids: list[Any] = []
        self.quiet = True

    @property
    def stops(self) -> int:
        """How many stop points this round has reached so far."""

        return self._stops

    @property
    def bids(self) -> list[Any]:
        """The bids of the round on screen, as far as its auction has gone."""

        return list(self._bids)

    def __getattr__(self, name: str) -> Any:
        """Forward anything not intercepted here to the inner view.

        Args:
            name: The attribute being read.

        Returns:
            The inner view's attribute.
        """

        return getattr(self._inner, name)

    # ------------------------------------------------------------------
    # Hooks that cross the quiet gate
    # ------------------------------------------------------------------

    def attach(self, game: Any, target_score: int) -> None:
        """Forward the attach, quiet or not: it renders nothing.

        Args:
            game: The game being replayed.
            target_score: Its target.
        """

        self._inner.attach(game, target_score)

    def on_round_complete(self, round_: Any, running_scores: Any) -> None:
        """Forward the round close, quiet or not.

        It renders nothing, and it resets the view's one-trick history —
        which a stepped round needs, or it would open showing the last
        trick of the round before it.

        Args:
            round_: The round that just closed.
            running_scores: The running totals after it.
        """

        self._inner.on_round_complete(round_, running_scores)

    # ------------------------------------------------------------------
    # Hooks that stop
    # ------------------------------------------------------------------

    def on_round_dealt(self, round_: Any) -> None:
        """Show the deal face up, then stop.

        Args:
            round_: The round just dealt.
        """

        self._in_auction = True
        self._round = round_
        self._bids = []
        if self.quiet:
            return
        self._inner.on_round_dealt(round_)
        self._inner.show_replay_deal(round_)
        self._stop()

    def on_contract_established(self, round_: Any) -> None:
        """Close the auction; under ``a``, come to rest on the empty table.

        The engine fires this right after the last bid and before the
        first card, which is exactly where ``a`` promises to land. The
        stop it makes is **not counted**: it re-shows the last bid's stop
        with the contract as its prompt, so stop numbering — which ``p``
        and ``resume_at`` both count in — is the same whichever keys
        brought the viewer here, and ``p`` steps back as it would from
        the last bid.

        An all-pass round never fires this, so ``a`` there runs on to the
        round's end, the next place anything happens.

        Args:
            round_: The round whose auction just closed on a contract.
        """

        self._in_auction = False
        if self.quiet:
            return
        # Forwarded defensively: the engine asks ``hasattr`` of *this*
        # wrapper, which now always says yes, so the question of whether
        # the inner view answers has to be asked again here.
        forward = getattr(self._inner, "on_contract_established", None)
        if forward is not None:
            forward(round_)
        if self._mode is not StepMode.AUCTION:
            return
        self._inner.show_replay_contract(round_)
        self._mode = StepMode.ACTION
        self._prompt()

    def on_bid_made(self, player: Any, bid: Any, history: Any) -> None:
        """Forward the bid frame, then stop.

        Args:
            player: The seat that bid.
            bid: Its bid.
            history: The auction so far.
        """

        self._bids = list(history)
        if self.quiet:
            return
        self._inner.on_bid_made(player, bid, history)
        self._stop()

    def on_card_played(self, player: Any, card: Any, plays: Any) -> None:
        """Forward the card frame, then stop unless the trick just closed.

        A fourth card is immediately followed by
        :meth:`on_trick_complete`, whose frame names the winner. Stopping
        on both would cost five presses for four cards and show the same
        trick twice, so the trick frame is the one that stops — the same
        ``len(plays) == 4`` discrimination
        :func:`~contrai_engine.view.state_helpers._trick_index` makes.

        Args:
            player: The seat that played.
            card: The card.
            plays: The trick as it now stands.
        """

        if self.quiet:
            return
        self._inner.on_card_played(player, card, plays)
        if len(plays) == 4:
            return
        self._stop()

    def on_belote_announced(
        self, player: Any, kind: str, suit: Any, round_: Any
    ) -> None:
        """Forward the announcement frame, then stop.

        The engine fires this *after* the card that triggered it, so
        without a stop of its own the announcement would be painted over
        by the next card's frame and never read.

        Args:
            player: The announcing seat.
            kind: ``"belote"`` or ``"rebelote"``.
            suit: The pair's suit.
            round_: The round in progress.
        """

        if self.quiet:
            return
        self._inner.on_belote_announced(player, kind, suit, round_)
        self._stop()

    def on_trick_complete(
        self, plays: Any, winner: Any, round_: Any
    ) -> None:
        """Forward the trick-won frame, then stop.

        Args:
            plays: The four plays.
            winner: The seat that took them.
            round_: The round in progress.
        """

        if self.quiet:
            return
        self._inner.on_trick_complete(plays, winner, round_)
        self._stop(trick_end=True)

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def _stop(self, *, trick_end: bool = False) -> None:
        """Count this stop point and block on it if the mode says to.

        Args:
            trick_end: Whether this stop is the close of a trick, which
                is where :data:`StepMode.TRICK` comes to rest.

        Raises:
            ReplayInterrupt: If the viewer asked to step back or to leave
                the round.
        """

        self._stops += 1
        if self._stops <= self._resume_at:
            return
        # ``AUCTION`` runs like ``ROUND`` here: its resting place is not a
        # counted stop but :meth:`on_contract_established`.
        if self._mode in (StepMode.ROUND, StepMode.AUCTION):
            return
        if self._mode is StepMode.TRICK and not trick_end:
            return
        self._prompt()

    def _prompt(self) -> None:
        """Read the viewer's key at the current stop and act on it.

        Raises:
            ReplayInterrupt: If the viewer asked to step back or to leave
                the round.
        """

        # ``g`` is served inside and never comes back: it repaints this
        # stop's own frame, so neither the stop count nor the mode moves.
        key = read_step_key(
            self._inner,
            self._round,
            self._bids,
            can_go_back=self._stops > 1,
            can_skip_auction=self._in_auction,
        )
        if key == OUT_KEY:
            raise ReplayInterrupt(None)
        if key == BACK_KEY:
            # Stopped at stop N, the viewer wants N-1 honoured — so the
            # next attempt passes N-2 of them in silence.
            raise ReplayInterrupt(max(self._stops - 2, 0))
        self._mode = _MODES.get(key, StepMode.ACTION)
