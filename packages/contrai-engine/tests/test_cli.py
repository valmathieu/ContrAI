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

import argparse
import json
import random
import sys
from pathlib import Path

import pytest

from contrai_core.position import Position
from contrai_core.rule_config import PRESETS, RuleConfig
from contrai_core.team_side import TeamSide
from contrai_data import EndReason, GameEnded, read_events
from contrai_engine import cli as cli_module
from contrai_engine.cli import (
    _apply_seed,
    _build_game,
    _normalise_argv,
    _parse_args,
    _parse_argv,
    _record_paths,
    _run_replay,
    _run_verify,
    main,
)
from contrai_engine.model.game import GameOverStatus
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.model.round.components import Mark
from contrai_engine.model.round.scoring import RoundScore
from contrai_engine.options import DebugOptions, TableAids
from contrai_engine.recording import RecordingView, RecordRequest
from contrai_engine.ruleset import TableSetup, load_setup, save_setup, setup_path
from contrai_engine.view.parsing import RoundPick

@pytest.fixture
def contrai_home(tmp_path):
    """The scratch directory the last-setup cache is redirected into."""

    return tmp_path / "contrai-home"


@pytest.fixture(autouse=True)
def _isolate_contrai_home(contrai_home, monkeypatch):
    """Point the last-setup cache at a scratch directory.

    main remembers the setup a player left the landing screen with, so
    without this every test driving it would write into the real
    ~/.contrai — and every test reading it back would see whatever the
    developer running the suite last played.
    """

    monkeypatch.setenv("CONTRAI_HOME", str(contrai_home))


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
    """``_parse_args`` — argparse wiring for the debug-mode and setup flags.

    It returns a ``(DebugOptions, TableSetup)`` pair: the debug flags, and
    the table setup ``--rules`` / ``--preset`` / ``--no-live-score``
    resolved to — the ruleset the game is built under plus the interface
    aids the view reads.
    """

    def test_no_flags_returns_all_off_defaults(self):
        """The back-compat anchor: an empty argv parses to the defaults."""

        assert _parse_args([]) == (DebugOptions(), TableSetup(), RecordRequest())

    def test_debug_flag_alone(self):
        assert _parse_args(["--debug"]) == (
            DebugOptions(debug=True), TableSetup(), RecordRequest(),
        )

    def test_seed_flag_alone(self):
        assert _parse_args(["--seed", "42"]) == (
            DebugOptions(seed=42), TableSetup(), RecordRequest(),
        )

    def test_autoplay_flag_alone(self):
        assert _parse_args(["--autoplay"]) == (
            DebugOptions(autoplay=True), TableSetup(), RecordRequest(),
        )

    def test_all_three_flags_combined(self):
        result = _parse_args(["--debug", "--seed", "7", "--autoplay"])
        assert result == (
            DebugOptions(debug=True, autoplay=True, seed=7), TableSetup(),
            RecordRequest(),
        )

    def test_seed_value_is_coerced_to_int(self):
        options, _, _ = _parse_args(["--seed", "123"])
        assert options.seed == 123
        assert isinstance(options.seed, int)

    def test_non_integer_seed_exits(self):
        """``argparse``'s ``type=int`` rejects a non-numeric ``--seed``."""

        with pytest.raises(SystemExit):
            _parse_args(["--seed", "not-a-number"])

    def test_preset_classic_resolves_to_the_defaults(self):
        assert _parse_args(["--preset", "classic"]) == (
            DebugOptions(), TableSetup(origin="classic"), RecordRequest(),
        )

    def test_no_live_score_switches_the_aid_off(self):
        """``--no-live-score`` is the §9.7 aid's only CLI surface."""

        assert _parse_args(["--no-live-score"])[1].aids == TableAids(
            live_round_score=False
        )

    def test_live_score_is_on_without_the_flag(self):
        assert _parse_args([])[1].aids.live_round_score is True

    def test_no_live_score_is_independent_of_the_ruleset_flags(self):
        """The aid is a view setting, so it composes with any ruleset."""

        options, setup, _ = _parse_args(
            ["--preset", "classic", "--no-live-score"]
        )
        assert (options, setup.rules) == (DebugOptions(), RuleConfig())
        assert setup.aids == TableAids(live_round_score=False)

    def test_rules_file_is_loaded(self, tmp_path):
        path = tmp_path / "table.toml"
        path.write_text("[general]\ntarget_score = 1000\n", encoding="utf-8")

        setup = _parse_args(["--rules", str(path)])[1]
        assert setup.rules == RuleConfig(target_score=1000)
        assert setup.origin == "table.toml"

    def test_rules_and_preset_are_mutually_exclusive(self, tmp_path):
        """``argparse``'s own group rejects the pair before ``resolve_rules``."""

        path = tmp_path / "table.toml"
        path.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--rules", str(path), "--preset", "classic"])
        assert excinfo.value.code == 2

    def test_unknown_preset_exits(self):
        """``choices`` rejects an unknown preset name."""

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--preset", "house"])
        assert excinfo.value.code == 2

    def test_missing_rules_file_exits_with_usage_error(self, tmp_path, capsys):
        """An unreadable file is a usage error, not a traceback."""

        missing = tmp_path / "nope.toml"

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--rules", str(missing)])
        assert excinfo.value.code == 2
        assert "nope.toml" in capsys.readouterr().err

    def test_unknown_key_in_rules_file_exits_with_usage_error(self, tmp_path, capsys):
        path = tmp_path / "typo.toml"
        path.write_text("[general]\ntarget_scor = 2000\n", encoding="utf-8")

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--rules", str(path)])
        assert excinfo.value.code == 2
        assert "unknown key" in capsys.readouterr().err

    def test_invalid_config_in_rules_file_exits_with_usage_error(self, tmp_path, capsys):
        """A well-formed file naming an impossible table is still a usage error."""

        path = tmp_path / "impossible.toml"
        path.write_text(
            "[scoring]\nmark_made_points = false\nmark_announced_points = false\n",
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--rules", str(path)])
        assert excinfo.value.code == 2
        assert "mark_made_points" in capsys.readouterr().err


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

    def test_rules_are_handed_to_the_game(self):
        rules = RuleConfig(target_score=1000)
        assert _build_game(rules=rules).rules is rules

    def test_default_rules_are_classic(self):
        assert _build_game().rules == RuleConfig()


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
        aids: TableAids | None = None,
        landing_setups: list[TableSetup] | None = None,
        end_game_choices: list[str] | None = None,
    ) -> None:
        self.options = options
        self.aids = aids
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
        # ``None`` means "echo whatever you were handed" — the real
        # screen's ``[Enter]``, i.e. deal the setup on display. A list
        # scripts a player who edited something instead.
        self._landing_setups = list(landing_setups) if landing_setups else None
        self._end_game_choices = list(end_game_choices or ["q"])

    def show_landing(self, selected: object = _UNSET) -> TableSetup:
        self.events.append("show_landing")
        self.landing_received.append(selected)
        if self._landing_setups is None:
            return selected if isinstance(selected, TableSetup) else TableSetup()
        return self._landing_setups.pop(0)

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
        belote_gated: TeamSide | None = None,
    ) -> None:
        self.events.append("show_round_recap")
        self.recaps.append(
            {
                "round": round_,
                "scores": running_scores,
                "is_final": is_final,
                "is_tiebreaker": is_tiebreaker,
                "belote_gated": belote_gated,
            }
        )

    def show_end_game(self, status: GameOverStatus) -> str:
        self.events.append("show_end_game")
        self.end_game_statuses.append(status)
        return self._end_game_choices.pop(0)


