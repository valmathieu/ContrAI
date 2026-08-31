"""``contrai`` CLI entry point.

Drives the landing → game loop → end-game flow, wiring a
:class:`RichView` into ``Game.manage_round``. Pure orchestration —
all rendering lives in :mod:`contrai_engine.view.rich_view`.

Also owns the three debug-mode flags (``--debug``, ``--seed``,
``--autoplay``): parsing them into a :class:`DebugOptions`
(:func:`_parse_args`), applying the seed to the global ``random``
module (:func:`_apply_seed`), and threading the result into both the
view (:class:`RichView`) and the game's seating (:func:`_build_game`).

The two mutually exclusive ruleset flags (``--rules FILE`` /
``--preset NAME``) and the ``--no-live-score`` aid switch are resolved in
the same pass, through :func:`contrai_engine.ruleset.resolve_setup`, into
the :class:`~contrai_engine.ruleset.TableSetup` the run starts from: the
:class:`~contrai_core.RuleConfig` the :class:`Game` is built under, plus
the :class:`~contrai_engine.options.TableAids` the view reads. A
malformed, unreadable or impossible ruleset is reported as an
``argparse`` usage error rather than a traceback.

That resolved setup is what the landing screen opens on and edits: a
player can pick a preset, load a file, turn any of the 22 knobs or
switch the live round score without leaving the screen, and what
:meth:`RichView.show_landing` hands back is what the next :class:`Game`
is built from. The model then owns every rule it names —
``Game.check_game_over()`` reads the target off ``game.rules`` and the
loop carries nothing alongside the game — while the interface aids stay
on the view, re-pointed each time the screen returns.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import random
import sys
from pathlib import Path

from contrai_core.position import Position
from contrai_core.rule_config import PRESETS, RuleConfig
from contrai_engine.log_setup import configure_logging
from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.options import DebugOptions, TableAids
from contrai_engine.ruleset import TableSetup, resolve_setup, save_setup, setup_path
from contrai_engine.view.rich_view import RichView


# TODO: replace with a seat picker on the landing screen. For now the
# layout matches the design handoff exactly: South is the human, the
# other three seats are AI (expert — the default strategies) — unless
# ``--autoplay`` is set, in which case South is an AI too (see
# ``_build_game``).
HUMAN_SEAT = Position.SOUTH

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``contrai`` argument parser.

    Split out from :func:`_parse_args` so the parser is available for
    ``parser.error`` reporting after the arguments themselves have been
    read — an invalid ruleset must exit with the same usage message and
    exit code as any other bad flag.

    Returns:
        The configured parser.
    """

    parser = argparse.ArgumentParser(prog="contrai")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="face-up hands and DEBUG-level diagnostics written to "
        "contrai-debug.log",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the game's RNG for a reproducible deal and dealer",
    )
    parser.add_argument(
        "--autoplay",
        action="store_true",
        help="run one full unattended game with an AI at every seat",
    )
    parser.add_argument(
        "--no-live-score",
        action="store_true",
        help="hide the running round points from the in-game Round panel",
    )
    # A file and a named preset are two ways of saying the same thing, so
    # argparse refuses the pair itself — ``resolve_rules`` guards the same
    # case for non-CLI callers.
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument(
        "--rules",
        type=Path,
        default=None,
        metavar="FILE",
        help="play under the table ruleset in this TOML file",
    )
    rules.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default=None,
        help="play under a named built-in ruleset",
    )
    return parser


def _parse_args(
    argv: list[str] | None = None,
) -> tuple[DebugOptions, TableSetup]:
    """Parse the CLI's flags into a :class:`DebugOptions` and a ``TableSetup``.

    Args:
        argv: Argument strings to parse, excluding the program name.
            ``None`` (the default) parses ``sys.argv[1:]``, matching
            ``argparse``'s own default.

    Returns:
        The parsed debug flags and the resolved table setup — the ruleset
        the game is built under plus the interface aids the view reads. No
        seed generation happens here — that is :func:`_apply_seed`'s job —
        so ``seed`` is ``None`` unless ``--seed`` was passed explicitly,
        and the setup is ``TableSetup()`` unless a flag named another.

    Raises:
        SystemExit: If ``argv`` fails to parse (e.g. a non-integer
            ``--seed`` value, or ``--rules`` and ``--preset`` together),
            or if the selected ruleset is unreadable, malformed or names
            an impossible table — all reported as ``argparse`` usage
            errors (exit code 2).
    """

    parser = _build_parser()
    args = parser.parse_args(argv)
    options = DebugOptions(debug=args.debug, autoplay=args.autoplay, seed=args.seed)
    # ``None`` rather than ``TableAids()`` when the flag is absent: only an
    # explicitly typed flag may override a setup file's own [table_aids].
    aids = TableAids(live_round_score=False) if args.no_live_score else None
    try:
        setup = resolve_setup(preset=args.preset, rules_path=args.rules, aids=aids)
    except (ValueError, OSError) as exc:
        # RulesetError, core's InvalidRuleConfigError, or an unreadable
        # file. ``parser.error`` prints usage + the message to stderr and
        # exits 2 — the same shape as any other bad flag.
        parser.error(str(exc))
    return options, setup


