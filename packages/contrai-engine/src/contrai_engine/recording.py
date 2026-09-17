"""Record a played game to a ``contrai-data`` JSONL file.

The engine notifies exactly one duck-typed view, and every notification
hook is guarded by ``hasattr``. :class:`RecordingView` exploits both: it
*decorates* whatever view the CLI is driving, forwarding every attribute
it does not intercept in both directions, and turns the hooks that carry
facts into record events. Because ``main()`` holds the wrapper, the two
hooks the CLI issues itself — ``on_round_complete`` and ``show_end_game``
— are captured alongside the ones the model fires, and ``--autoplay``
records for exactly the same reason a human game does.

Three hooks are deliberately *not* intercepted and simply fall through
``__getattr__``: ``on_all_pass_redeal``, ``on_contract_established`` and
``on_trick_complete``. Each carries a fact the record format defines as
derived — an all-pass is a ``round_dealt`` followed by four passes and
the next ``round_dealt``, the contract folds out of the auction, and a
trick winner comes from ``TrickRecord.winner`` — and a derived fact
stored twice is a fact that can disagree with itself.

This is the only module in ``contrai_engine`` that imports
``contrai_data``.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Final

from contrai_core import Card, Rank, TeamSide
from contrai_data import (
    FORMAT,
    BeloteHeld,
    BidMade,
    CardPlayed,
    ContractTerms,
    EndReason,
    GameEnded,
    GameStarted,
    HandsDerivation,
    Header,
    RecordSource,
    RecordWriter,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    Ruleset,
    ScoreSource,
    Seat,
    SeatKind,
    SideMark,
    SlamOutcome,
    game_path,
    new_game_id,
    records_root,
)

from .model.player.levels import AI_LEVELS
from .model.round import marked_components
from .options import TableAids

logger = logging.getLogger(__name__)

#: Sentinel for "the ``--record`` flag was absent", as opposed to "it was
#: given with no directory" (``None``). A plain default cannot tell the two
#: apart, and they mean different things: absent defers to the knob, bare
#: overrides it.
UNSET: Final[Any] = object()

#: Attributes :class:`RecordingView` keeps for itself. Everything else,
#: read or written, belongs to the view underneath.
_OWN_PREFIX = "_record_"


def _utc_now() -> str:
    """The current instant as the ISO-8601 UTC string the format wants.

    Returns:
        The instant, e.g. ``2026-09-11T14:03:57Z``.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generator() -> str:
    """This build's producer string for a record header.

    Returns:
        ``contrai-engine <version>``, or the bare package name when the
        distribution metadata is not installed.
    """
    try:
        return f"contrai-engine {version('contrai-engine')}"
    except PackageNotFoundError:  # pragma: no cover - installed in the workspace
        return "contrai-engine"


def _ai_level(player: Any) -> str:
    """The :data:`AI_LEVELS` key this player's strategies were built from.

    Args:
        player: An AI seat.

    Returns:
        The registered level name, or ``"custom"`` for a hand-mixed pair
        of strategies that no level registers.
    """
    pair = (type(player.bidding), type(player.cardplay))
    for name, registered in AI_LEVELS.items():
        if tuple(registered) == pair:
            return name
    return "custom"


def _seat(player: Any) -> Seat:
    """The record's description of one seat.

    Engine records carry no identity: ``id`` and ``account`` are the
    site's, and the display name is deliberately dropped — a seat picker
    could one day let a person type their own, and a record is a corpus
    entry, not a scoreboard.

    Args:
        player: The player occupying the seat.

    Returns:
        The :class:`~contrai_data.Seat` for that player.
    """
    if player.is_human:
        return Seat(
            id=None, name="human", account=None, kind=SeatKind.HUMAN, level=None
        )
    level = _ai_level(player)
    return Seat(
        id=None, name=f"ai:{level}", account=None, kind=SeatKind.AI, level=level
    )


