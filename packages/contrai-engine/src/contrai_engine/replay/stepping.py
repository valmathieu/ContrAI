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
BACK_KEY = "p"
OUT_KEY = "q"


class StepMode(Enum):
    """How far the replay runs before it stops again."""

    ACTION = "action"
    TRICK = "trick"
    ROUND = "round"

    def __str__(self) -> str:
        """Render as the mode token, e.g. ``"trick"``.

        Returns:
            The mode's value.
        """

        return self.value


#: Which mode each continuing key selects. ``p`` and ``q`` leave the
#: round instead of setting a mode, so neither appears here.
_MODES: dict[str, StepMode] = {
    NEXT_KEY: StepMode.ACTION,
    TRICK_KEY: StepMode.TRICK,
    ROUND_KEY: StepMode.ROUND,
}


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
                answer ``show_replay_deal(round_)`` and
                ``show_replay_step(can_go_back=...)``.
            resume_at: How many stops to pass through in silence before
                prompting — how a restarted round returns to where it
                was.
        """

        self._inner = inner
        self._resume_at = resume_at
        self._stops = 0
        self._mode = StepMode.ACTION
        self.quiet = True

    @property
    def stops(self) -> int:
        """How many stop points this round has reached so far."""

        return self._stops

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

        if self.quiet:
            return
        self._inner.on_round_dealt(round_)
        self._inner.show_replay_deal(round_)
        self._stop()

    def on_bid_made(self, player: Any, bid: Any, history: Any) -> None:
        """Forward the bid frame, then stop.

        Args:
            player: The seat that bid.
            bid: Its bid.
            history: The auction so far.
        """

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
        if self._mode is StepMode.ROUND:
            return
        if self._mode is StepMode.TRICK and not trick_end:
            return
        key = self._inner.show_replay_step(can_go_back=self._stops > 1)
        if key == OUT_KEY:
            raise ReplayInterrupt(None)
        if key == BACK_KEY:
            # Stopped at stop N, the viewer wants N-1 honoured — so the
            # next attempt passes N-2 of them in silence.
            raise ReplayInterrupt(max(self._stops - 2, 0))
        self._mode = _MODES.get(key, StepMode.ACTION)