class _FakeRound:
    """Scripted stand-in for ``Round``: a number and an all-pass score.

    ``main`` only ever passes the round through to the view, but a
    :class:`RecordingView` wrapped around that view reads a score line
    off it — so the double carries the contractless one an all-pass
    publishes, which is the simplest score a record will accept. It also
    carries the table ruleset, which the recorder reads to write each
    mark as the sheet would carry it; a real ``Round`` always has one.
    """

    contract = None
    rules = RuleConfig()

    def __init__(self, number: int) -> None:
        self.round_number = number
        self.round_score = RoundScore(
            scores={side: 0 for side in TeamSide},
            contract_made=None,
            unannounced_slam=None,
            marks={side: Mark(0, 0) for side in TeamSide},
            belote_points={side: 0 for side in TeamSide},
            card_points={side: 0 for side in TeamSide},
            last_trick_side=None,
            multiplier=1,
        )

    def __repr__(self) -> str:
        return f"round-{self.round_number}"


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
        belote_gated_after: tuple[int, ...] = (),
        raises: BaseException | None = None,
    ) -> None:
        self.rounds_to_play = rounds_to_play
        self.rounds_played = 0
        self.current_round: object = _FakeRound(0)
        self.scores = {TeamSide.NS: 0, TeamSide.EW: 0}
        self.targets_checked: list[int] = []
        # One entry per ``manage_round``: the object ``main`` actually
        # drove. Whether that is the view or a wrapper around it is the
        # whole question the recorder-wiring tests ask.
        self.views_seen: list[object] = []
        self._tied_after = set(tied_after)
        self._belote_gated_after = set(belote_gated_after)
        self._raises = raises

    #: Four real seats: ``RecordingView.attach`` reads a position and a
    #: strategy pair off each one, and a record's seating is exactly that.
    players = [AiPlayer(seat.value, position=seat) for seat in Position]

    rules: RuleConfig = RuleConfig()
    """The ruleset ``cli`` folds the landing pick onto; ``_make_game``
    replaces it with whatever the real call was handed, mirroring the real
    ``Game``, which owns its target from construction on."""

    def check_game_over(self) -> GameOverStatus:
        self.targets_checked.append(self.rules.target_score)
        over = self.rounds_played >= self.rounds_to_play
        # A tie at/above the target *is* sudden death, so the real
        # ``Game`` never reports it alongside ``game_over``. Mirror that
        # here rather than letting a test script an impossible verdict.
        tied = self.rounds_played in self._tied_after and not over
        # Same invariant for the §8 belote gate: a side it holds back has
        # not won, so the real ``Game`` never names one alongside a win.
        gated = self.rounds_played in self._belote_gated_after and not over
        return GameOverStatus(
            game_over=over,
            winner=TeamSide.NS if over else None,
            tied_teams=[TeamSide.NS, TeamSide.EW] if tied else None,
            final_scores=dict(self.scores),
            belote_gated=TeamSide.NS if gated else None,
        )

    def manage_round(self, view: _RecordingView) -> None:
        view.events.append("manage_round")
        self.views_seen.append(view)
        self.rounds_played += 1
        self.current_round = _FakeRound(self.rounds_played)
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
        self.rules_seen: list[RuleConfig | None] = []
        """One entry per ``_build_game`` call: the ``rules`` it got."""


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
        landing_setups: list[TableSetup] | None = None,
        end_game_choices: list[str] | None = None,
        argv: tuple[str, ...] = ("contrai",),
    ) -> _Harness:
        harness = _Harness()
        queue = list(games)

        def _make_view(
            options: DebugOptions | None = None,
            aids: TableAids | None = None,
        ) -> _RecordingView:
            harness.view = _RecordingView(
                options,
                aids=aids,
                landing_setups=landing_setups,
                end_game_choices=end_game_choices,
            )
            return harness.view

        def _make_game(
            autoplay: bool = False, rules: RuleConfig | None = None
        ) -> _FakeGame:
            harness.build_calls.append(autoplay)
            harness.rules_seen.append(rules)
            game = queue.pop(0)
            if rules is not None:
                game.rules = rules
            return game

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
        """``"r"`` reuses the chosen setup: fresh game, no second landing."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            landing_setups=[TableSetup(rules=RuleConfig(target_score=1000))],
            end_game_choices=["r", "q"],
        )

        main()

        assert harness.build_calls == [False, False]
        assert harness.view.events.count("show_landing") == 1
        assert harness.view.events.count("attach") == 2
        # Both games run under the same setup — that is what "rematch" means.
        assert [target for _, target in harness.view.attached] == [1000, 1000]

    def test_new_game_reruns_the_landing_with_the_current_setup(
        self, install_cli_doubles
    ):
        """``"n"`` re-shows the landing, opening on the setup in play."""

        first = TableSetup(rules=RuleConfig(target_score=1000))
        second = TableSetup(rules=RuleConfig(target_score=2000))
        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            landing_setups=[first, second],
            end_game_choices=["n", "q"],
        )

        main()

        # First call opens on what the flags resolved to; the second opens
        # on whatever the first call returned.
        assert harness.view.landing_received == [TableSetup(), first]
        assert [target for _, target in harness.view.attached] == [1000, 2000]

    def test_the_game_is_built_under_the_setup_the_landing_returned(
        self, install_cli_doubles
    ):
        """The screen's edit is what reaches the model — the whole point of
        the setup screen, and the one thing no unit test above proves."""

        game = _FakeGame(rounds_to_play=1)
        rules = RuleConfig(target_score=3000, extended_trump_choices=True)
        harness = install_cli_doubles(
            games=[game],
            landing_setups=[TableSetup(rules=rules, origin="custom")],
            end_game_choices=["q"],
        )

        main()

        assert harness.rules_seen == [rules]
        # ...and the target is the model's own number, not one the loop
        # carries alongside the game: every ``check_game_over`` reads it
        # off ``game.rules``.
        assert harness.view.attached == [(game, 3000)]
        assert set(game.targets_checked) == {3000}

    def test_the_aid_the_landing_returned_is_repointed_on_the_view(
        self, install_cli_doubles
    ):
        """The aids never reach the model, so the CLI hands them to the
        view directly once the screen is done with them."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            landing_setups=[
                TableSetup(aids=TableAids(live_round_score=False))
            ],
            end_game_choices=["q"],
        )

        main()

        assert harness.view.aids == TableAids(live_round_score=False)

    def test_recap_flags_are_derived_from_check_game_over(
        self, install_cli_doubles
    ):
        """The three recap flags track the status of each round."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=3, tied_after=(1,),
                             belote_gated_after=(2,))],
            end_game_choices=["q"],
        )

        main()

        recaps = harness.view.recaps
        assert len(recaps) == 3
        # Round 1 left the teams level at/above target: sudden death.
        assert recaps[0]["is_final"] is False
        assert recaps[0]["is_tiebreaker"] is True
        assert recaps[0]["belote_gated"] is None
        # Round 2 put N-S past the target on belote the gate holds back.
        assert recaps[1]["is_final"] is False
        assert recaps[1]["is_tiebreaker"] is False
        assert recaps[1]["belote_gated"] is TeamSide.NS
        # Round 3 clinched it.
        assert recaps[2]["is_final"] is True
        assert recaps[2]["is_tiebreaker"] is False
        assert recaps[2]["belote_gated"] is None

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
        assert [
            recap["round"].round_number for recap in harness.view.recaps
        ] == [1, 2]

    def test_round_completion_receives_the_running_scores(
        self, install_cli_doubles
    ):
        """``on_round_complete`` is handed the game's live score mapping."""

        game = _FakeGame(rounds_to_play=1)
        harness = install_cli_doubles(games=[game], end_game_choices=["q"])

        main()

        round_, scores = harness.view.round_completions[0]
        assert round_.round_number == 1
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

    def test_default_run_builds_the_game_under_the_classic_ruleset(
        self, install_cli_doubles
    ):
        """No ruleset flag: the game is still built under an explicit config."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
        )

        main()

        assert harness.rules_seen == [RuleConfig()]

    def test_preset_flag_reaches_the_seating(self, install_cli_doubles):
        """``--preset`` resolves once and reaches ``_build_game``."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
            argv=("contrai", "--preset", "classic"),
        )

        main()

        assert harness.rules_seen == [PRESETS["classic"]]

    def test_no_live_score_reaches_the_view(self, install_cli_doubles):
        """The aid is constructed into the view, not carried by the model."""

        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
            argv=("contrai", "--no-live-score"),
        )

        main()

        assert harness.view.aids == TableAids(live_round_score=False)

    def test_default_run_leaves_the_aid_on(self, install_cli_doubles):
        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
        )

        main()

        assert harness.view.aids == TableAids()

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


