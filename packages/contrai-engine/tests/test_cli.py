"""Unit tests for the ``contrai`` CLI: flag parsing, seeding, seating, main loop.

:func:`_apply_seed` reseeds the process-wide ``random`` module as a side
effect — that is the behavior under test — so the autouse
``_restore_random_state`` fixture snapshots and restores it, keeping
that side effect from leaking into unrelated tests elsewhere in the
suite. Nothing here reaches ``configure_logging`` with ``debug`` set, so
no log-handler teardown is needed; ``test_log_setup.py`` owns that
concern.

:class:`TestMain` drives :func:`main` against a recording view stub and a
scripted fake game, so the loop's control flow is asserted without any
Rich rendering or blocking input. The real wiring — that a genuine
``RichView`` and ``Game`` still compose — is covered by the
``uv run contrai`` smoke test instead.
"""

from __future__ import annotations

import random
import sys

import pytest

from contrai_core.position import Position
from contrai_core.team_side import TeamSide
from contrai_engine.cli import _apply_seed, _build_game, _parse_args, main
from contrai_engine.model.game import GameOverStatus
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.options import DebugOptions
from contrai_engine.view.theme import DEFAULT_TARGET

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


# --------------------------------------------------------------------------
# ``main`` test doubles
#
# ``main`` is the de-facto controller: it owns the landing → game loop →
# end-game flow and nothing else does. Driving it against the real
# ``RichView`` would block on input and paint the terminal, so the two
# collaborators it constructs — the view and the game — are replaced with
# recorders. What is asserted here is *control flow*: which calls happen,
# in what order, with which arguments.
# --------------------------------------------------------------------------

_UNSET = object()
"""Marks "the CLI omitted this argument" — distinct from every real target."""


class _RecordingConsole:
    """Stand-in for ``RichView.console``; captures what was printed."""

    def __init__(self) -> None:
        self.printed: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.printed.append(" ".join(str(arg) for arg in args))


class _RecordingView:
    """Stand-in for ``RichView`` recording the calls ``main`` makes.

    Only the five methods the CLI actually drives are implemented. That
    is deliberate: if ``main`` ever reaches for a sixth, these tests fail
    with ``AttributeError`` instead of silently passing.
    """

    def __init__(
        self,
        options: DebugOptions | None = None,
        *,
        landing_targets: list[int] | None = None,
        end_game_choices: list[str] | None = None,
    ) -> None:
        self.options = options
        self.console = _RecordingConsole()
        # One ordered log covering view *and* game calls alike (the fake
        # game appends through the view it is handed), so the per-round
        # sequence is assertable and not merely the set of calls made.
        self.events: list[str] = []
        self.landing_received: list[object] = []
        self.attached: list[tuple[object, int]] = []
        self.round_completions: list[tuple[object, dict]] = []
        self.recaps: list[dict] = []
        self.end_game_statuses: list[GameOverStatus] = []
        self._landing_targets = list(landing_targets or [DEFAULT_TARGET])
        self._end_game_choices = list(end_game_choices or ["q"])

    def show_landing(self, selected_target: object = _UNSET) -> int:
        self.events.append("show_landing")
        self.landing_received.append(selected_target)
        return self._landing_targets.pop(0)

    def attach(self, game: object, target_score: int) -> None:
        self.events.append("attach")
        self.attached.append((game, target_score))

    def on_round_complete(self, round_: object, running_scores: dict) -> None:
        self.events.append("on_round_complete")
        self.round_completions.append((round_, running_scores))

    def show_round_recap(
        self,
        round_: object,
        running_scores: dict,
        *,
        is_final: bool = False,
        is_tiebreaker: bool = False,
    ) -> None:
        self.events.append("show_round_recap")
        self.recaps.append(
            {
                "round": round_,
                "scores": running_scores,
                "is_final": is_final,
                "is_tiebreaker": is_tiebreaker,
            }
        )

    def show_end_game(self, status: GameOverStatus) -> str:
        self.events.append("show_end_game")
        self.end_game_statuses.append(status)
        return self._end_game_choices.pop(0)


