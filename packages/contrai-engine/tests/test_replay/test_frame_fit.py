"""Every replay frame fits on one terminal screen.

The regression this guards: a replay frame once stacked about 50 rows —
an AI-rationale panel whose every entry read "recorded action", and a
second Prompt panel under the frame's own — so the score, round and trick
panels at its top scrolled off a normal terminal. The frame is measured
here as it is really drawn: a recorded game replayed through
:class:`SteppingView` into a :class:`RichView` whose console writes to a
buffer, one frame per ``console.clear()``.
"""

from __future__ import annotations

import io

from rich.console import Console

from contrai_engine.options import DebugOptions
from contrai_engine.replay.controller import ReplayController
from contrai_engine.replay.stepping import SteppingView
from contrai_engine.view.rich_view import RichView

from .conftest import play_and_record

#: The tallest frame allowed, input line included. Forty rows is a
#: normal terminal's height with a little to spare.
MAX_ROWS = 40


def _frame_heights(record) -> list[int]:
    """Replay every round of ``record`` a key at a time; measure each frame.

    Args:
        record: The recorded game.

    Returns:
        The height of every frame, in rows, counting the input line.
    """

    view = RichView(options=DebugOptions(replay=True))
    buffer = io.StringIO()
    view.console = Console(file=buffer, width=100, no_color=True)
    starts: list[int] = []
    view.console.clear = lambda *a, **k: starts.append(buffer.tell())
    view.console.input = lambda *a, **k: "n"
    stepper = SteppingView(view)
    controller = ReplayController(record, view=stepper)
    stepper.attach(controller.game, controller.game.rules.target_score)
    stepper.quiet = False
    for round_ in controller.rounds:
        controller.replay_round(round_)
    text = buffer.getvalue()
    bounds = starts + [len(text)]
    # ``+ 1`` for the line the viewer types on, which the stubbed input
    # never writes.
    return [
        text[start:end].count("\n") + 1
        for start, end in zip(bounds, bounds[1:])
    ]


def test_every_frame_of_a_replay_fits_in_forty_rows(tmp_path):
    # Seed 7's sixth round has a three-row auction, the tallest frame
    # the unfixed layout was measured on.
    heights = _frame_heights(play_and_record(tmp_path, seed=7, rounds=6))

    assert len(heights) > 100
    assert max(heights) <= MAX_ROWS