class TestLastSetupPersistence:
    """``main`` remembers the setup a player leaves the landing screen with.

    ``CONTRAI_HOME`` points at a scratch directory for every test in this
    file (the autouse ``_isolate_contrai_home`` fixture), so nothing here
    touches the real ``~/.contrai``.
    """

    def test_a_run_writes_the_setup_it_dealt_under(
        self, install_cli_doubles, contrai_home
    ):
        setup = TableSetup(
            rules=RuleConfig(target_score=3000),
            aids=TableAids(live_round_score=False),
            origin="custom",
        )
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            landing_setups=[setup],
            end_game_choices=["q"],
        )

        main()

        remembered = load_setup(contrai_home / "last-setup.toml")
        assert remembered.rules == setup.rules
        assert remembered.aids == setup.aids

    def test_the_file_is_written_where_setup_path_says(
        self, install_cli_doubles, contrai_home
    ):
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)], end_game_choices=["q"]
        )

        main()

        assert setup_path().is_file()
        assert setup_path().parent == contrai_home

    def test_a_new_game_remembers_the_second_pick_too(
        self, install_cli_doubles, contrai_home
    ):
        """``[n]`` re-opens the screen, so what it returns is remembered."""
        second = TableSetup(rules=RuleConfig(target_score=500))
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            landing_setups=[TableSetup(), second],
            end_game_choices=["n", "q"],
        )

        main()

        assert load_setup(setup_path()).rules == second.rules

    def test_autoplay_writes_nothing(self, install_cli_doubles, contrai_home):
        """An unattended run must not rewrite what a player chose."""
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
            argv=("contrai", "--autoplay"),
        )

        main()

        assert not setup_path().exists()

    def test_an_unwritable_home_does_not_stop_the_game(
        self, install_cli_doubles, monkeypatch
    ):
        """Persistence is a convenience; failing to save is not fatal."""

        def _boom(*_args, **_kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr("contrai_engine.cli.save_setup", _boom)
        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)], end_game_choices=["q"]
        )

        main()  # must not propagate

        assert harness.view.events[-1] == "show_end_game"

    def test_a_bare_start_is_still_the_catalogue_defaults(
        self, install_cli_doubles, contrai_home
    ):
        """A remembered setup is offered, never applied: what the flags
        resolve to is what the landing screen opens on."""
        save_setup(
            setup_path(),
            TableSetup(rules=RuleConfig(target_score=500)),
        )
        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)], end_game_choices=["q"]
        )

        main()

        assert harness.view.landing_received == [TableSetup()]
        assert harness.rules_seen == [RuleConfig()]


