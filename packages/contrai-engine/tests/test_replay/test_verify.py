"""Pins the verifier: a clean record verifies, a broken one is suspect.

Every suspect record here is built the same way — take a **real, legal**
game, take it apart into events, break exactly one thing, put it back.
That is what makes each case a fair test of one check: the round is
otherwise a round that actually happened, so anything the verifier
reports is attributable to the single mutation.
"""

from __future__ import annotations

import json

import pytest
from contrai_core import Card, DoubleBid, PassBid, Rank, Suit
from contrai_data import (
    BeloteHeld,
    BidMade,
    CardPlayed,
    RoundScored,
    SideMark,
)

from contrai_engine.replay import Verdict, verify_game, verify_record
from contrai_engine.replay.verify import default_out_root

from .conftest import TS, play_and_record, rebuilt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_verdict(verdict, number):
    """The verdict of round ``number``."""

    return next(r for r in verdict.rounds if r.number == number)


def _kinds(round_verdict) -> set[str]:
    """The mismatch classes a round reported."""

    return {str(m.kind) for m in round_verdict.mismatches}


def _first_contracted(record):
    """The first round of ``record`` that reached a contract."""

    return next(r for r in record.rounds if r.contract is not None)


# ---------------------------------------------------------------------------
# The engine's own records verify
# ---------------------------------------------------------------------------


class TestCleanRecords:
    """Verify on the engine's own records must always be clean (§5.4)."""

    def test_a_recorded_game_verifies_every_round(self, recorded_game):
        verdict = verify_game(recorded_game)

        assert verdict.verdict is Verdict.VERIFIED
        assert verdict.counts[Verdict.SUSPECT] == 0
        assert verdict.counts[Verdict.PARTIAL] == 0
        assert len(verdict.rounds) == len(recorded_game.rounds)

    def test_a_tournament_record_verifies(self, recorded_tournament_game):
        # The ruleset observed games carry, and a clockwise one.
        verdict = verify_game(recorded_tournament_game)

        assert verdict.verdict is Verdict.VERIFIED

    @pytest.mark.parametrize("seed", [1, 7, 2024])
    def test_several_seeds_verify(self, tmp_path, seed):
        record = play_and_record(tmp_path / str(seed), seed=seed)

        assert verify_game(record).verdict is Verdict.VERIFIED

    def test_an_all_pass_round_verifies(self, recorded_game):
        passed_out = [r for r in recorded_game.rounds if r.contract is None]
        assert passed_out, "seed 1 is expected to pass a round out"

        verdict = verify_game(recorded_game)

        for round_ in passed_out:
            assert _round_verdict(verdict, round_.number).verdict is (
                Verdict.VERIFIED
            )

    def test_rebuilding_a_record_changes_nothing(self, recorded_game):
        # The mutation tests below are only meaningful if the take-apart
        # and put-back is itself lossless.
        assert verify_game(rebuilt(recorded_game)).verdict is Verdict.VERIFIED

    def test_a_verdict_names_the_record(self, recorded_game):
        verdict = verify_game(recorded_game)

        assert verdict.game_id == recorded_game.header.game_id
        assert verdict.source == "engine"
        assert verdict.preset == "classic"


# ---------------------------------------------------------------------------
# illegal_bid
# ---------------------------------------------------------------------------


def _partner_doubles(record):
    """``record`` with one round's declarer doubled by its own partner.

    The mutation has to leave the round *structurally* complete, or the
    projection marks it incomplete and it is never replayed at all — so
    the check under test would never run. A ``[contract, pass, pass,
    pass]`` auction becomes ``[contract, pass, DOUBLE, pass, pass,
    pass]``: the third speaker is the declarer's partner (seats alternate
    sides, so speakers 0 and 2 are partners), three trailing passes still
    close it, and the contract still stands. Everything the projection
    checks holds; the one thing that does not is the rule
    ``Auction._is_double_legal`` enforces.

    Returns:
        The round mutated, and the rebuilt record.
    """

    target = next(
        r
        for r in record.rounds
        if r.contract is not None and len(r.auction) == 4
    )
    doubler = target.auction[2].player
    assert doubler is target.auction[0].player.partner

    def mutate(events):
        out = []
        for event in events:
            if not (isinstance(event, BidMade) and event.round == target.number):
                out.append(event)
                continue
            bid = DoubleBid(doubler) if event.seq == 3 else event.bid
            out.append(
                BidMade(
                    round=event.round,
                    seq=event.seq,
                    position=event.position,
                    bid=bid,
                    think_ms=None,
                    ts=TS,
                )
            )
            if event.seq == 4:
                # The double replaced a pass, so two more are needed for
                # three consecutive ones to close the auction again — the
                # two seats that speak after the last original pass.
                for offset, speaker in enumerate(
                    (target.auction[0].player, target.auction[1].player), start=5
                ):
                    out.append(
                        BidMade(
                            round=event.round,
                            seq=offset,
                            position=speaker,
                            bid=PassBid(speaker),
                            think_ms=None,
                            ts=TS,
                        )
                    )
        return out

    return target, rebuilt(record, mutate)