def _apply_seed(options: DebugOptions) -> DebugOptions:
    """Seed the global ``random`` module and record the seed used.

    Order is generate-then-seed: an explicit ``--seed`` always wins and
    is applied as-is. Absent that, ``--debug`` generates a fresh seed —
    so a debug run is reproducible after the fact even when the user
    didn't think to pass one — applies it, and records it back onto the
    returned options. With neither flag set, this is a complete no-op:
    the global RNG state is left untouched, preserving today's
    behavior exactly.

    Args:
        options: The parsed flags, as returned by :func:`_parse_args`.

    Returns:
        ``options`` unchanged when a seed was already explicit or
        neither flag was set; otherwise a copy with ``seed`` filled in
        from the freshly generated value.
    """

    if options.seed is not None:
        random.seed(options.seed)
        return options
    if options.debug:
        seed = random.randrange(2**32)
        random.seed(seed)
        return dataclasses.replace(options, seed=seed)
    return options


def _build_game(autoplay: bool = False, rules: RuleConfig | None = None) -> Game:
    """Instantiate a fresh Game with the seating this run calls for.

    Args:
        autoplay: When ``False`` (the default), :data:`HUMAN_SEAT`
            (South) is a :class:`HumanPlayer` and the other three seats
            are :class:`AiPlayer`. When ``True``, every seat — South
            included — is an :class:`AiPlayer`, built the same way as
            the other AI seats: one unattended 4-AI game.
        rules: The table ruleset to play under. ``None`` (the default)
            leaves the game on the §9 catalogue defaults.

    Returns:
        A freshly constructed :class:`Game`, not yet dealt.
    """
    players = []
    for seat in Position:
        if seat is HUMAN_SEAT and not autoplay:
            players.append(HumanPlayer("You", position=seat))
        else:
            players.append(AiPlayer(seat.value, position=seat))
    return Game(players, rules=rules)


def _remember(setup: TableSetup, options: DebugOptions) -> None:
    """Persist the setup a player left the landing screen with.

    Never under ``--autoplay``: an unattended run must not rewrite what a
    player chose. An unwritable home is not fatal either — the cache is a
    convenience, and a game that refused to start because it could not
    write one would be the worse trade.

    Args:
        setup: The setup to remember.
        options: The run's debug flags, read for ``autoplay``.
    """
    if options.autoplay:
        return
    try:
        save_setup(setup_path(), setup)
    except OSError:
        logger.debug("could not remember the table setup", exc_info=True)


def main() -> None:
    """Entry point registered as the ``contrai`` console script."""
    options, setup = _parse_args()
    options = _apply_seed(options)
    configure_logging(options)

    # Force UTF-8 stdout/stderr so suit glyphs (♠♥♦♣) render under
    # cmd.exe and other code-page-1252 contexts. Modern Windows
    # Terminal handles UTF-8 natively but the legacy console path
    # crashes on encode without this.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass

    view = RichView(options=options, aids=setup.aids)
    setup = view.show_landing(setup)
    view.aids = setup.aids
    _remember(setup, options)
    try:
        while True:
            game = _build_game(autoplay=options.autoplay, rules=setup.rules)
            view.attach(game, target_score=game.rules.target_score)
            while not game.check_game_over().game_over:
                game.manage_round(view=view)
                view.on_round_complete(game.current_round, game.scores)
                # Show a between-round recap (contract, made/failed,
                # round points, running totals). Always shown, including
                # before the end-game banner so the player can read the
                # final round's breakdown before the scoreboard takes
                # over — the prompt adapts to the final-round and
                # sudden-death (tie at/above target) cases, and the panel
                # names any side the §8 belote gate is holding back.
                status = game.check_game_over()
                view.show_round_recap(
                    game.current_round,
                    game.scores,
                    is_final=status.game_over,
                    is_tiebreaker=status.tied_teams is not None,
                    belote_gated=status.belote_gated,
                )
            choice = view.show_end_game(game.check_game_over())
            if choice == "q":
                break
            if choice == "n":
                setup = view.show_landing(setup)
                view.aids = setup.aids
                _remember(setup, options)
            # 'r' → rematch: same setup, fresh game in the next loop tick.
    except (KeyboardInterrupt, EOFError):
        view.console.print("\nGoodbye.")


if __name__ == "__main__":
    main()