class TestRecordFlags:
    """``--record`` / ``--no-record`` — the third thing ``_parse_args`` returns."""

    def test_absent_by_default(self):
        _, _, record = _parse_args([])
        assert record == RecordRequest()
        assert record.resolve(TableAids()) is None

    def test_bare_flag_means_the_default_root(self, contrai_home):
        _, _, record = _parse_args(["--record"])
        assert record.resolve(TableAids()) == contrai_home / "records"

    def test_flag_with_a_directory(self, tmp_path):
        _, _, record = _parse_args(["--record", str(tmp_path)])
        assert record.resolve(TableAids()) == tmp_path

    def test_no_record_beats_the_knob(self):
        _, _, record = _parse_args(["--no-record"])
        assert record.resolve(TableAids(record=True)) is None

    def test_the_knob_decides_when_no_flag_is_given(self, contrai_home):
        _, _, record = _parse_args([])
        assert record.resolve(TableAids(record=True)) == contrai_home / "records"

    def test_the_two_flags_are_mutually_exclusive(self):
        with pytest.raises(SystemExit) as excinfo:
            _parse_args(["--record", "--no-record"])
        assert excinfo.value.code == 2

    def test_the_flag_composes_with_the_ruleset_flags(self, tmp_path):
        options, setup, record = _parse_args(
            ["--preset", "classic", "--record", str(tmp_path)]
        )
        assert (options, setup.rules) == (DebugOptions(), RuleConfig())
        assert record.resolve(TableAids()) == tmp_path


def _games_under(root):
    """Every record file under a records root, newest name last."""
    return sorted((root / "games").glob("*.jsonl"))


class TestRecorderWiring:
    """``main`` holds the wrapper, so the CLI's own hooks are recorded too."""

    def test_main_does_not_wrap_the_view_by_default(self, install_cli_doubles):
        game = _FakeGame(rounds_to_play=1)
        harness = install_cli_doubles(games=[game], end_game_choices=["q"])

        main()

        assert game.views_seen == [harness.view]

    def test_main_wraps_the_view_when_recording(
        self, install_cli_doubles, tmp_path
    ):
        game = _FakeGame(rounds_to_play=1)
        harness = install_cli_doubles(
            games=[game],
            end_game_choices=["q"],
            argv=("contrai", "--record", str(tmp_path)),
        )

        main()

        (driver,) = game.views_seen
        assert isinstance(driver, RecordingView)
        assert driver._record_inner is harness.view
        # The real view still saw every hook the CLI issues.
        assert harness.view.events == [
            "show_landing",
            "attach",
            "manage_round",
            "on_round_complete",
            "show_round_recap",
            "show_end_game",
        ]
        assert len(_games_under(tmp_path)) == 1

    def test_the_landing_screen_is_not_recorded(
        self, install_cli_doubles, tmp_path
    ):
        """``show_landing`` goes to the real view: it is not part of a game."""
        harness = install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
            argv=("contrai", "--record", str(tmp_path)),
        )

        main()

        assert harness.view.events[0] == "show_landing"
        assert harness.view.landing_received == [TableSetup()]

    def test_no_record_writes_nothing(self, install_cli_doubles, contrai_home):
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            landing_setups=[TableSetup(aids=TableAids(record=True))],
            end_game_choices=["q"],
            argv=("contrai", "--no-record"),
        )

        main()

        assert not (contrai_home / "records").exists()

    def test_the_knob_alone_switches_recording_on(
        self, install_cli_doubles, contrai_home
    ):
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            landing_setups=[TableSetup(aids=TableAids(record=True))],
            end_game_choices=["q"],
        )

        main()

        assert len(_games_under(contrai_home / "records")) == 1

    def test_the_recorder_is_rebuilt_after_a_new_game(
        self, install_cli_doubles, contrai_home
    ):
        """Toggling the knob on the landing screen takes the next deal."""
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            landing_setups=[
                TableSetup(),
                TableSetup(aids=TableAids(record=True)),
            ],
            end_game_choices=["n", "q"],
        )

        main()

        # The first game did not record; the second did.
        assert len(_games_under(contrai_home / "records")) == 1

    def test_a_rematch_opens_a_second_record(
        self, install_cli_doubles, tmp_path
    ):
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1), _FakeGame(rounds_to_play=1)],
            end_game_choices=["r", "q"],
            argv=("contrai", "--record", str(tmp_path)),
        )

        main()

        assert len(_games_under(tmp_path)) == 2

    def test_an_interrupt_closes_the_record(self, install_cli_doubles, tmp_path):
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1, raises=KeyboardInterrupt())],
            argv=("contrai", "--record", str(tmp_path)),
        )

        main()  # must not propagate

        (path,) = _games_under(tmp_path)
        ended = [
            event
            for event in read_events(path).events
            if isinstance(event, GameEnded)
        ]
        assert [event.reason for event in ended] == [EndReason.INTERRUPTED]

    def test_quitting_leaves_no_second_game_ended(
        self, install_cli_doubles, tmp_path
    ):
        install_cli_doubles(
            games=[_FakeGame(rounds_to_play=1)],
            end_game_choices=["q"],
            argv=("contrai", "--record", str(tmp_path)),
        )

        main()

        (path,) = _games_under(tmp_path)
        ended = [
            event
            for event in read_events(path).events
            if isinstance(event, GameEnded)
        ]
        assert [event.reason for event in ended] == [EndReason.TARGET_REACHED]


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


