"""Tests for the replay step gate.

The wrapper is driven with a fake inner view rather than a ``RichView``:
what is under test is *when* it stops and what it forwards, and a Rich
console would only make that harder to read.
"""

from __future__ import annotations

import pytest

from contrai_engine.replay.stepping import (
    ReplayInterrupt,
    StepMode,
    SteppingView,
)


class _Inner:
    """Records the hooks it receives and answers the step prompt."""

    def __init__(self, keys: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.prompts: list[bool] = []
        self._keys = list(keys or [])

    def attach(self, game, target_score):
        self.calls.append("attach")

    def on_round_dealt(self, round_):
        self.calls.append("on_round_dealt")

    def on_bid_made(self, player, bid, history):
        self.calls.append("on_bid_made")

    def on_card_played(self, player, card, plays):
        self.calls.append("on_card_played")

    def on_belote_announced(self, player, kind, suit, round_):
        self.calls.append("on_belote_announced")

    def on_trick_complete(self, plays, winner, round_):
        self.calls.append("on_trick_complete")

    def on_round_complete(self, round_, running_scores):
        self.calls.append("on_round_complete")

    def show_replay_deal(self, round_):
        self.calls.append("show_replay_deal")

    def show_replay_step(self, *, can_go_back):
        self.prompts.append(can_go_back)
        return self._keys.pop(0) if self._keys else "r"

    def anything_else(self):
        return "forwarded"


def _play(stepper, cards):
    """Feed ``cards`` plays, tripping the trick hook every fourth."""
    for index in range(cards):
        plays = [None] * (index % 4 + 1)
        stepper.on_card_played(None, None, plays)
        if len(plays) == 4:
            stepper.on_trick_complete(plays, None, None)


class TestQuietPrefix:
    def test_quiet_forwards_only_attach_and_round_complete(self):
        inner = _Inner()
        stepper = SteppingView(inner)

        stepper.attach(None, 2000)
        stepper.on_round_dealt(None)
        stepper.on_bid_made(None, None, [])
        stepper.on_card_played(None, None, [None])
        stepper.on_round_complete(None, {})

        assert inner.calls == ["attach", "on_round_complete"]
        assert inner.prompts == []

    def test_quiet_swallows_the_belote_and_trick_hooks_too(self):
        inner = _Inner()
        stepper = SteppingView(inner)

        stepper.on_belote_announced(None, "belote", None, None)
        stepper.on_trick_complete([None] * 4, None, None)

        assert inner.calls == []

    def test_quiet_takes_no_stops(self):
        stepper = SteppingView(_Inner())

        stepper.on_bid_made(None, None, [])

        assert stepper.stops == 0


class TestStops:
    def test_the_deal_is_a_stop_and_renders_a_frame(self):
        inner = _Inner(["n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_round_dealt(None)

        assert inner.calls == ["on_round_dealt", "show_replay_deal"]
        assert stepper.stops == 1

    def test_a_trick_costs_four_stops_not_five(self):
        inner = _Inner(["n"] * 20)
        stepper = SteppingView(inner)
        stepper.quiet = False

        _play(stepper, 4)

        assert stepper.stops == 4
        assert inner.prompts == [False, True, True, True]

    def test_the_fourth_card_stops_on_the_trick_frame(self):
        inner = _Inner(["n"] * 20)
        stepper = SteppingView(inner)
        stepper.quiet = False

        _play(stepper, 4)

        # The fourth card is forwarded but does not stop; the frame the
        # fourth stop sits under is the trick-won one.
        assert inner.calls[-1] == "on_trick_complete"

    def test_a_belote_announcement_is_its_own_stop(self):
        inner = _Inner(["n", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_card_played(None, None, [None])
        stepper.on_belote_announced(None, "belote", None, None)

        assert stepper.stops == 2


class TestModes:
    def test_t_runs_to_the_end_of_the_trick(self):
        inner = _Inner(["t"] + ["n"] * 10)
        stepper = SteppingView(inner)
        stepper.quiet = False

        _play(stepper, 4)

        # One prompt for the first card, then nothing until the trick frame.
        assert inner.prompts == [False, True]

    def test_r_runs_to_the_end_of_the_round(self):
        inner = _Inner(["r"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        _play(stepper, 8)

        assert inner.prompts == [False]
        assert stepper.stops == 8

    def test_an_unknown_key_falls_back_to_stepping_by_action(self):
        # The view never returns one, but the mode table is the only
        # thing standing between a stray key and a runaway replay.
        inner = _Inner(["z", "n", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_bid_made(None, None, [])
        stepper.on_bid_made(None, None, [])

        assert len(inner.prompts) == 2

    def test_a_mode_renders_as_its_token(self):
        assert str(StepMode.TRICK) == "trick"


class TestLeaving:
    def test_q_raises_an_interrupt_with_no_resume_point(self):
        stepper = SteppingView(_Inner(["q"]))
        stepper.quiet = False

        with pytest.raises(ReplayInterrupt) as excinfo:
            stepper.on_bid_made(None, None, [])

        assert excinfo.value.resume_at is None

    def test_p_resumes_two_stops_back(self):
        stepper = SteppingView(_Inner(["n", "n", "p"]))
        stepper.quiet = False

        with pytest.raises(ReplayInterrupt) as excinfo:
            for _ in range(3):
                stepper.on_bid_made(None, None, [])

        # Stopped at the third; re-entering wants the second honoured,
        # so the first is skipped.
        assert excinfo.value.resume_at == 1

    def test_the_first_stop_cannot_go_back(self):
        inner = _Inner(["n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_bid_made(None, None, [])

        assert inner.prompts == [False]

    def test_going_back_from_the_second_stop_lands_at_zero(self):
        # ``max(stops - 2, 0)`` — a back from stop 2 replays from the top
        # rather than underflowing into a negative fast-forward.
        stepper = SteppingView(_Inner(["n", "p"]))
        stepper.quiet = False

        with pytest.raises(ReplayInterrupt) as excinfo:
            for _ in range(2):
                stepper.on_bid_made(None, None, [])

        assert excinfo.value.resume_at == 0


class TestFastForward:
    def test_resume_at_skips_that_many_stops(self):
        inner = _Inner(["n"] * 5)
        stepper = SteppingView(inner, resume_at=2)
        stepper.quiet = False

        for _ in range(4):
            stepper.on_bid_made(None, None, [])

        assert stepper.stops == 4
        assert len(inner.prompts) == 2

    def test_a_skipped_stop_still_forwards_its_frame(self):
        inner = _Inner(["n"] * 5)
        stepper = SteppingView(inner, resume_at=2)
        stepper.quiet = False

        stepper.on_bid_made(None, None, [])

        assert inner.calls == ["on_bid_made"]
        assert inner.prompts == []


class TestForwarding:
    def test_an_unknown_attribute_reaches_the_inner_view(self):
        stepper = SteppingView(_Inner())

        assert stepper.anything_else() == "forwarded"

    def test_it_is_not_a_replay_error(self):
        from contrai_engine.replay.exceptions import ReplayError

        assert not issubclass(ReplayInterrupt, ReplayError)
