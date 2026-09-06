"""Shared fixtures for the replay tests.

Every record these tests replay is produced by **playing a real game**
and recording it, never by hand. That is not convenience — it is the only
way to get a record that is *legal*: a hand-written auction and a
hand-written trick sequence have to satisfy the follow-suit, over-trump
and bidding-ladder rules simultaneously, and one that does not is
indistinguishable from the corruption these tests exist to catch.
(``contrai-data``'s own ``three_round_game`` fixture is deliberately
illegal — every seat holds one suit, so nobody can ever follow — and
cannot be reused here.)

Hand-written records still appear in these tests, but only for the
*error* paths, where being illegal is the point.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from contrai_core import Position, RuleConfig
from contrai_data import GameRecord, load_game

from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer
from contrai_engine.recording import RecordingView


class SilentView:
    """A view with none of the push hooks — the ``hasattr`` contract.

    ``RecordingView`` forwards to an inner view; these tests care about
    the record, not about what a screen would have shown, so the inner
    view swallows everything.
    """

    def show_end_game(self, status):
        """Answer the end-game prompt with "quit"."""

        return "q"


def play_and_record(
    root: Path,
    *,
    seed: int,
    rules: RuleConfig | None = None,
    preset: str = "classic",
    rounds: int | None = None,
) -> GameRecord:
    """Play one seeded 4-AI game, record it, and read the record back.

    Args:
        root: The records root to write under.
        seed: The RNG seed, which fixes the dealer, every deal and every
            tie the AI strategies break at random.
        rules: The table ruleset, or ``None`` for the §9 defaults.
        preset: The preset name to stamp on the record.
        rounds: Stop after this many rounds instead of playing to the
            target. ``None`` plays the whole game.

    Returns:
        The recorded game, projected.
    """

    random.seed(seed)
    players = [AiPlayer(f"AI {seat.name}", seat) for seat in Position]
    game = Game(players, rules=rules)
    view = RecordingView(SilentView(), root, preset=preset)
    view.attach(game, target_score=game.rules.target_score)
    played = 0
    while not game.check_game_over().game_over:
        game.manage_round(view=view)
        view.on_round_complete(game.current_round, game.scores)
        played += 1
        if rounds is not None and played >= rounds:
            break
    view.close_record()
    (path,) = (Path(root) / "games").glob("*.jsonl")
    return load_game(path)


@pytest.fixture
def recorded_game(tmp_path) -> GameRecord:
    """A whole 4-AI game under the §9 defaults, recorded and read back."""

    return play_and_record(tmp_path, seed=1)


@pytest.fixture
def recorded_tournament_game(tmp_path) -> GameRecord:
    """The same, under the ``tournament`` preset.

    Worth its own fixture because ``tournament`` is the ruleset observed
    games carry, and it is the one that turns ``double_closes_auction``
    on — so a replay under it exercises the early auction close.
    """

    return play_and_record(
        tmp_path,
        seed=7,
        rules=RuleConfig.tournament(),
        preset="tournament",
    )