class TestNormaliseArgv:
    """``play`` is inserted when the arguments do not name a subcommand.

    Which is the whole compatibility story: every invocation that worked
    before subcommands existed is one with the word left out.
    """

    def test_nothing_becomes_play(self):
        assert _normalise_argv([]) == ["play"]

    def test_flags_become_play_flags(self):
        assert _normalise_argv(["--autoplay", "--seed", "7"]) == [
            "play",
            "--autoplay",
            "--seed",
            "7",
        ]

    def test_a_named_subcommand_is_left_alone(self):
        assert _normalise_argv(["verify", "a.jsonl"]) == ["verify", "a.jsonl"]

    def test_play_written_out_is_left_alone(self):
        assert _normalise_argv(["play", "--debug"]) == ["play", "--debug"]

    def test_it_does_not_mutate_its_argument(self):
        argv = ["--debug"]

        _normalise_argv(argv)

        assert argv == ["--debug"]


class TestParseArgvResolvesSysArgv:
    """``sys.argv`` is resolved *before* normalising, never after."""

    def test_no_argv_reads_sys_argv_and_normalises_it(self, monkeypatch):
        # ``main`` calls ``_parse_argv()`` with nothing. Normalising only
        # an explicitly passed list would leave this path un-normalised,
        # and ``contrai --autoplay`` would exit 2.
        monkeypatch.setattr(sys, "argv", ["contrai", "--autoplay"])

        args, _ = _parse_argv()

        assert args.command == "play"
        assert args.autoplay is True

    def test_a_bare_invocation_still_names_the_play_command(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai"])

        args, _ = _parse_argv()

        assert args.command == "play"
        assert args.debug is False

    def test_a_verify_invocation_is_recognised(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "verify", "a.jsonl"])

        args, _ = _parse_argv()

        assert args.command == "verify"
        assert args.paths == [Path("a.jsonl")]

    def test_the_play_subparser_comes_back_for_error_reporting(self):
        # A bad ``--rules`` must print ``usage: contrai play …`` rather
        # than top-level usage, which names none of the flags typed.
        _, play = _parse_argv([])

        assert play.prog == "contrai play"


class TestVerifyArguments:
    def test_paths_are_required(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _parse_argv(["verify"])

        assert excinfo.value.code == 2

    def test_several_paths_are_accepted(self):
        args, _ = _parse_argv(["verify", "a.jsonl", "b.jsonl"])

        assert args.paths == [Path("a.jsonl"), Path("b.jsonl")]

    def test_json_and_out_default_off(self):
        args, _ = _parse_argv(["verify", "a.jsonl"])

        assert args.json is False
        assert args.out is None
        assert args.no_write is False

    def test_out_takes_a_directory(self, tmp_path):
        args, _ = _parse_argv(["verify", "a.jsonl", "--out", str(tmp_path)])

        assert args.out == tmp_path

    def test_a_bare_record_path_points_at_verify(self, capsys):
        # Without the hint this normalises to ``play a.jsonl`` and
        # argparse reports an unrecognised argument, which is true and
        # useless.
        with pytest.raises(SystemExit) as excinfo:
            _parse_argv(["some-game.jsonl"])

        assert excinfo.value.code == 2
        assert "contrai verify some-game.jsonl" in capsys.readouterr().err

    def test_an_existing_file_points_at_verify_too(self, tmp_path, capsys):
        record = tmp_path / "record"
        record.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit):
            _parse_argv([str(record)])

        assert "contrai verify" in capsys.readouterr().err

    def test_an_ordinary_bad_flag_is_untouched(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _parse_argv(["--nope"])

        assert excinfo.value.code == 2
        assert "contrai verify" not in capsys.readouterr().err


class TestReplayArguments:
    def test_a_replay_invocation_is_left_alone(self):
        assert _normalise_argv(["replay", "a.jsonl"]) == [
            "replay",
            "a.jsonl",
        ]

    def test_a_replay_invocation_is_recognised(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "replay", "a.jsonl"])

        args, _ = _parse_argv()

        assert args.command == "replay"
        assert args.path == Path("a.jsonl")
        assert args.round is None

    def test_round_takes_a_number(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["contrai", "replay", "a.jsonl", "--round", "9"]
        )

        args, _ = _parse_argv()

        assert args.round == 9

    def test_the_path_is_required(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "replay"])

        with pytest.raises(SystemExit) as excinfo:
            _parse_argv()

        assert excinfo.value.code == 2

    def test_the_bare_record_hint_names_replay_too(self, capsys, tmp_path):
        record = tmp_path / "some-game.jsonl"
        record.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit):
            _parse_argv([str(record)])

        err = capsys.readouterr().err
        assert "contrai verify" in err
        assert "contrai replay" in err


