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
from contrai_data import (
    BeloteHeld,
    BidMade,
    CardPlayed,
    EndReason,
    GameEnded,
    GameEvent,
    GameRecord,
    GameStarted,
    Header,
    RecordSource,
    RoundDealt,
    RoundRecord,
    Ruleset,
    load_game,
    project,
)

from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer
from contrai_engine.recording import RecordingView

#: A fixed instant for every rebuilt event. Verification never reads a
#: timestamp, and a constant one keeps a rebuilt record diffable.
TS = "2026-09-11T00:00:00Z"


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


def round_events(round_: RoundRecord) -> list[GameEvent]:
    """Rebuild one projected round's events, in file order.

    A projection is lossless for everything verification reads, so a
    round can be turned back into events, mutated, and re-projected —
    which is how a *suspect* record is built here: take a real, legal
    game and break exactly one thing.

    Args:
        round_: A projected round.

    Returns:
        Its ``round_dealt``, bids, plays, belotes and score, in order.
    """

    events: list[GameEvent] = [
        RoundDealt(
            round=round_.number,
            dealer=round_.dealer,
            hands=round_.hands,
            hands_derivation=round_.hands_derivation,
            ts=TS,
        )
    ]
    for seq, bid in enumerate(round_.auction, start=1):
        events.append(
            BidMade(
                round=round_.number,
                seq=seq,
                position=bid.player,
                bid=bid,
                think_ms=None,
                ts=TS,
            )
        )
    for number, trick in enumerate(round_.tricks, start=1):
        for play in trick:
            events.append(
                CardPlayed(
                    round=round_.number,
                    trick=number,
                    position=play.position,
                    card=play.card,
                    derived=False,
                    think_ms=None,
                    ts=TS,
                )
            )
    events.extend(round_.belotes)
    if round_.score is not None:
        events.append(round_.score)
    return events


def record_events(record: GameRecord) -> list[GameEvent]:
    """Rebuild a whole record's events, in file order.

    Args:
        record: A projected record.

    Returns:
        Its header, ``game_started``, every round, and ``game_ended``.
    """

    events: list[GameEvent] = [
        Header(
            format=record.header.format,
            source=RecordSource.ENGINE,
            generator="test",
            game_id=record.header.game_id,
            created_at=TS,
        ),
        GameStarted(
            ruleset=Ruleset(preset=record.preset, config=record.ruleset),
            seats=record.seats,
            observed_from=None,
            ts=TS,
        ),
    ]
    for round_ in record.rounds:
        events.extend(round_events(round_))
    events.append(
        GameEnded(
            totals=record.ended.totals if record.ended else None,
            winner=record.ended.winner if record.ended else None,
            reason=record.ended.reason if record.ended else EndReason.INTERRUPTED,
            ts=TS,
        )
    )
    return events


def rebuilt(record: GameRecord, mutate=None) -> GameRecord:
    """``record``, taken apart into events, optionally changed, put back.

    Args:
        record: The record to rebuild.
        mutate: A callable taking the event list and returning the list
            to project. ``None`` rebuilds unchanged, which is worth its
            own assertion: a mutation test proves nothing if the rebuild
            itself changes the record.

    Returns:
        The re-projected record.
    """

    events = record_events(record)
    if mutate is not None:
        events = mutate(events)
    return project(events)


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