class _FakeGame:
    """Scripted stand-in for ``Game``: play N rounds, then be over.

    ``check_game_over`` derives its verdict from the number of rounds
    played rather than from a positional call script, because ``main``
    calls it three times per round (loop guard, recap status, end-game
    banner) — a script keyed on call index would break the moment that
    count changes, for reasons having nothing to do with the behavior
    under test.
    """

    def __init__(
        self,
        *,
        rounds_to_play: int = 1,
        tied_after: tuple[int, ...] = (),
        raises: BaseException | None = None,
    ) -> None:
        self.rounds_to_play = rounds_to_play
        self.rounds_played = 0
        self.current_round = "round-0"
        self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}
        self.targets_checked: list[int] = []
        self._tied_after = set(tied_after)
        self._raises = raises

    def check_game_over(self, target_score: int) -> GameOverStatus:
        self.targets_checked.append(target_score)
        over = self.rounds_played >= self.rounds_to_play
        # A tie at/above the target *is* sudden death, so the real
        # ``Game`` never reports it alongside ``game_over``. Mirror that
        # here rather than letting a test script an impossible verdict.
        tied = self.rounds_played in self._tied_after and not over
        return GameOverStatus(
            game_over=over,
            winner=TeamSide.NS if over else None,
            tied_teams=[TeamSide.NS, TeamSide.EW] if tied else None,
            final_scores=dict(self.scores),
        )

    def manage_round(self, view: _RecordingView) -> None:
        view.events.append("manage_round")
        self.rounds_played += 1
        self.current_round = f"round-{self.rounds_played}"
        if self._raises is not None:
            raise self._raises


class _RaisingStream:
    """Stream whose ``reconfigure`` fails, like a locked legacy console."""

    def __init__(self) -> None:
        self.calls = 0

    def reconfigure(self, **kwargs) -> None:
        self.calls += 1
        raise RuntimeError("cannot switch code page")


class _ReconfigurableStream:
    """Stream that accepts ``reconfigure`` and records the encoding asked for."""

    def __init__(self) -> None:
        self.encodings: list[str | None] = []

    def reconfigure(self, **kwargs) -> None:
        self.encodings.append(kwargs.get("encoding"))


class _PlainStream:
    """Stream with no ``reconfigure`` attribute at all — must be skipped."""


class _Harness:
    """Handles on the doubles ``install_cli_doubles`` wired into ``cli``."""

    def __init__(self) -> None:
        self.view: _RecordingView | None = None
        self.build_calls: list[bool] = []
        """One entry per ``_build_game`` call: the ``autoplay`` it got."""


@pytest.fixture
def install_cli_doubles(monkeypatch):
    """Return an installer that swaps ``cli``'s view and game for doubles.

    Also pins ``sys.argv``: ``main`` parses it through ``_parse_args()``
    with no explicit argv, so pytest's own command line would otherwise
    reach ``argparse`` and abort the run.
    """

    def _install(
        *,
        games: list[_FakeGame],
        landing_targets: list[int] | None = None,
        end_game_choices: list[str] | None = None,
        argv: tuple[str, ...] = ("contrai",),
    ) -> _Harness:
        harness = _Harness()
        queue = list(games)

        def _make_view(options: DebugOptions | None = None) -> _RecordingView:
            harness.view = _RecordingView(
                options,
                landing_targets=landing_targets,
                end_game_choices=end_game_choices,
            )
            return harness.view

        def _make_game(autoplay: bool = False) -> _FakeGame:
            harness.build_calls.append(autoplay)
            return queue.pop(0)

        monkeypatch.setattr(sys, "argv", list(argv))
        monkeypatch.setattr("contrai_engine.cli.RichView", _make_view)
        monkeypatch.setattr("contrai_engine.cli._build_game", _make_game)
        return harness

    return _install