class TestRecordPaths:
    def test_a_file_stands_for_itself(self, tmp_path):
        record = tmp_path / "a.jsonl"
        record.write_text("", encoding="utf-8")

        assert _record_paths([record]) == [record]

    def test_a_directory_stands_for_the_records_in_it(self, tmp_path):
        first = tmp_path / "a.jsonl"
        second = tmp_path / "b.jsonl"
        for path in (first, second):
            path.write_text("", encoding="utf-8")

        assert _record_paths([tmp_path]) == [first, second]

    def test_a_records_root_reaches_its_games_directory(self, tmp_path):
        games = tmp_path / "games"
        games.mkdir()
        record = games / "a.jsonl"
        record.write_text("", encoding="utf-8")

        assert _record_paths([tmp_path]) == [record]

    def test_the_same_record_named_twice_is_verified_once(self, tmp_path):
        record = tmp_path / "a.jsonl"
        record.write_text("", encoding="utf-8")

        assert _record_paths([record, tmp_path]) == [record]

    def test_a_missing_path_is_kept_so_the_error_names_it(self, tmp_path):
        missing = tmp_path / "nope.jsonl"

        assert _record_paths([missing]) == [missing]


class TestRunVerify:
    """``_run_verify`` — the report, the files it writes, the exit code."""

    @staticmethod
    def _args(paths, **overrides):
        """The ``verify`` namespace, with every flag off unless overridden."""

        fields = {"json": False, "out": None, "no_write": False}
        fields.update(overrides)
        return argparse.Namespace(paths=list(paths), **fields)

    @pytest.fixture
    def record_root(self, tmp_path):
        """A records root holding one clean 4-AI game."""

        from tests.test_replay.conftest import play_and_record

        play_and_record(tmp_path, seed=1)
        return tmp_path

    def test_a_clean_record_exits_zero(self, record_root, capsys):
        code = _run_verify(self._args([record_root]))

        assert code == 0
        assert "verified" in capsys.readouterr().out

    def test_it_writes_the_verdict_beside_games(self, record_root):
        _run_verify(self._args([record_root]))

        assert list((record_root / "verdicts").glob("*.json"))

    def test_no_write_writes_nothing(self, record_root):
        _run_verify(self._args([record_root], no_write=True))

        assert not (record_root / "verdicts").exists()

    def test_out_redirects_the_verdict(self, record_root, tmp_path):
        elsewhere = tmp_path / "elsewhere"

        _run_verify(self._args([record_root], out=elsewhere))

        assert list((elsewhere / "verdicts").glob("*.json"))
        assert not (record_root / "verdicts").exists()

    def test_json_prints_a_list_of_verdicts(self, record_root, capsys):
        _run_verify(self._args([record_root], json=True, no_write=True))

        payload = json.loads(capsys.readouterr().out)
        assert len(payload) == 1
        assert payload[0]["verdict"] == "verified"

    def test_a_suspect_record_exits_one(self, record_root, capsys, monkeypatch):
        from contrai_engine.replay.verdict import (
            GameVerdict,
            Mismatch,
            MismatchKind,
            RoundVerdict,
        )

        def _suspect(path, out=None):
            return GameVerdict(
                game_id="engine-test",
                source="engine",
                preset="classic",
                rounds=(
                    RoundVerdict.decide(
                        1,
                        mismatches=(
                            Mismatch(
                                kind=MismatchKind.SCORE,
                                detail="the marked points differ",
                                position="North",
                                expected="10",
                                observed="20",
                            ),
                        ),
                    ),
                ),
            )

        monkeypatch.setattr(cli_module, "verify_record", _suspect)

        code = _run_verify(self._args([record_root], no_write=True))

        out = capsys.readouterr().out
        assert code == 1
        assert "suspect" in out
        assert "the marked points differ" in out
        assert "engine: 10" in out and "record: 20" in out

    def test_an_unreadable_record_exits_one_and_names_it(
        self, tmp_path, capsys
    ):
        broken = tmp_path / "broken.jsonl"
        broken.write_text("not json at all\n", encoding="utf-8")

        code = _run_verify(self._args([broken]))

        assert code == 1
        assert "broken.jsonl" in capsys.readouterr().err

    def test_no_records_found_exits_one(self, tmp_path, capsys):
        code = _run_verify(self._args([tmp_path]))

        assert code == 1
        assert "no records found" in capsys.readouterr().err