def _outcome(contract_made: bool | None) -> RoundOutcome:
    """Translate ``RoundScore.contract_made`` into a record outcome.

    Args:
        contract_made: The round's made/failed signal — ``None`` when the
            round was passed out.

    Returns:
        The matching :class:`~contrai_data.RoundOutcome`.
    """
    if contract_made is None:
        return RoundOutcome.ALL_PASS
    return RoundOutcome.MADE if contract_made else RoundOutcome.FAILED


def _slam(contract: Any, score: Any) -> SlamOutcome:
    """Which Slam, if any, this round was.

    A bid Slam outranks a swept one: the declarer announced it, so that
    is what the round *was*, whatever the sweep looked like afterwards.

    Args:
        contract: The round's contract, or ``None`` on an all-pass.
        score: The round's :class:`RoundScore`.

    Returns:
        The matching :class:`~contrai_data.SlamOutcome`.
    """
    if contract is not None:
        if contract.is_solo_slam():
            return SlamOutcome.SOLO_SLAM
        if contract.is_slam():
            return SlamOutcome.SLAM
    if score.unannounced_slam is not None:
        return SlamOutcome.UNANNOUNCED
    return SlamOutcome.NONE


@dataclasses.dataclass(frozen=True, slots=True)
class RecordRequest:
    """What the ``--record`` / ``--no-record`` flags asked for.

    Held apart from the resolved root because the knob it defers to lives
    on the table setup, which the landing screen can still change. The
    CLI keeps the request and re-resolves it after every landing screen,
    so toggling the knob takes effect on the next deal.

    Attributes:
        directory: The records root ``--record`` named, ``None`` for a
            bare ``--record`` (the default root), or :data:`UNSET` when
            the flag was absent.
        disabled: Whether ``--no-record`` was given.
    """

    directory: Any = UNSET
    disabled: bool = False

    def resolve(self, aids: TableAids) -> Path | None:
        """Where this run writes its records, or ``None`` when it does not.

        Precedence is flag > knob > off: an explicit flag always wins,
        an absent one defers to ``aids.record``, and the default is off.

        Args:
            aids: The interface aids of the table about to be dealt.

        Returns:
            The records root, or ``None`` when this run does not record.
        """
        if self.disabled:
            return None
        if self.directory is not UNSET:
            return (
                Path(self.directory) if self.directory is not None else records_root()
            )
        return records_root() if aids.record else None