class TestIllegalBid:
    def test_a_seat_doubling_its_own_partner_is_suspect(self, recorded_game):
        # ``Auction.apply`` refuses it; the verifier never re-implements
        # the rule, it just catches the refusal.
        target, broken = _partner_doubles(recorded_game)

        verdict = verify_game(broken)

        assert verdict.verdict is Verdict.SUSPECT
        reported = _round_verdict(verdict, target.number)
        assert _kinds(reported) == {"illegal_bid"}
        assert "refused a recorded bid" in reported.mismatches[0].detail

    def test_the_later_rounds_still_verify(self, recorded_game):
        # One bad round does not poison the rest: the next round starts
        # from its own recorded deal, into hands the driver cleared.
        target, broken = _partner_doubles(recorded_game)

        verdict = verify_game(broken)

        later = [r for r in verdict.rounds if r.number > target.number]
        assert later, "the game is expected to run past the broken round"
        assert all(r.verdict is Verdict.VERIFIED for r in later)


# ---------------------------------------------------------------------------
# illegal_play
# ---------------------------------------------------------------------------


class TestIllegalPlay:
    def test_a_card_a_seat_does_not_hold_is_suspect(self, recorded_game):
        target = _first_contracted(recorded_game)
        # Hand every seat the trick's last card: three of the four do not
        # hold it, so ``PlayState.apply`` refuses.
        intruder = target.tricks[0][3].card

        def mutate(events):
            return [
                CardPlayed(
                    round=e.round,
                    trick=e.trick,
                    position=e.position,
                    card=intruder,
                    derived=False,
                    think_ms=None,
                    ts=TS,
                )
                if isinstance(e, CardPlayed)
                and e.round == target.number
                and e.trick == 1
                else e
                for e in events
            ]

        verdict = verify_game(rebuilt(recorded_game, mutate))

        assert verdict.verdict is Verdict.SUSPECT
        assert "illegal_play" in _kinds(_round_verdict(verdict, target.number))


# ---------------------------------------------------------------------------
# trick_winner
# ---------------------------------------------------------------------------


class TestTrickWinner:
    def test_a_trick_led_by_the_wrong_seat_is_suspect(self, recorded_game):
        # Rotate trick 2's four plays. The same four cards are played by
        # the same four seats, so nothing is illegal — but the trick is
        # now led by a seat core says did not win trick 1, and the replay
        # asks it for a card out of turn.
        target = _first_contracted(recorded_game)
        rotated = list(target.tricks[1][1:]) + [target.tricks[1][0]]

        def mutate(events):
            out, index = [], 0
            for event in events:
                if (
                    isinstance(event, CardPlayed)
                    and event.round == target.number
                    and event.trick == 2
                ):
                    play = rotated[index]
                    index += 1
                    out.append(
                        CardPlayed(
                            round=event.round,
                            trick=event.trick,
                            position=play.position,
                            card=play.card,
                            derived=False,
                            think_ms=None,
                            ts=TS,
                        )
                    )
                else:
                    out.append(event)
            return out

        verdict = verify_game(rebuilt(recorded_game, mutate))

        assert verdict.verdict is Verdict.SUSPECT
        assert "trick_winner" in _kinds(_round_verdict(verdict, target.number))


# ---------------------------------------------------------------------------
# belote
# ---------------------------------------------------------------------------


