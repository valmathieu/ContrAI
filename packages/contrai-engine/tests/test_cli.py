"""Unit tests for the ``contrai`` CLI: flag parsing, seeding, and seating.

:func:`_apply_seed` reseeds the process-wide ``random`` module as a side
effect — that is the behavior under test — so the autouse
``_restore_random_state`` fixture snapshots and restores it, keeping
that side effect from leaking into unrelated tests elsewhere in the
suite. Nothing here reaches ``configure_logging``, so no log-handler
teardown is needed; ``test_log_setup.py`` owns that concern.
"""

from __future__ import annotations

import random

import pytest

from contrai_core.position import Position
from contrai_engine.cli import _apply_seed, _build_game, _parse_args
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.options import DebugOptions

@pytest.fixture(autouse=True)
def _restore_random_state():
    """Snapshot and restore the global ``random`` module state.

    ``_apply_seed`` reseeds the process-wide RNG as a side effect —
    exactly the behavior under test — so without this fixture a seed
    applied by one test here would leak into unrelated tests elsewhere
    in the suite that also draw from the global ``random`` module.
    """

    state = random.getstate()
    yield
    random.setstate(state)


class TestParseArgs:
    """``_parse_args`` — argparse wiring for the three debug-mode flags."""

    def test_no_flags_returns_all_off_defaults(self):
        """The back-compat anchor: an empty argv parses to ``DebugOptions()``."""

        assert _parse_args([]) == DebugOptions()

    def test_debug_flag_alone(self):
        assert _parse_args(["--debug"]) == DebugOptions(debug=True)

    def test_seed_flag_alone(self):
        assert _parse_args(["--seed", "42"]) == DebugOptions(seed=42)

    def test_autoplay_flag_alone(self):
        assert _parse_args(["--autoplay"]) == DebugOptions(autoplay=True)

    def test_all_three_flags_combined(self):
        result = _parse_args(["--debug", "--seed", "7", "--autoplay"])
        assert result == DebugOptions(debug=True, autoplay=True, seed=7)

    def test_seed_value_is_coerced_to_int(self):
        result = _parse_args(["--seed", "123"])
        assert result.seed == 123
        assert isinstance(result.seed, int)

    def test_non_integer_seed_exits(self):
        """``argparse``'s ``type=int`` rejects a non-numeric ``--seed``."""

        with pytest.raises(SystemExit):
            _parse_args(["--seed", "not-a-number"])


class TestApplySeed:
    """``_apply_seed`` — generate-then-seed ordering and RNG side effects."""

    def test_explicit_seed_reproduces_a_fresh_random_seed_stream(self):
        """An explicit seed is applied as-is and matches a fresh ``random.seed(N)``."""

        result = _apply_seed(DebugOptions(seed=99))
        draws = [random.random() for _ in range(5)]

        random.seed(99)
        expected = [random.random() for _ in range(5)]

        assert result.seed == 99
        assert draws == expected

    def test_debug_without_seed_generates_and_records_one(self):
        """``--debug`` alone generates a seed, applies it, and records it back."""

        result = _apply_seed(DebugOptions(debug=True))
        assert result.seed is not None

        draws = [random.random() for _ in range(5)]
        random.seed(result.seed)
        expected = [random.random() for _ in range(5)]
        assert draws == expected

    def test_debug_with_explicit_seed_keeps_the_explicit_seed(self):
        """An explicit seed wins over generation even when ``--debug`` is set."""

        result = _apply_seed(DebugOptions(debug=True, seed=5))
        assert result.seed == 5

    def test_no_flags_leaves_random_state_untouched(self):
        """With neither flag, the global RNG state is not consumed at all."""

        before = random.getstate()
        result = _apply_seed(DebugOptions())
        after = random.getstate()

        assert result == DebugOptions()
        assert before == after


class TestBuildGame:
    """``_build_game`` — default human seating vs. 4-AI autoplay."""

    def test_default_seating_has_human_at_south(self):
        game = _build_game()
        assert isinstance(game.players_by_position[Position.SOUTH], HumanPlayer)
        for seat in (Position.NORTH, Position.EAST, Position.WEST):
            assert isinstance(game.players_by_position[seat], AiPlayer)

    def test_autoplay_seats_four_ai_players(self):
        game = _build_game(autoplay=True)
        for seat in Position:
            player = game.players_by_position[seat]
            assert isinstance(player, AiPlayer)
            # ``is_human`` is the property the round and view dispatch
            # gates actually read: a truthy value at any seat would put
            # a blocking prompt back into an unattended run.
            assert player.is_human is False


class TestSeedDeterminism:
    """Same seed -> identical per-seat hands and dealer across two fresh games."""

    def test_same_seed_reproduces_hands_and_dealer(self):
        _apply_seed(DebugOptions(seed=2024))
        game_a = _build_game()
        game_a.start_new_round()
        hands_a = {
            seat: list(player.hand)
            for seat, player in game_a.players_by_position.items()
        }
        dealer_a = game_a.dealer.position

        _apply_seed(DebugOptions(seed=2024))
        game_b = _build_game()
        game_b.start_new_round()
        hands_b = {
            seat: list(player.hand)
            for seat, player in game_b.players_by_position.items()
        }
        dealer_b = game_b.dealer.position

        assert hands_a == hands_b
        assert dealer_a == dealer_b
