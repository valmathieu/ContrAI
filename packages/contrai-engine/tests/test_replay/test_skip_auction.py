"""``[a]`` on a real recorded round: it lands before the first card.

``test_stepping`` proves the gate's arithmetic against a fake engine;
this proves the promise against the real one — that the engine fires
``on_contract_established`` after the last bid and before any card, so
the stop ``[a]`` comes to rest on really is the empty table.
"""

from __future__ import annotations

from contrai_engine.replay.controller import ReplayController
from contrai_engine.replay.stepping import ReplayInterrupt, SteppingView

from .conftest import play_and_record


class _Recorder:
    """An inner view that notes what it is shown and answers ``a``, then ``q``."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.rested_on = None
        self._keys = ["a", "q"]

    def attach(self, game, target_score):
        pass

    def on_round_complete(self, round_, running_scores):
        pass

    def on_round_dealt(self, round_):
        self.events.append("deal")

    def on_bid_made(self, player, bid, history):
        self.events.append("bid")

    def on_card_played(self, player, card, plays):
        self.events.append("card")

    def show_replay_deal(self, round_):
        pass

    def show_replay_contract(self, round_):
        self.events.append("contract")
        self.rested_on = round_

    def show_replay_step(self, *, can_go_back, can_skip_auction=False):
        self.events.append("prompt")
        return self._keys.pop(0)


def test_a_rests_on_the_contract_before_any_card_is_played(tmp_path):
    record = play_and_record(tmp_path, seed=7, rounds=2)
    inner = _Recorder()
    stepper = SteppingView(inner)
    controller = ReplayController(record, view=stepper)
    stepper.attach(controller.game, controller.game.rules.target_score)
    # Seed 7 passes its first round out; the second is played.
    passed_out, played = controller.rounds
    assert not passed_out.tricks and played.tricks
    controller.replay_round(passed_out)
    stepper.quiet = False

    try:
        controller.replay_round(played)
    except ReplayInterrupt:
        pass

    bids = len(played.auction)
    assert inner.events == ["deal", "prompt"] + ["bid"] * bids + [
        "contract",
        "prompt",
    ]
    assert inner.rested_on.contract is not None
    assert inner.rested_on.play_state is None