class TestBelote:
    @staticmethod
    def _unsupported(record):
        """A (round, seat, suit) whose seat holds no King-and-Queen there."""

        for round_ in record.rounds:
            for seat, cards in round_.hands.items():
                held = set(cards)
                for suit in Suit:
                    pair = {Card(suit, Rank.KING), Card(suit, Rank.QUEEN)}
                    if not pair <= held:
                        return round_, seat, suit
        raise AssertionError("no unsupported pair in the record")

    @staticmethod
    def _held_off_trump(record):
        """A contracted round where a seat holds K + Q outside trump."""

        for round_ in record.rounds:
            if round_.contract is None:
                continue
            for seat, cards in round_.hands.items():
                held = set(cards)
                for suit in Suit:
                    if suit is round_.trump_suit:
                        continue
                    pair = {Card(suit, Rank.KING), Card(suit, Rank.QUEEN)}
                    if pair <= held:
                        return round_, seat, suit
        raise AssertionError("no off-trump pair in the record")

    def test_a_belote_the_deal_does_not_hold_is_suspect(self, recorded_game):
        round_, seat, suit = self._unsupported(recorded_game)

        def mutate(events):
            return _insert(
                events,
                BeloteHeld(
                    round=round_.number,
                    position=seat,
                    cards=(Card(suit, Rank.KING), Card(suit, Rank.QUEEN)),
                    announced=False,
                    ts=TS,
                ),
            )

        verdict = verify_game(rebuilt(recorded_game, mutate))

        assert verdict.verdict is Verdict.SUSPECT
        reported = _round_verdict(verdict, round_.number)
        assert _kinds(reported) == {"belote"}
        assert reported.mismatches[0].position == str(seat)

    def test_an_announced_belote_the_replay_never_announced_is_suspect(
        self, recorded_game
    ):
        # The seat really does hold the pair, so the first belote rule
        # passes — but the suit is not trump, so no announcement can ever
        # have happened.
        round_, seat, suit = self._held_off_trump(recorded_game)

        def mutate(events):
            return _insert(
                events,
                BeloteHeld(
                    round=round_.number,
                    position=seat,
                    cards=(Card(suit, Rank.KING), Card(suit, Rank.QUEEN)),
                    announced=True,
                    ts=TS,
                ),
            )

        verdict = verify_game(rebuilt(recorded_game, mutate))

        reported = _round_verdict(verdict, round_.number)
        assert reported.verdict is Verdict.SUSPECT
        assert "belote" in _kinds(reported)
        assert "never announced" in reported.mismatches[0].detail

    def test_a_belote_the_record_omits_is_not_a_mismatch(self, recorded_game):
        # An observed table may simply not transmit a pair. If it moved
        # the marks the score check catches it; if it did not, there is
        # nothing to report.
        with_belotes = [r for r in recorded_game.rounds if r.belotes]
        assert with_belotes, "seed 1 is expected to announce a belote"

        def mutate(events):
            return [e for e in events if not isinstance(e, BeloteHeld)]

        verdict = verify_game(rebuilt(recorded_game, mutate))

        assert verdict.verdict is Verdict.VERIFIED


def _insert(events, event):
    """``events`` with ``event`` placed inside its own round's block.

    Just before that round's score line, which every round of a recorded
    engine game carries — the projection files an event by its round
    number rather than by where it sits, but keeping the block intact
    makes a dumped record readable.
    """

    out = []
    for existing in events:
        if isinstance(existing, RoundScored) and existing.round == event.round:
            out.append(event)
        out.append(existing)
    assert event in out, "the target round carries no score line to insert before"
    return out


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


class TestScore:
    @staticmethod
    def _scored(record):
        """The first round of ``record`` carrying a score line."""

        return next(r for r in record.rounds if r.score is not None)

    def _mutating_score(self, record, **changes):
        """``record`` rebuilt with one round's score line changed."""

        target = self._scored(record)

        def mutate(events):
            return [
                type(e)(
                    **{
                        **{
                            f: getattr(e, f)
                            for f in type(e).__dataclass_fields__
                        },
                        **changes,
                    }
                )
                if isinstance(e, RoundScored) and e.round == target.number
                else e
                for e in events
            ]

        return target, verify_game(rebuilt(record, mutate))

    def test_different_marked_points_are_suspect(self, recorded_game):
        target = self._scored(recorded_game)
        bumped = {
            side: SideMark(made=mark.made + 10, announced=mark.announced)
            for side, mark in target.score.marked.items()
        }

        target, verdict = self._mutating_score(recorded_game, marked=bumped)

        reported = _round_verdict(verdict, target.number)
        assert reported.verdict is Verdict.SUSPECT
        assert _kinds(reported) == {"score"}
        assert "marked points differ" in reported.mismatches[0].detail

    def test_different_card_points_are_suspect(self, recorded_game):
        target = self._scored(recorded_game)
        shifted = {side: points + 1 for side, points in target.score.taken.items()}

        target, verdict = self._mutating_score(recorded_game, taken=shifted)

        reported = _round_verdict(verdict, target.number)
        assert reported.verdict is Verdict.SUSPECT
        assert "card points differ" in reported.mismatches[0].detail

    def test_a_different_last_trick_side_is_suspect(self, recorded_game):
        from contrai_core import TeamSide

        target = self._scored(recorded_game)
        other = (
            TeamSide.EW if target.score.last_trick is TeamSide.NS else TeamSide.NS
        )

        target, verdict = self._mutating_score(recorded_game, last_trick=other)

        assert "score" in _kinds(_round_verdict(verdict, target.number))

    def test_a_round_with_no_score_line_is_partial(self, recorded_game):
        # The common case in an observed game: a spectator sees the cards
        # long before it sees a score sheet.
        target = self._scored(recorded_game)

        def mutate(events):
            return [
                e
                for e in events
                if not (isinstance(e, RoundScored) and e.round == target.number)
            ]

        verdict = verify_game(rebuilt(recorded_game, mutate))

        reported = _round_verdict(verdict, target.number)
        assert reported.verdict is Verdict.PARTIAL
        assert reported.mismatches == ()
        assert reported.unchecked == ("score",)
        assert verdict.verdict is Verdict.PARTIAL

    def test_a_record_with_no_score_lines_at_all_is_partial_not_suspect(
        self, recorded_game
    ):
        # The corpus shape the gate is phrased for: 2 score lines across
        # 14 rounds, and no round suspect.
        def mutate(events):
            return [e for e in events if not isinstance(e, RoundScored)]

        verdict = verify_game(rebuilt(recorded_game, mutate))

        assert verdict.verdict is Verdict.PARTIAL
        assert verdict.counts[Verdict.SUSPECT] == 0
        assert verdict.counts[Verdict.PARTIAL] == len(recorded_game.rounds)