class RecordingView:
    """A view decorator that writes the game it is shown to a record.

    Args:
        inner: The view to wrap. Any object will do — the wrapper reads
            nothing off it and forwards only what it is given.
        root: The records root. The file lands at
            ``<root>/games/<game_id>.jsonl``.
        preset: The name of the ruleset the game is played under, as the
            record's ``ruleset.preset`` label.
        clock: Returns the current instant as an ISO-8601 UTC string.
            Injected so a test can pin timestamps.
    """

    def __init__(
        self,
        inner: Any,
        root: Path | str,
        *,
        preset: str = "classic",
        clock: Callable[[], str] | None = None,
    ) -> None:
        object.__setattr__(self, "_record_inner", inner)
        object.__setattr__(self, "_record_root", Path(root))
        object.__setattr__(self, "_record_preset", preset)
        object.__setattr__(self, "_record_clock", clock or _utc_now)
        object.__setattr__(self, "_record_writer", None)
        object.__setattr__(self, "_record_round", 0)
        object.__setattr__(self, "_record_cards", 0)
        object.__setattr__(self, "_record_belotes", set())
        object.__setattr__(self, "_record_totals", None)

    # --- transparency ----------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Forward anything this wrapper does not define to the inner view.

        Args:
            name: The attribute being read.

        Returns:
            The inner view's attribute of that name.

        Raises:
            AttributeError: For a dunder lookup, which must not be
                answered by proxy, or when the inner view has no such
                attribute either.
        """
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "_record_inner"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep the wrapper's own state; hand everything else to the view.

        ``main()`` re-points ``view.aids`` after every landing screen. A
        plain ``__setattr__`` would land that on the wrapper and leave the
        real view reading a stale aid, with nothing looking wrong.

        Args:
            name: The attribute being written.
            value: The value to write.
        """
        if name.startswith(_OWN_PREFIX):
            object.__setattr__(self, name, value)
        else:
            setattr(self._record_inner, name, value)

    def _record_forward(self, name: str, *args: Any, **kwargs: Any) -> None:
        """Pass a push hook on, if the inner view has it.

        The engine guards each hook with ``hasattr``. Defining one here
        makes that guard pass whatever the inner view is, so the forward
        has to re-ask the question the engine no longer can.

        Args:
            name: The hook's name.
            *args: Positional arguments to pass on.
            **kwargs: Keyword arguments to pass on.
        """
        hook = getattr(self._record_inner, name, None)
        if hook is not None:
            hook(*args, **kwargs)

    def _record_write(self, event: Any) -> None:
        """Append one event, unless no game is open.

        Args:
            event: The record event to append.
        """
        writer = self._record_writer
        if writer is not None:
            writer.write(event)

    # --- lifecycle -------------------------------------------------------

    def attach(self, game: Any, target_score: int) -> None:
        """Open a record for ``game``, then attach the inner view.

        Args:
            game: The game about to be played.
            target_score: The score a side must reach to win it.
        """
        self.close_record(EndReason.INTERRUPTED)
        game_id = new_game_id("engine")
        self._record_writer = RecordWriter(game_path(self._record_root, game_id))
        self._record_round = 0
        self._record_cards = 0
        self._record_belotes = set()
        self._record_totals = None
        now = self._record_clock()
        self._record_write(
            Header(
                format=FORMAT,
                source=RecordSource.ENGINE,
                generator=_generator(),
                game_id=game_id,
                created_at=now,
            )
        )
        self._record_write(
            GameStarted(
                ruleset=Ruleset(preset=self._record_preset, config=game.rules),
                seats={player.position: _seat(player) for player in game.players},
                observed_from=None,
                ts=now,
            )
        )
        self._record_forward("attach", game, target_score)

    def close_record(
        self,
        reason: EndReason = EndReason.INTERRUPTED,
        *,
        status: Any = None,
    ) -> None:
        """Close the open record with a ``game_ended``, if one is open.

        Idempotent: a second call after the game ended does nothing, which
        is what lets ``main()`` close defensively in a ``finally`` without
        knowing whether the game finished.

        Args:
            reason: Why the record stops.
            status: The ``GameOverStatus`` when the game ended properly —
                its ``final_scores`` and ``winner`` beat the running
                totals the wrapper has been tracking.
        """
        writer = self._record_writer
        if writer is None:
            return
        totals = status.final_scores if status is not None else self._record_totals
        writer.write(
            GameEnded(
                totals=dict(totals) if totals is not None else None,
                winner=status.winner if status is not None else None,
                reason=reason,
                ts=self._record_clock(),
            )
        )
        writer.close()
        self._record_writer = None

    def show_end_game(self, status: Any) -> str:
        """Close the record, then show the end-game screen.

        Args:
            status: The game's final :class:`GameOverStatus`.

        Returns:
            Whatever the inner view's own ``show_end_game`` returned.
        """
        self.close_record(EndReason.TARGET_REACHED, status=status)
        return self._record_inner.show_end_game(status)

    # --- fact-carrying hooks --------------------------------------------

    def on_round_dealt(self, round_: Any) -> None:
        """Record the deal and reset the per-round counters.

        Args:
            round_: The round just dealt.
        """
        self._record_round = round_.round_number
        self._record_cards = 0
        self._record_belotes = set()
        self._record_write(
            RoundDealt(
                round=round_.round_number,
                dealer=round_.dealer.position,
                hands={
                    player.position: tuple(player.hand.cards)
                    for player in round_.players_order
                },
                hands_derivation=HandsDerivation.SELF_PLAY,
                ts=self._record_clock(),
            )
        )
        self._record_forward("on_round_dealt", round_)

    def on_bid_made(self, player: Any, bid: Any, history: list) -> None:
        """Record one bid, re-seated onto its :class:`Position`.

        A live ``Bid`` names the ``Player`` who made it; a record names
        the seat, so the bid is rebuilt on the position. ``history`` is
        the auction *after* the bid was applied, which makes its length
        the gapless 1-based sequence the format asks for.

        Args:
            player: The seat that bid.
            bid: The bid just applied.
            history: The auction's bids, the new one included.
        """
        self._record_write(
            BidMade(
                round=self._record_round,
                seq=len(history),
                position=player.position,
                bid=dataclasses.replace(bid, player=player.position),
                think_ms=None,
                ts=self._record_clock(),
            )
        )
        self._record_forward("on_bid_made", player, bid, history)

    def on_card_played(self, player: Any, card: Any, plays: Any) -> None:
        """Record one card, numbering the trick off the cards seen so far.

        Args:
            player: The seat that played.
            card: The card played.
            plays: The trick as it stands after the play.
        """
        self._record_write(
            CardPlayed(
                round=self._record_round,
                trick=self._record_cards // 4 + 1,
                position=player.position,
                card=card,
                derived=False,
                think_ms=None,
                ts=self._record_clock(),
            )
        )
        self._record_cards += 1
        self._record_forward("on_card_played", player, card, plays)

    def on_belote_announced(
        self, player: Any, kind: str, suit: Any, round_: Any
    ) -> None:
        """Record a King + Queen pair, once.

        The hook fires twice per pair — Belote then Rebelote — and the
        record has one event per pair, so the second firing is dropped.

        Args:
            player: The seat holding the pair.
            kind: Which leg fired, ``"belote"`` or ``"rebelote"``.
            suit: The pair's suit.
            round_: The round in progress.
        """
        pair = (player.position, suit)
        if pair not in self._record_belotes:
            self._record_belotes.add(pair)
            self._record_write(
                BeloteHeld(
                    round=self._record_round,
                    position=player.position,
                    cards=(Card(suit, Rank.KING), Card(suit, Rank.QUEEN)),
                    announced=True,
                    ts=self._record_clock(),
                )
            )
        self._record_forward("on_belote_announced", player, kind, suit, round_)

    def on_round_complete(self, round_: Any, running_scores: dict) -> None:
        """Record the round's score line, then pass the hook on.

        Args:
            round_: The round just finished.
            running_scores: The running totals after it.
        """
        self._record_totals = dict(running_scores)
        self._record_score(round_, running_scores)
        self._record_forward("on_round_complete", round_, running_scores)

    def _record_score(self, round_: Any, running_scores: dict) -> None:
        """Write the ``round_scored`` line for a scored round.

        Args:
            round_: The round just finished.
            running_scores: The running totals after it.
        """
        score = round_.round_score
        if score is None:
            logger.warning(
                "round %s completed without a score; no round_scored written",
                round_.round_number,
            )
            return
        contract = round_.contract
        self._record_write(
            RoundScored(
                round=round_.round_number,
                outcome=_outcome(score.contract_made),
                declarer=contract.player.position if contract is not None else None,
                contract=(
                    ContractTerms(
                        value=contract.value,
                        suit=contract.suit,
                        multiplier=score.multiplier,
                    )
                    if contract is not None
                    else None
                ),
                taken=dict(score.card_points),
                belote=dict(score.belote_points),
                announcements={side: 0 for side in TeamSide},
                carried_over={side: 0 for side in TeamSide},
                marked={
                    # What the sheet would carry, multiplier included: the
                    # scorer works in components, a record states the figures.
                    side: SideMark(
                        *marked_components(
                            mark, score.multiplier, round_.rules
                        )
                    )
                    for side, mark in score.marks.items()
                },
                totals=dict(running_scores),
                last_trick=score.last_trick_side,
                slam=_slam(contract, score),
                source=ScoreSource.ENGINE,
                ts=self._record_clock(),
            )
        )


def finish_recording(view: Any) -> None:
    """Close ``view``'s open record, if it has one.

    A no-op for a plain view, so ``main()`` can call it unconditionally
    on the way out without branching on whether this run records.

    Args:
        view: The object ``main()`` has been driving.
    """
    if isinstance(view, RecordingView):
        view.close_record(EndReason.INTERRUPTED)