class TestRunReplay:
    """``_run_replay`` against a real record and a scripted view."""

    @staticmethod
    def _args(path, **overrides):
        """The ``replay`` namespace, with every flag off unless overridden."""

        fields = {"round": None}
        fields.update(overrides)
        return argparse.Namespace(path=Path(path), **fields)

    @pytest.fixture
    def record_path(self, tmp_path):
        """One clean 4-AI game, written as a record."""

        from tests.test_replay.conftest import play_and_record

        play_and_record(tmp_path, seed=1, rounds=3)
        (path,) = (tmp_path / "games").glob("*.jsonl")
        return path

    @staticmethod
    def _view(picks, keys):
        """A view scripting the picker's answers and the step keys."""

        class _ReplayView:
            def __init__(self):
                self.options = None
                self.console = _RecordingConsole()
                self.summaries: list[tuple] = []
                self.recaps = 0
                self.steps = 0
                self.grids: list[tuple] = []
                self.redraws = 0
                self._picks = list(picks)
                self._keys = list(keys)

            def attach(self, game, target_score):
                pass

            def on_round_dealt(self, round_):
                pass

            def on_bid_made(self, player, bid, history):
                pass

            def on_card_played(self, player, card, plays):
                pass

            def on_belote_announced(self, player, kind, suit, round_):
                pass

            def on_trick_complete(self, plays, winner, round_):
                pass

            def on_round_complete(self, round_, running_scores):
                pass

            def show_replay_deal(self, round_):
                pass

            def show_round_recap(self, round_, scores, **kwargs):
                self.recaps += 1

            def show_replay_summary(self, rows, game_id):
                self.summaries.append((tuple(rows), game_id))
                pick = self._picks.pop(0) if self._picks else None
                # A bare number scripts "step that round".
                return RoundPick(pick) if isinstance(pick, int) else pick

            def show_replay_grid(self, round_, bids):
                self.grids.append((round_, list(bids)))

            def show_replay_notice(self, text):
                self.console.print(text)

            def redraw_screen(self):
                self.redraws += 1

            def show_replay_contract(self, round_):
                pass

            def show_replay_step(self, *, can_go_back, **_offered):
                self.steps += 1
                return self._keys.pop(0) if self._keys else "r"

        return _ReplayView()

    def test_quitting_the_picker_exits_zero(self, record_path, monkeypatch):
        view = self._view([None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert len(view.summaries) == 1

    def test_the_picker_lists_every_recorded_round(
        self, record_path, monkeypatch
    ):
        view = self._view([None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        _run_replay(self._args(record_path))

        (rows, _), = view.summaries
        assert len(rows) == 3
        assert all(row.verdict is not None for row in rows)

    def test_stepping_a_round_reaches_its_recap(
        self, record_path, monkeypatch
    ):
        # 'r' runs the round out, then the picker is answered with quit.
        view = self._view([1, None], ["r"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert view.recaps == 1

    def test_stepping_a_later_round_replays_the_earlier_ones_in_silence(
        self, record_path, monkeypatch
    ):
        # Round 3 is reached by replaying 1 and 2 quietly: one recap, and
        # no deal frame for the rounds nobody asked to watch.
        view = self._view([3, None], ["r"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert view.recaps == 1

    def test_the_view_is_built_in_replay_mode(
        self, record_path, monkeypatch
    ):
        seen: list[DebugOptions] = []
        view = self._view([None], [])

        def _make_view(options=None, aids=None):
            seen.append(options)
            return view

        monkeypatch.setattr(cli_module, "RichView", _make_view)

        _run_replay(self._args(record_path))

        assert seen[0].replay is True

    def test_round_opens_straight_on_that_round(
        self, record_path, monkeypatch
    ):
        view = self._view([None], ["r"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path, round=1)) == 0
        # The round ran before the picker was ever shown.
        assert view.recaps == 1

    def test_leaving_a_round_returns_to_the_picker(
        self, record_path, monkeypatch
    ):
        # 'q' at the round's first stop unwinds to the picker, which is
        # then answered with quit.
        view = self._view([1, None], ["q"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert view.recaps == 0
        assert len(view.summaries) == 2

    def test_going_back_replays_the_round_from_its_deal(
        self, record_path, monkeypatch
    ):
        # Two actions, then back, then run out: the round is replayed a
        # second time and still reaches exactly one recap.
        view = self._view([1, None], ["n", "n", "p", "r"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert view.recaps == 1

    def test_going_back_from_the_recap_re_enters_the_round(
        self, record_path, monkeypatch
    ):
        # 'r' to the recap, 'p' at the recap prompt, then 'r' again: two
        # recaps, because the round was walked to its end twice.
        view = self._view([1, None], ["r", "p", "r"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert view.recaps == 2

    def test_a_grid_pick_shows_the_grid_and_returns_to_the_picker(
        self, record_path, monkeypatch
    ):
        view = self._view([RoundPick(2, grid=True), None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        (round_, bids), = view.grids
        # Rounds 1 and 2 were replayed, unseen, to reach it.
        assert round_.round_number == 2
        assert len(bids) == len(round_.auction.bids) >= 4
        assert view.recaps == 0
        assert view.steps == 0
        assert len(view.summaries) == 2

    def test_a_grid_carries_every_trick_of_a_played_round(
        self, tmp_path, monkeypatch
    ):
        from tests.test_replay.conftest import play_and_record

        # Seed 7 passes its first round out and plays the second.
        play_and_record(tmp_path, seed=7, rounds=2)
        (path,) = (tmp_path / "games").glob("*.jsonl")
        view = self._view([RoundPick(2, grid=True), None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        _run_replay(self._args(path))

        (round_, _), = view.grids
        assert round_.contract is not None
        assert len(round_.play_state.completed_tricks) == 8

    def test_a_grid_round_that_diverges_says_so(
        self, record_path, monkeypatch
    ):
        from contrai_engine.replay.exceptions import ReplayError

        class _Diverging(cli_module.ReplayController):
            def replay_round(self, round_):
                if round_.number == 2:
                    raise ReplayError("the record ran out")
                super().replay_round(round_)

        monkeypatch.setattr(cli_module, "ReplayController", _Diverging)
        view = self._view([RoundPick(2, grid=True), None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert view.grids == []
        assert any("diverges" in line for line in view.console.printed)
        assert view.steps == 1

    def test_g_at_the_recap_shows_the_round_then_the_recap_again(
        self, record_path, monkeypatch
    ):
        # 'r' to the recap, 'g' there, then 'q' back to the picker.
        view = self._view([1, None], ["r", "g", "q"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        (round_, bids), = view.grids
        assert round_.round_number == 1
        assert len(bids) == len(round_.auction.bids)
        assert view.redraws == 1
        assert view.recaps == 1

    def test_g_at_a_divergence_shows_the_round_as_far_as_it_got(
        self, record_path, monkeypatch
    ):
        from contrai_engine.replay.exceptions import ReplayError

        class _Diverging(cli_module.ReplayController):
            def replay_round(self, round_):
                if round_.number == 2:
                    raise ReplayError("the record ran out")
                super().replay_round(round_)

        monkeypatch.setattr(cli_module, "ReplayController", _Diverging)
        view = self._view([2, None], ["g", "q"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        _run_replay(self._args(record_path))

        assert len(view.grids) == 1
        assert view.steps == 2

    def test_a_stepped_round_that_diverges_says_so(
        self, record_path, monkeypatch
    ):
        from contrai_engine.replay.exceptions import ReplayError

        class _Diverging(cli_module.ReplayController):
            def replay_round(self, round_):
                if round_.number == 2:
                    raise ReplayError("the record ran out")
                super().replay_round(round_)

        monkeypatch.setattr(cli_module, "ReplayController", _Diverging)
        view = self._view([2, None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path)) == 0
        assert any("diverges" in line for line in view.console.printed)
        assert view.recaps == 0
        assert view.steps == 1

    def _explains(self, monkeypatch) -> list[bool]:
        """Record the ``explain`` flag of every controller the CLI builds."""

        flags: list[bool] = []

        class _Watched(cli_module.ReplayController):
            def __init__(self, record, view=None, *, explain=False):
                flags.append(explain)
                super().__init__(record, view, explain=explain)

        monkeypatch.setattr(cli_module, "ReplayController", _Watched)
        return flags

    def test_a_record_of_engine_ai_seats_shows_their_reasons(
        self, record_path, monkeypatch
    ):
        flags = self._explains(monkeypatch)
        view = self._view([1, None], ["r"])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        _run_replay(self._args(record_path))

        assert view.rationale == "compact"
        assert flags and all(flags)

    def test_a_record_with_no_engine_ai_shows_no_reasons_at_all(
        self, record_path, monkeypatch
    ):
        import dataclasses

        from contrai_data import Seat, SeatKind

        real_load = cli_module.load_game

        def _observed(path):
            record = real_load(path)
            seats = {
                seat: Seat(None, "someone", None, SeatKind.OBSERVED, None)
                for seat in record.seats
            }
            return dataclasses.replace(record, seats=seats)

        monkeypatch.setattr(cli_module, "load_game", _observed)
        flags = self._explains(monkeypatch)
        view = self._view([1, None], ["r"])
        view.rationale = None
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        _run_replay(self._args(record_path))

        assert view.rationale is None
        assert flags and not any(flags)

    def test_a_suspect_earlier_round_is_stepped_past(
        self, record_path, monkeypatch
    ):
        from contrai_engine.replay.exceptions import ReplayError

        cleared: list[int] = []

        class _EarlierDiverges(cli_module.ReplayController):
            def replay_round(self, round_):
                if round_.number == 1:
                    raise ReplayError("round 1 is suspect")
                super().replay_round(round_)

            def clear_hands(self):
                cleared.append(1)
                super().clear_hands()

        monkeypatch.setattr(cli_module, "ReplayController", _EarlierDiverges)
        view = self._view([RoundPick(2, grid=True), None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        _run_replay(self._args(record_path))

        assert cleared == [1]
        assert len(view.grids) == 1

    def test_an_unknown_round_exits_one(
        self, record_path, monkeypatch, capsys
    ):
        view = self._view([None], [])
        monkeypatch.setattr(cli_module, "RichView", lambda *a, **k: view)

        assert _run_replay(self._args(record_path, round=99)) == 1
        assert "99" in capsys.readouterr().err

    def test_an_unreadable_record_exits_one(self, tmp_path, capsys):
        missing = tmp_path / "nope.jsonl"

        assert _run_replay(self._args(missing)) == 1
        assert "cannot be read" in capsys.readouterr().err

    def test_an_interrupt_exits_zero(self, record_path, monkeypatch):
        class _Interrupting:
            options = None
            console = _RecordingConsole()

            def show_replay_summary(self, rows, game_id):
                raise KeyboardInterrupt

        monkeypatch.setattr(
            cli_module, "RichView", lambda *a, **k: _Interrupting()
        )

        assert _run_replay(self._args(record_path)) == 0


class TestMainDispatch:
    def test_the_streams_are_utf8_before_verify_writes_anything(
        self, monkeypatch
    ):
        # A card renders as ``K♠``, and ``verify`` prints one when it
        # reports a belote mismatch — so the fix-up has to run *before*
        # the subcommand dispatch, not inside the game path. On a cp1252
        # console the alternative is not mojibake, it is
        # UnicodeEncodeError.
        out, err = _ReconfigurableStream(), _ReconfigurableStream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        monkeypatch.setattr(sys, "argv", ["contrai", "verify", "a.jsonl"])
        seen: list[list[str | None]] = []

        def _record_then_exit(args):
            seen.append(list(out.encodings))
            return 0

        monkeypatch.setattr(cli_module, "_run_verify", _record_then_exit)

        with pytest.raises(SystemExit):
            main()

        assert seen == [["utf-8"]]
        assert err.encodings == ["utf-8"]

    def test_a_stream_that_refuses_does_not_stop_the_run(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", _RaisingStream())
        monkeypatch.setattr(sys, "stderr", _PlainStream())
        monkeypatch.setattr(sys, "argv", ["contrai", "verify", "a.jsonl"])
        monkeypatch.setattr(cli_module, "_run_verify", lambda args: 0)

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 0

    def test_verify_exits_with_its_own_code(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "verify", "a.jsonl"])
        monkeypatch.setattr(cli_module, "_run_verify", lambda args: 3)

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 3

    def test_verify_never_reaches_the_game_loop(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "verify", "a.jsonl"])
        monkeypatch.setattr(cli_module, "_run_verify", lambda args: 0)
        monkeypatch.setattr(
            cli_module,
            "RichView",
            lambda *a, **k: pytest.fail("verify must not build a view"),
        )

        with pytest.raises(SystemExit):
            main()

    def test_replay_exits_with_its_own_code(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "replay", "a.jsonl"])
        monkeypatch.setattr(cli_module, "_run_replay", lambda args: 4)

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 4

    def test_replay_never_reaches_the_game_loop(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["contrai", "replay", "a.jsonl"])
        monkeypatch.setattr(cli_module, "_run_replay", lambda args: 0)
        monkeypatch.setattr(
            cli_module,
            "_build_game",
            lambda *a, **k: pytest.fail("replay must not build a game"),
        )

        with pytest.raises(SystemExit):
            main()
