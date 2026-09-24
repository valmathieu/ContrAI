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
    read_step_key,
)


class _Inner:
    """Records the hooks it receives and answers the step prompt."""

    def __init__(self, keys: list[str] | None = None) -> None:
        self.calls: list[str] = []
        self.prompts: list[bool] = []
        self.skips: list[bool] = []
        self.grids: list[tuple] = []
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

    def on_contract_established(self, round_):
        self.calls.append("on_contract_established")

    def show_replay_deal(self, round_):
        self.calls.append("show_replay_deal")

    def show_replay_contract(self, round_):
        self.calls.append("show_replay_contract")

    def show_replay_grid(self, round_, bids):
        self.calls.append("show_replay_grid")
        self.grids.append((round_, list(bids)))

    def redraw_screen(self):
        self.calls.append("redraw_screen")

    def toggle_rationale(self):
        self.calls.append("toggle_rationale")

    def show_replay_step(self, *, can_go_back, can_skip_auction=False):
        self.prompts.append(can_go_back)
        self.skips.append(can_skip_auction)
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


def _bid_to_contract(stepper, bids):
    """Deal, make ``bids`` bids, then close the auction on a contract."""
    stepper.on_round_dealt(None)
    for _ in range(bids):
        stepper.on_bid_made(None, None, [])
    stepper.on_contract_established(None)


class TestSkipAuction:
    def test_a_passes_the_bids_and_rests_on_the_contract(self):
        inner = _Inner(["a", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        _bid_to_contract(stepper, 4)

        # The deal's prompt, then nothing until the contract's.
        assert len(inner.prompts) == 2
        assert inner.calls[-2:] == [
            "on_contract_established",
            "show_replay_contract",
        ]

    def test_the_contract_stop_is_not_counted(self):
        # It re-shows the last bid's stop, so numbering is the same
        # whichever keys brought the viewer there.
        stepper = SteppingView(_Inner(["a", "n"]))
        stepper.quiet = False

        _bid_to_contract(stepper, 4)

        assert stepper.stops == 5

    def test_back_from_the_contract_steps_back_as_from_the_last_bid(self):
        stepper = SteppingView(_Inner(["a", "p"]))
        stepper.quiet = False

        with pytest.raises(ReplayInterrupt) as excinfo:
            _bid_to_contract(stepper, 4)

        assert excinfo.value.resume_at == 3

    def test_play_steps_by_action_after_the_contract(self):
        inner = _Inner(["a", "n", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False
        _bid_to_contract(stepper, 4)

        stepper.on_card_played(None, None, [None])

        assert len(inner.prompts) == 3

    def test_an_all_pass_round_runs_to_its_end(self):
        # No contract hook ever fires, so nothing stops ``a`` before the
        # round is over.
        inner = _Inner(["a"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_round_dealt(None)
        for _ in range(4):
            stepper.on_bid_made(None, None, [])

        assert len(inner.prompts) == 1

    def test_a_is_offered_only_while_the_auction_is_open(self):
        inner = _Inner(["n"] * 10)
        stepper = SteppingView(inner)
        stepper.quiet = False

        _bid_to_contract(stepper, 2)
        stepper.on_card_played(None, None, [None])

        # Deal, two bids: open. The card: closed.
        assert inner.skips == [True, True, True, False]

    def test_outside_a_the_contract_is_forwarded_without_a_stop(self):
        inner = _Inner(["n"] * 10)
        stepper = SteppingView(inner)
        stepper.quiet = False

        _bid_to_contract(stepper, 4)

        assert inner.calls[-1] == "on_contract_established"
        assert len(inner.prompts) == 5

    def test_quiet_forwards_no_contract_but_still_closes_the_auction(self):
        inner = _Inner()
        stepper = SteppingView(inner)

        stepper.on_round_dealt(None)
        stepper.on_contract_established(None)
        stepper.quiet = False
        stepper.on_card_played(None, None, [None])

        assert "on_contract_established" not in inner.calls
        assert inner.skips == [False]

    def test_an_inner_view_without_the_hook_is_tolerated(self):
        # The engine asks ``hasattr`` of the wrapper, which always says
        # yes now, so a hookless inner view must not break the round.
        class _Hookless(_Inner):
            on_contract_established = None

        stepper = SteppingView(_Hookless(["n"] * 10))
        stepper.quiet = False

        _bid_to_contract(stepper, 1)

        assert stepper.stops == 2


class TestGridKey:
    def test_g_shows_the_grid_repaints_and_asks_again(self):
        inner = _Inner(["g", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_round_dealt("round")

        assert inner.calls == [
            "on_round_dealt",
            "show_replay_deal",
            "show_replay_grid",
            "redraw_screen",
        ]
        assert inner.grids == [("round", [])]
        assert len(inner.prompts) == 2

    def test_g_takes_no_stop_and_the_next_key_still_counts(self):
        # At card 1: 'g', then 't'. The 't' read after the grid runs the
        # trick out as it would have without it.
        inner = _Inner(["g", "t", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        _play(stepper, 4)

        assert stepper.stops == 4
        # Card 1 was asked twice (g, t); the trick frame once.
        assert inner.prompts == [False, False, True]
        assert len(inner.grids) == 1

    def test_the_grid_gets_the_bids_so_far(self):
        inner = _Inner(["n", "n", "g", "n"])
        stepper = SteppingView(inner)
        stepper.quiet = False

        stepper.on_round_dealt("round")
        stepper.on_bid_made(None, None, ["bid 1"])
        stepper.on_bid_made(None, None, ["bid 1", "bid 2"])

        assert inner.grids == [("round", ["bid 1", "bid 2"])]

    def test_bids_are_tracked_while_quiet_and_reset_by_a_deal(self):
        stepper = SteppingView(_Inner())

        stepper.on_round_dealt("round 1")
        stepper.on_bid_made(None, None, ["bid"])
        assert stepper.bids == ["bid"]

        stepper.on_round_dealt("round 2")
        assert stepper.bids == []


class TestReadStepKey:
    def test_any_number_of_grids_before_the_real_key(self):
        inner = _Inner(["g", "g", "t"])

        key = read_step_key(inner, "round", ["bid"], can_go_back=True)

        assert key == "t"
        assert inner.grids == [("round", ["bid"])] * 2
        assert inner.calls.count("redraw_screen") == 2

    def test_w_toggles_the_reasons_repaints_and_asks_again(self):
        inner = _Inner(["w", "w", "n"])

        key = read_step_key(inner, "round", [], can_go_back=True)

        assert key == "n"
        assert inner.calls == [
            "toggle_rationale",
            "redraw_screen",
            "toggle_rationale",
            "redraw_screen",
        ]

    def test_the_offers_reach_the_prompt(self):
        inner = _Inner(["n"])

        read_step_key(
            inner, None, [], can_go_back=False, can_skip_auction=True
        )

        assert inner.prompts == [False]
        assert inner.skips == [True]


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