class TestMain:
    """``main`` — the landing → game loop → end-game control flow."""

    def test_quit_ends_the_loop_after_one_game(self, install_cli_doubles):
        """``"q"`` stops after a single game, in the documented call order."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
        )

        main()

        assert harness.build_calls == [False]
        assert harness.view.events == [
            "show_landing",
            "attach",
            "manage_round",
            "on_round_complete",
            "show_round_recap",
            "show_end_game",
        ]

    def test_rematch_builds_a_second_game_without_a_new_landing(
        self, install_cli_doubles
    ):
        """``"r"`` reuses the chosen target: fresh game, no second landing."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            landing_targets=[1000],
            end_game_choices=["r", "q"],
        )

        main()

        assert harness.build_calls == [False, False]
        assert harness.view.events.count("show_landing") == 1
        assert harness.view.events.count("attach") == 2
        # Both games run to the same target — that is what "rematch" means.
        assert [target for _, target in harness.view.attached] == [1000, 1000]

    def test_new_game_reruns_the_landing_with_the_current_target(
        self, install_cli_doubles
    ):
        """``"n"`` re-shows the landing, pre-selecting the target in play."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            landing_targets=[1000, 2000],
            end_game_choices=["n", "q"],
        )

        main()

        # First call passes nothing (the screen's own default stands);
        # the second pre-selects whatever the first call returned.
        assert harness.view.landing_received == [_UNSET, 1000]
        assert [target for _, target in harness.view.attached] == [1000, 2000]

    def test_attach_receives_the_target_show_landing_returned(
        self, install_cli_doubles
    ):
        """The landing's return value is the target threaded into the view."""

        game = _FakeGame(rounds_to_play=1)
        harness = install_cli_doubles(
            games=[game],
            landing_targets=[3000],
            end_game_choices=["q"],
        )

        main()

        assert harness.view.attached == [(game, 3000)]
        # ...and the same target reaches every ``check_game_over`` call.
        assert set(game.targets_checked) == {3000}

    def test_recap_flags_are_derived_from_check_game_over(
        self, install_cli_doubles
    ):
        """``is_final``/``is_tiebreaker`` track the status of each round."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=2, tied_after=(1,))],
            end_game_choices=["q"],
        )

        main()

        recaps = harness.view.recaps
        assert len(recaps) == 2
        # Round 1 left the teams level at/above target: sudden death.
        assert recaps[0]["is_final"] is False
        assert recaps[0]["is_tiebreaker"] is True
        # Round 2 clinched it.
        assert recaps[1]["is_final"] is True
        assert recaps[1]["is_tiebreaker"] is False

    def test_each_round_repeats_the_manage_complete_recap_sequence(
        self, install_cli_doubles
    ):
        """The three per-round calls recur, in order, once per round."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=2)],
            end_game_choices=["q"],
        )

        main()

        assert harness.view.events == [
            "show_landing",
            "attach",
            "manage_round",
            "on_round_complete",
            "show_round_recap",
            "manage_round",
            "on_round_complete",
            "show_round_recap",
            "show_end_game",
        ]
        # Each recap sees the round that just finished, not a stale one.
        assert [recap["round"] for recap in harness.view.recaps] == [
            "round-1",
            "round-2",
        ]

    def test_round_completion_receives_the_running_scores(
        self, install_cli_doubles
    ):
        """``on_round_complete`` is handed the game's live score mapping."""

        game = _FakeGame(rounds_to_play=1)
        harness = install_cli_doubles(games=[game], end_game_choices=["q"])

        main()

        round_, scores = harness.view.round_completions[0]
        assert round_ == "round-1"
        assert scores is game.scores

    def test_end_game_receives_the_final_status(self, install_cli_doubles):
        """The banner is fed a genuinely game-over status, not a stale one."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
        )

        main()

        status = harness.view.end_game_statuses[0]
        assert status.game_over is True
        assert status.winner is TeamSide.NS

    def test_autoplay_flag_reaches_both_the_view_and_the_seating(
        self, install_cli_doubles
    ):
        """``--autoplay`` is threaded into ``RichView`` and ``_build_game``."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
            argv=("contrai", "--autoplay"),
        )

        main()

        assert harness.build_calls == [True]
        assert harness.view.options == DebugOptions(autoplay=True)

    @pytest.mark.parametrize(
        "error", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "ctrl-d"]
    )
    def test_interrupting_the_loop_says_goodbye(
        self, install_cli_doubles, error
    ):
        """Ctrl-C and Ctrl-D both leave through the same graceful exit."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1, raises=error)],
            end_game_choices=["q"],
        )

        main()  # must not propagate

        assert "Goodbye." in harness.view.console.printed[-1]
        # The end-game banner is never reached on an interrupt.
        assert "show_end_game" not in harness.view.events


class TestMainStreamReconfigure:
    """``main``'s UTF-8 stdout/stderr fix-up for legacy Windows consoles."""

    @pytest.fixture
    def _one_quiet_game(self, install_cli_doubles):
        """A one-round, immediately-quit game, so only the streams matter."""

        return install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
        )

    def test_reconfigurable_streams_are_switched_to_utf8(
        self, monkeypatch, _one_quiet_game
    ):
        stdout, stderr = _ReconfigurableStream(), _ReconfigurableStream()
        monkeypatch.setattr(sys, "stdout", stdout)
        monkeypatch.setattr(sys, "stderr", stderr)

        main()

        assert stdout.encodings == ["utf-8"]
        assert stderr.encodings == ["utf-8"]

    def test_a_failing_reconfigure_is_swallowed(
        self, monkeypatch, _one_quiet_game
    ):
        """A console that refuses the switch must not stop the game."""

        stream = _RaisingStream()
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", stream)

        main()  # must not propagate

        assert stream.calls == 2
        assert _one_quiet_game.view.events[-1] == "show_end_game"

    def test_a_stream_without_reconfigure_is_skipped(
        self, monkeypatch, _one_quiet_game
    ):
        """Streams predating ``reconfigure`` are left alone, not crashed on."""

        monkeypatch.setattr(sys, "stdout", _PlainStream())
        monkeypatch.setattr(sys, "stderr", _PlainStream())

        main()  # must not propagate

        assert _one_quiet_game.view.events[-1] == "show_end_game"
