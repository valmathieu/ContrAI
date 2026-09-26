"""Check a record by replaying it, and say what disagreed.

The verifier is the **recorder's mirror**. ``RecordingView`` intercepts
the engine's view hooks and writes down what it sees;
:class:`VerifyingObserver` intercepts the same hooks and, instead of
writing, compares what it sees against what the record claims. Nothing
here re-implements a rule: bid and card legality come free from
``Auction.apply`` and ``PlayState.apply`` raising, the trick winner is
``TrickRecord.winner`` on both sides, and the score is ``score_round``.

The five checks of spec §5.4, and where each one's truth comes from:

=================  ===========================================
class              source of truth
=================  ===========================================
``illegal_bid``    ``Auction.apply`` refuses the recorded bid
``illegal_play``   ``PlayState.apply`` refuses the recorded card
``trick_winner``   core's winner rule vs the record's next leader
``belote``         the dealt hands, and the replay's announcements
``score``          ``score_round`` vs the ``round_scored`` line
=================  ===========================================

Two of those deserve a word.

**A trick-winner disagreement surfaces as a seat mismatch.** If the
record's trick 4 is led by a seat core says did not win trick 3, the
replay asks that seat for a card out of turn and
:class:`~contrai_engine.replay.exceptions.SeatMismatchError` fires at the
first card of the next trick. Comparing winners directly at the end of a
round would never catch it — by then the replay has already diverged.

**An illegal action ends that round's replay.** The engine state has
diverged from the record's, so every later check in that round would be
comparing two different games. The round is reported ``suspect`` with
what was found, and the next round starts from its own recorded deal.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contrai_core import PRESETS, Card, Position, Rank, Suit, TeamSide
from contrai_core.bid import SlamLevel
from contrai_core.exceptions import IllegalBidError, IllegalPlayError
from contrai_data import (
    BeloteHeld,
    GameRecord,
    RoundRecord,
    SlamOutcome,
    load_game,
)

from ..model.round import marked_components
from ..recording import _outcome, _slam
from .controller import ReplayController
from .exceptions import ReplayError, ScriptExhaustedError, SeatMismatchError
from .verdict import (
    GameVerdict,
    Mismatch,
    MismatchKind,
    RoundVerdict,
    Verdict,
    write_verdict,
)

#: The phase a round is in, which decides how an exception is classified.
_BIDDING = "bidding"
_PLAY = "play"

#: The check names that can end up ``unchecked``. They are the JSON
#: tokens of :class:`MismatchKind`, so a reader never has to map between
#: two vocabularies for the same check.
_SCORE = str(MismatchKind.SCORE)


def _side_totals(mapping: Any) -> dict[str, int]:
    """A per-side mapping rendered for a message.

    Args:
        mapping: Anything keyed by :class:`~contrai_core.TeamSide`.

    Returns:
        The same values keyed by the side's token, in ``TeamSide`` order
        so two renderings of the same data compare equal as strings.
    """

    return {side.value: mapping.get(side, 0) for side in TeamSide}


@dataclass
class _RoundCheck:
    """Everything one round's checks accumulate before a verdict.

    Attributes:
        record: The recorded round being checked.
        phase: Which phase the replay reached, so an exception can be
            classified.
        announced: The (seat, suit) belote pairs the replay announced.
        mismatches: What disagreed, in the order it was found.
        unchecked: Checks that had nothing to run against.
    """

    record: RoundRecord
    phase: str = _BIDDING
    announced: set[tuple[Position, Suit]] = field(default_factory=set)
    mismatches: list[Mismatch] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)

    def fault(self, kind: MismatchKind, detail: str, **fields: Any) -> None:
        """Record one disagreement.

        Args:
            kind: Which class it belongs to.
            detail: What disagreed, in one sentence.
            **fields: Any of ``position`` / ``trick`` / ``seq`` /
                ``expected`` / ``observed``.
        """

        self.mismatches.append(Mismatch(kind=kind, detail=detail, **fields))


class VerifyingObserver:
    """Compares a replayed game against the record it came from.

    Plugged in wherever a view goes, so the engine notifies it through
    exactly the hooks a live game uses — which is what keeps it honest:
    an observer that had to be handed special state would be checking
    something other than what the engine did.

    Attributes:
        record: The record under verification.
        rounds: The per-round verdicts, in file order, as they are
            decided.
        notes: Non-fatal observations about the record as a whole.
    """

    def __init__(self, record: GameRecord) -> None:
        """Verify against ``record``.

        Args:
            record: The record the replay is being compared to.
        """

        self.record = record
        self.rounds: list[RoundVerdict] = []
        self.notes: list[str] = list(_ruleset_notes(record))
        self._current: _RoundCheck | None = None

    # --- driven by the verifier, not by the engine -----------------------

    def expect(self, round_: RoundRecord) -> None:
        """Open the checks for the recorded round about to be replayed.

        The pairing is handed in rather than looked up, because the two
        numberings are not the same: ``Game.round_number`` counts the
        rounds *replayed*, while a record's round numbers are its
        source's deal count — which may start above one and may skip.
        One incomplete round in the middle is enough to put a lookup by
        number permanently off by one, and nothing about the result would
        look wrong.

        Args:
            round_: The recorded round the next ``manage_round`` replays.
        """

        self._current = _RoundCheck(round_)

    # --- hooks ----------------------------------------------------------

    def on_contract_established(self, round_: Any) -> None:
        """Note that bidding closed, so later faults are play faults.

        Args:
            round_: The engine round, contract now fixed.
        """

        if self._current is not None:
            self._current.phase = _PLAY

    def on_belote_announced(
        self, player: Any, kind: str, suit: Any, round_: Any
    ) -> None:
        """Note a belote leg the replay announced.

        The hook fires twice per pair — Belote then Rebelote — and a set
        makes the second firing free.

        Args:
            player: The seat holding the pair.
            kind: Which leg fired.
            suit: The pair's suit.
            round_: The round in progress.
        """

        if self._current is not None:
            self._current.announced.add((player.position, suit))

    def on_round_complete(self, round_: Any, running_scores: Any) -> None:
        """Run every end-of-round check and decide the round's verdict.

        Args:
            round_: The engine round, played out and scored.
            running_scores: The running totals after it.
        """

        check = self._current
        if check is None:
            return
        _check_auction(check, round_)
        _check_trick_winners(check, round_)
        _check_belote(check, round_)
        _check_score(check, round_)
        self.rounds.append(
            RoundVerdict.decide(
                check.record.number,
                tuple(check.mismatches),
                tuple(check.unchecked),
            )
        )
        self._current = None

    def round_failed(self, round_: RoundRecord, exc: Exception) -> None:
        """Report a round whose replay stopped on an illegal action.

        Args:
            round_: The recorded round that failed.
            exc: What the engine or the driver raised.
        """

        check = self._current or _RoundCheck(round_)
        kind, detail = _classify(check.phase, exc)
        check.fault(kind, detail)
        self.rounds.append(
            RoundVerdict.decide(
                round_.number, tuple(check.mismatches), tuple(check.unchecked)
            )
        )
        self._current = None

    def round_skipped(self, round_: RoundRecord) -> None:
        """Report a round the record left structurally unfinished.

        Nothing is wrong with it — there is simply not enough of it to
        replay, which is what ``partial`` means.

        Args:
            round_: The recorded round that was not replayed.
        """

        self.rounds.append(
            RoundVerdict.decide(
                round_.number,
                unchecked=tuple(str(k) for k in MismatchKind),
                replayed=False,
            )
        )

    def finish(self) -> GameVerdict:
        """The whole record's verdict.

        Returns:
            The rounds decided so far, rolled up.
        """

        return GameVerdict(
            game_id=self.record.header.game_id,
            source=str(self.record.header.source),
            preset=self.record.preset,
            rounds=tuple(self.rounds),
            notes=tuple(self.notes),
        )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(phase: str, exc: Exception) -> tuple[MismatchKind, str]:
    """Which mismatch class an exception belongs to, and how to say it.

    Args:
        phase: The phase the round had reached.
        exc: What was raised.

    Returns:
        The class and a one-sentence detail.
    """

    if isinstance(exc, IllegalBidError):
        return MismatchKind.ILLEGAL_BID, f"the auction refused a recorded bid: {exc}"
    if isinstance(exc, IllegalPlayError):
        return (
            MismatchKind.ILLEGAL_PLAY,
            f"the play state refused a recorded card: {exc}",
        )
    if isinstance(exc, SeatMismatchError):
        if phase == _PLAY:
            # The record's next leader is not the seat core says won the
            # trick before it — which is what a winner disagreement looks
            # like from inside a replay.
            return (
                MismatchKind.TRICK_WINNER,
                f"the record's next card is not the seat core says leads: {exc}",
            )
        return MismatchKind.ILLEGAL_BID, f"a bid was recorded for another seat: {exc}"
    if isinstance(exc, ScriptExhaustedError):
        kind = (
            MismatchKind.ILLEGAL_PLAY if phase == _PLAY else MismatchKind.ILLEGAL_BID
        )
        return kind, f"the replay ran past the record: {exc}"
    return MismatchKind.ILLEGAL_PLAY, f"the replay stopped: {exc}"


# ---------------------------------------------------------------------------
# The end-of-round checks
# ---------------------------------------------------------------------------


def _check_auction(check: _RoundCheck, round_: Any) -> None:
    """Every recorded bid must appear in the replayed auction, in order.

    Compared **by consulted seat, never by sequence position**: the
    engine writes forced passes an observed table never transmits — the
    doubling side after a double, the partner of a Slam bidder, all four
    seats after a redouble — so the replayed auction may be longer than
    the recorded one. What must hold is that the record is a subsequence
    of it and that every extra bid is one of those forced passes.

    Args:
        check: The round's accumulating checks.
        round_: The engine round, auction closed.
    """

    auction = round_.auction
    replayed = [] if auction is None else list(auction.bids)
    recorded = list(check.record.auction)
    cursor = 0
    for index, want in enumerate(recorded, start=1):
        while cursor < len(replayed) and not _same_bid(replayed[cursor], want):
            extra = replayed[cursor]
            if type(extra).__name__ != "PassBid":
                check.fault(
                    MismatchKind.ILLEGAL_BID,
                    "the replayed auction holds a bid the record does not",
                    seq=index,
                    position=str(_seat_of(extra)),
                    observed=str(extra),
                )
                return
            cursor += 1
        if cursor >= len(replayed):
            check.fault(
                MismatchKind.ILLEGAL_BID,
                "a recorded bid never reached the replayed auction",
                seq=index,
                position=str(want.player),
                expected=str(want),
            )
            return
        cursor += 1


def _same_bid(replayed: Any, recorded: Any) -> bool:
    """Whether two bids are the same bid, one seated on a player.

    Args:
        replayed: A bid out of the replayed auction, seated on a live
            player.
        recorded: A bid out of the record, seated on a bare position.

    Returns:
        Whether they are the same kind of bid, by the same seat, with the
        same terms.
    """

    if type(replayed) is not type(recorded):
        return False
    if _seat_of(replayed) is not recorded.player:
        return False
    return all(
        getattr(replayed, name, None) == getattr(recorded, name, None)
        for name in ("value", "suit")
    )


def _seat_of(bid: Any) -> Position | None:
    """The seat a bid was made from, however the bid is seated.

    Args:
        bid: A bid seated on a player or on a position.

    Returns:
        The seat.
    """

    return getattr(bid.player, "position", bid.player)


def _check_trick_winners(check: _RoundCheck, round_: Any) -> None:
    """The replayed trick winners must be the record's.

    A cheap cross-check rather than the real detector — a winner
    disagreement normally stops the replay at the next trick's first card
    (see :func:`_classify`). What this catches is the case where the
    replay finished but produced a different number of tricks than the
    record holds.

    Args:
        check: The round's accumulating checks.
        round_: The engine round, played out.
    """

    state = round_.play_state
    replayed = (
        ()
        if state is None
        else tuple(winner.position for winner in state.trick_winners)
    )
    recorded = check.record.trick_winners
    if replayed == recorded:
        return
    for index, (got, want) in enumerate(zip(replayed, recorded), start=1):
        if got is not want:
            check.fault(
                MismatchKind.TRICK_WINNER,
                "the replayed trick was won by another seat",
                trick=index,
                expected=str(got),
                observed=str(want),
            )
            return
    check.fault(
        MismatchKind.TRICK_WINNER,
        f"the replay completed {len(replayed)} trick(s), the record holds "
        f"{len(recorded)}",
        expected=str(len(replayed)),
        observed=str(len(recorded)),
    )


def _check_belote(check: _RoundCheck, round_: Any) -> None:
    """Every belote the record claims must be one the deal supports.

    Two rules, and the asymmetry between them is deliberate.

    A pair the record *claims* must be in the dealt hands, and one it
    marks ``announced`` must have been announced in the replay — a record
    cannot invent either.

    A pair the engine *derives* that the record never mentions is **not**
    a mismatch: an observed table may simply not transmit it. If it moved
    the marks, the score check catches it, and if it did not, there is
    nothing to report.

    Args:
        check: The round's accumulating checks.
        round_: The engine round, played out.
    """

    hands = check.record.hands
    for belote in check.record.belotes:
        if not _deal_supports(hands, belote):
            check.fault(
                MismatchKind.BELOTE,
                "the record claims a belote the dealt hand does not hold",
                position=str(belote.position),
                observed=", ".join(str(card) for card in belote.cards),
            )
            continue
        if belote.announced and (belote.position, belote.cards[0].suit) not in (
            check.announced
        ):
            check.fault(
                MismatchKind.BELOTE,
                "the record marks a belote announced that the replay never "
                "announced",
                position=str(belote.position),
                observed=str(belote.cards[0].suit),
            )


def _deal_supports(hands: Any, belote: BeloteHeld) -> bool:
    """Whether the dealt hand holds the King and Queen the pair names.

    Args:
        hands: The round's dealt hands, by seat.
        belote: The claimed pair.

    Returns:
        Whether that seat was dealt both cards.
    """

    held = set(hands.get(belote.position, ()))
    suit = belote.cards[0].suit
    return {Card(suit, Rank.KING), Card(suit, Rank.QUEEN)} <= held


def _check_score(check: _RoundCheck, round_: Any) -> None:
    """The replayed score line must be the recorded one, field by field.

    When the record carries no ``round_scored`` event there is nothing to
    check against, and the round is ``partial`` rather than ``verified``.
    That is the common case in an observed game, not an edge case: the
    acceptance corpus has two score lines across fourteen rounds.

    Args:
        check: The round's accumulating checks.
        round_: The engine round, scored.
    """

    recorded = check.record.score
    if recorded is None:
        check.unchecked.append(_SCORE)
        return

    score = round_.round_score
    if score is None:
        check.fault(
            MismatchKind.SCORE,
            "the record scores a round the replay did not",
            observed=str(recorded.outcome),
        )
        return

    contract = round_.contract
    outcome = _outcome(score)
    if outcome is not recorded.outcome:
        check.fault(
            MismatchKind.SCORE,
            "the round resolved differently",
            expected=str(outcome),
            observed=str(recorded.outcome),
        )

    slam = _slam(contract, score)
    if slam is not recorded.slam:
        check.fault(
            MismatchKind.SCORE,
            "the round's slam classification differs",
            expected=str(slam),
            observed=str(recorded.slam),
        )

    declarer = contract.player.position if contract is not None else None
    if declarer is not recorded.declarer:
        check.fault(
            MismatchKind.SCORE,
            "the declaring seat differs",
            expected=str(declarer),
            observed=str(recorded.declarer),
        )

    _check_contract_terms(check, contract, score, recorded)

    marks = {
        side: marked_components(mark, score.multiplier, round_.rules)
        for side, mark in score.marks.items()
    }
    wanted = {
        side: (mark.made, mark.announced)
        for side, mark in recorded.marked.items()
    }
    if marks != wanted:
        check.fault(
            MismatchKind.SCORE,
            "the marked points differ, made and announced compared apart",
            expected=str({s.value: m for s, m in marks.items()}),
            observed=str({s.value: m for s, m in wanted.items()}),
        )

    if not _taken_agrees(
        score, recorded, slam, defense_sweeper=_defense_sweeper(round_)
    ):
        check.fault(
            MismatchKind.SCORE,
            "the captured card points differ",
            expected=str(_side_totals(score.card_points)),
            observed=str(_side_totals(recorded.taken)),
        )

    if dict(score.belote_points) != dict(recorded.belote):
        check.fault(
            MismatchKind.SCORE,
            "the belote points differ",
            expected=str(_side_totals(score.belote_points)),
            observed=str(_side_totals(recorded.belote)),
        )

    if score.last_trick_side is not recorded.last_trick:
        if recorded.last_trick is None:
            # The source named no side. An observed table may fold the
            # ten-point bonus into the row's card points and state nothing
            # else, and those points are compared above — so the bonus is
            # still checked, while the side itself was never claimed. A claim
            # never made is not a disagreement.
            check.unchecked.append(_SCORE)
        else:
            check.fault(
                MismatchKind.SCORE,
                "the last trick went to another side",
                expected=str(score.last_trick_side),
                observed=str(recorded.last_trick),
            )

    _check_carried_over(check, score, recorded)


def _check_carried_over(check: _RoundCheck, score: Any, recorded: Any) -> None:
    """The dispute pot paid out this round must be the one recorded (§7.5).

    A record that cannot say — an observed round whose running total
    before it was never read — carries ``None``, and a claim never made is
    not a disagreement: the check is left unchecked, as a missing last
    trick is, and ``score`` is named unchecked once however many of its
    parts could not be compared.

    Args:
        check: The round's accumulating checks.
        score: The replayed round score.
        recorded: The recorded score line.
    """

    if recorded.carried_over is None:
        if _SCORE not in check.unchecked:
            check.unchecked.append(_SCORE)
        return
    mine = {side: score.carried_over.get(side, 0) for side in TeamSide}
    theirs = {side: recorded.carried_over.get(side, 0) for side in TeamSide}
    if mine != theirs:
        check.fault(
            MismatchKind.SCORE,
            "the carried-over points differ",
            expected=str(_side_totals(mine)),
            observed=str(_side_totals(theirs)),
        )


def _check_contract_terms(
    check: _RoundCheck, contract: Any, score: Any, recorded: Any
) -> None:
    """The contract's value, trump and multiplier must match the record.

    Args:
        check: The round's accumulating checks.
        contract: The replayed contract, or ``None`` on an all-pass.
        score: The replayed round score, which owns the multiplier.
        recorded: The recorded score line.
    """

    terms = recorded.contract
    if contract is None or terms is None:
        if (contract is None) != (terms is None):
            check.fault(
                MismatchKind.SCORE,
                "one of the two has a contract and the other does not",
                expected="none" if contract is None else str(contract),
                observed="none" if terms is None else str(terms),
            )
        return
    replayed = (contract.value, contract.suit, score.multiplier)
    wanted = (terms.value, terms.suit, terms.multiplier)
    if replayed != wanted:
        check.fault(
            MismatchKind.SCORE,
            "the contract's terms differ",
            expected=f"{contract.value} {contract.suit} x{score.multiplier}",
            observed=f"{terms.value} {terms.suit} x{terms.multiplier}",
        )


def _sweep_substitute(slam: SlamOutcome) -> int | None:
    """The flat figure an observed table writes in place of a swept pile.

    §7.2: the substitute is the base value of the Slam-family level the
    round carries — **500** for an announced Solo Slam, **250** for an
    announced Slam and for an unannounced sweep alike. The engine's own
    personal-sweep premium (:func:`sweep_substitute`, which pays a
    declarer's solo sweep the Solo Slam's 500) is a *marking* rule and
    never reaches this column: an unannounced sweep is written 250
    whoever took the tricks.

    Args:
        slam: The round's slam classification.

    Returns:
        The substitute figure, or ``None`` when the round is no sweep at
        all and the real pile is the only acceptable answer.
    """

    if slam is SlamOutcome.SOLO_SLAM:
        return SlamLevel.SOLO_SLAM.base_value
    if slam in (SlamOutcome.SLAM, SlamOutcome.UNANNOUNCED):
        return SlamLevel.SLAM.base_value
    return None


def _defense_sweeper(round_: Any) -> TeamSide | None:
    """The defending side, when it took every trick of the replayed round.

    Read off the replay's own trick winners rather than the piles: a
    trick of sevens and eights is worth nothing, so a zero pile does not
    mean a side won no trick.

    Args:
        round_: The engine round, played out.

    Returns:
        The defense's side when it won all 8 tricks, else ``None`` —
        including when the declaring side swept, which the round's slam
        classification already covers.
    """

    contract = getattr(round_, "contract", None)
    state = getattr(round_, "play_state", None)
    if contract is None or state is None:
        return None
    winners = state.trick_winners
    if len(winners) != 8:
        return None
    sides = {winner.position.team_side for winner in winners}
    declaring = contract.player.position.team_side
    if len(sides) != 1 or declaring in sides:
        return None
    return sides.pop()


def _taken_agrees(
    score: Any,
    recorded: Any,
    slam: SlamOutcome,
    *,
    defense_sweeper: TeamSide | None = None,
) -> bool:
    """Whether the captured card points agree, allowing a sweep's substitute.

    §4.2.1: an observed table records a sweeping side's card points as
    the flat Slam substitute rather than the 162 actually on the table,
    while the engine records the real pile. Both are true statements
    about the same round, so a sweep accepts either — but only its *own*
    substitute, so a Solo Slam's 500 and a Slam's 250 stay distinct and
    a wrong figure is still a fault.

    The table writes a *defense's* sweep the same way, as the team's 250
    — a recording convention, whatever the ruleset then marks for it —
    so a defense that took every trick widens the tolerance too, though
    the round carries no slam classification.

    Args:
        score: The replayed round score.
        recorded: The recorded score line.
        slam: The round's slam classification.
        defense_sweeper: The defending side when it took all 8 tricks
            (:func:`_defense_sweeper`), else ``None``.

    Returns:
        Whether the two card-point lines describe the same round.
    """

    mine = {side: score.card_points.get(side, 0) for side in TeamSide}
    theirs = {side: recorded.taken.get(side, 0) for side in TeamSide}
    if mine == theirs:
        return True
    substitute = _sweep_substitute(slam)
    if substitute is None:
        if defense_sweeper is None:
            return False
        # The defense swept: its pile is the one the 250 stands in for.
        substitute, sweeper = SlamLevel.SLAM.base_value, defense_sweeper
    else:
        # A sweep: the side that took everything is the one whose pile the
        # substitute stands in for, and the other side's zero must still be
        # a zero on both sides of the comparison.
        sweeper = next(
            (side for side, points in mine.items() if points > 0), None
        )
    if sweeper is None:
        return False
    return theirs == {
        side: (substitute if side is sweeper else 0) for side in TeamSide
    }


def _ruleset_notes(record: GameRecord) -> list[str]:
    """Non-fatal observations about the record's ruleset.

    A record naming a preset whose ``RuleConfig`` no longer matches the
    config it carries is *stale*, not wrong — which is exactly what the
    acceptance corpus is after a knob is added, and exactly the sort of
    thing that is better said out loud than discovered later.

    Args:
        record: The record being verified.

    Returns:
        Zero or one note.
    """

    known = PRESETS.get(record.preset)
    if known is None or known == record.ruleset:
        return []
    # Field names come off the *known* preset, which is always a real
    # ``RuleConfig``. Reading them off the record's own ruleset would
    # trust the file to be a dataclass, which is not this layer's to
    # assume — and a note is never worth raising over.
    drifted = sorted(
        field.name
        for field in dataclasses.fields(known)
        if getattr(known, field.name) != getattr(record.ruleset, field.name, None)
    )
    return [
        f"the record names preset {record.preset!r}, whose ruleset now "
        f"differs from the one recorded: {', '.join(drifted)}"
    ]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def verify_game(record: GameRecord) -> GameVerdict:
    """Verify an already-loaded record.

    Rounds are walked in **file order**, so the verdict list reads like
    the record; only the complete ones are replayed, and the scripted
    deal source sees exactly those, in that order.

    Args:
        record: The record to verify.

    Returns:
        Its verdict.
    """

    observer = VerifyingObserver(record)
    controller = ReplayController(record, view=observer)
    for round_ in record.rounds:
        if not round_.complete:
            observer.round_skipped(round_)
            continue
        observer.expect(round_)
        try:
            controller.replay_round(round_)
        except (IllegalBidError, IllegalPlayError, ReplayError) as exc:
            observer.round_failed(round_, exc)
            # A round that stopped part-way left cards in the hands it
            # dealt to. The next round deals into those same hands, so
            # they are cleared here — without it, the seat after a failed
            # round holds sixteen cards and every later round is suspect
            # for a reason that has nothing to do with the record.
            controller.clear_hands()
    return observer.finish()


def verify_record(path: Path | str, *, out: Path | str | None = None) -> GameVerdict:
    """Load a record, verify it, and optionally write the verdict.

    Args:
        path: The record file.
        out: The records root to write ``verdicts/<game_id>.json`` under.
            ``None`` (the default) writes nothing; the caller may still
            write the returned verdict itself. Passing the record's own
            root puts the verdict beside ``games/``, which is the §3.3
            layout.

    Returns:
        The record's verdict.

    Raises:
        FileNotFoundError: If the file does not exist.
        RecordFormatError: If the record is malformed — a *format*
            failure, which is not a verdict: there is nothing to verify.
        UnsupportedFormatError: If the header names an unreadable format.
    """

    verdict = verify_game(load_game(path))
    if out is not None:
        write_verdict(out, verdict)
    return verdict


def default_out_root(path: Path | str) -> Path:
    """The records root a record at ``path`` belongs to.

    A record lives at ``<root>/games/<game_id>.jsonl``, so its root is
    two levels up — which is where its verdict belongs (§3.3). A file
    somewhere else entirely keeps its own directory as the root, so a
    one-off verification still writes somewhere sensible.

    Args:
        path: A record file.

    Returns:
        The root to write verdicts under.
    """

    file = Path(path)
    if file.parent.name == "games":
        return file.parent.parent
    return file.parent


__all__ = [
    "VerifyingObserver",
    "Verdict",
    "verify_game",
    "verify_record",
    "default_out_root",
]