# ---------------------------------------------------------------------------
# Incomplete rounds and ruleset drift
# ---------------------------------------------------------------------------


class TestPartialRounds:
    def test_an_unreplayable_round_is_partial(self, recorded_game):
        last = recorded_game.rounds[-1]

        def mutate(events):
            # Drop the last round's final trick: the auction closed but
            # the eighth trick never landed, so it cannot be replayed.
            return [
                e
                for e in events
                if not (
                    isinstance(e, CardPlayed)
                    and e.round == last.number
                    and e.trick == 8
                )
            ]

        verdict = verify_game(rebuilt(recorded_game, mutate))

        reported = _round_verdict(verdict, last.number)
        assert reported.verdict is Verdict.PARTIAL
        assert reported.replayed is False
        assert verdict.counts[Verdict.SUSPECT] == 0


class TestRulesetDrift:
    def test_a_stale_preset_is_a_note_not_a_verdict(self, tmp_path):
        # A record naming ``tournament`` but carrying the classic config
        # is stale, not wrong — which is exactly what the acceptance
        # corpus is after a knob is added.
        record = play_and_record(tmp_path, seed=1, preset="tournament")

        verdict = verify_game(record)

        assert verdict.verdict is Verdict.VERIFIED
        assert len(verdict.notes) == 1
        assert "tournament" in verdict.notes[0]
        assert "any_failure_marks_160" in verdict.notes[0]

    def test_a_matching_preset_produces_no_note(self, recorded_tournament_game):
        assert verify_game(recorded_tournament_game).notes == ()

    def test_an_unknown_preset_produces_no_note(self, tmp_path):
        record = play_and_record(tmp_path, seed=1, preset="house.toml")

        assert verify_game(record).notes == ()


# ---------------------------------------------------------------------------
# The file-facing entry point
# ---------------------------------------------------------------------------


class TestVerifyRecord:
    def test_it_verifies_a_file(self, tmp_path):
        play_and_record(tmp_path, seed=1)
        (path,) = (tmp_path / "games").glob("*.jsonl")

        verdict = verify_record(path)

        assert verdict.verdict is Verdict.VERIFIED

    def test_it_writes_no_verdict_unless_asked(self, tmp_path):
        play_and_record(tmp_path, seed=1)
        (path,) = (tmp_path / "games").glob("*.jsonl")

        verify_record(path)

        assert not (tmp_path / "verdicts").exists()

    def test_it_writes_the_verdict_beside_games(self, tmp_path):
        play_and_record(tmp_path, seed=1)
        (path,) = (tmp_path / "games").glob("*.jsonl")

        verdict = verify_record(path, out=default_out_root(path))

        written = tmp_path / "verdicts" / f"{verdict.game_id}.json"
        assert json.loads(written.read_text(encoding="utf-8")) == verdict.as_json()

    def test_a_record_outside_a_games_directory_keeps_its_own_root(
        self, tmp_path
    ):
        loose = tmp_path / "loose.jsonl"
        loose.write_text("", encoding="utf-8")

        assert default_out_root(loose) == tmp_path

    def test_a_record_in_a_games_directory_walks_up_two(self, tmp_path):
        assert default_out_root(tmp_path / "games" / "x.jsonl") == tmp_path
