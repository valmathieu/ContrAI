"""``contrai`` CLI entry point.

Drives the landing → game loop → end-game flow, wiring a
:class:`RichView` into ``Game.manage_round``. Pure orchestration —
all rendering lives in :mod:`contrai_engine.view.rich_view`.

Also owns the three debug-mode flags (``--debug``, ``--seed``,
``--autoplay``): parsing them into a :class:`DebugOptions`
(:func:`_parse_args`), applying the seed to the global ``random``
module (:func:`_apply_seed`), and threading the result into both the
view (:class:`RichView`) and the game's seating (:func:`_build_game`).
"""

from __future__ import annotations

import argparse
import dataclasses
import random
import sys

from contrai_core.position import Position
from contrai_engine.log_setup import configure_logging
from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.options import DebugOptions
from contrai_engine.view.rich_view import RichView


# TODO: replace with a seat picker on the landing screen. For now the
# layout matches the design handoff exactly: South is the human, the
# other three seats are AI (expert — the default strategies) — unless
# ``--autoplay`` is set, in which case South is an AI too (see
# ``_build_game``).
HUMAN_SEAT = Position.SOUTH


def _parse_args(argv: list[str] | None = None) -> DebugOptions:
    """Parse the CLI's three debug-mode flags into a :class:`DebugOptions`.

    Args:
        argv: Argument strings to parse, excluding the program name.
            ``None`` (the default) parses ``sys.argv[1:]``, matching
            ``argparse``'s own default.

    Returns:
        The parsed flags. No seed generation happens here — that is
        :func:`_apply_seed`'s job — so ``seed`` is ``None`` unless
        ``--seed`` was passed explicitly.

    Raises:
        SystemExit: If ``argv`` fails to parse (e.g. a non-integer
            ``--seed`` value) — ``argparse``'s standard behavior.
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
    args = parser.parse_args(argv)
    return DebugOptions(debug=args.debug, autoplay=args.autoplay, seed=args.seed)


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


def _build_game(autoplay: bool = False) -> Game:
    """Instantiate a fresh Game with the seating this run calls for.

    Args:
        autoplay: When ``False`` (the default), :data:`HUMAN_SEAT`
            (South) is a :class:`HumanPlayer` and the other three seats
            are :class:`AiPlayer`. When ``True``, every seat — South
            included — is an :class:`AiPlayer`, built the same way as
            the other AI seats: one unattended 4-AI game.

    Returns:
        A freshly constructed :class:`Game`, not yet dealt.
    """
    players = []
    for seat in Position:
        if seat is HUMAN_SEAT and not autoplay:
            players.append(HumanPlayer("You", position=seat))
        else:
            players.append(AiPlayer(seat.value, position=seat))
    return Game(players)


def main() -> None:
    """Entry point registered as the ``contrai`` console script."""
    options = _apply_seed(_parse_args())
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

    view = RichView(options=options)
    target = view.show_landing()
    try:
        while True:
            game = _build_game(autoplay=options.autoplay)
            view.attach(game, target_score=target)
            while not game.check_game_over(target).game_over:
                game.manage_round(view=view)
                view.on_round_complete(game.current_round, game.scores)
                # Show a between-round recap (contract, made/failed,
                # round points, running totals). Always shown, including
                # before the end-game banner so the player can read the
                # final round's breakdown before the scoreboard takes
                # over — the prompt adapts to the final-round and
                # sudden-death (tie at/above target) cases.
                status = game.check_game_over(target)
                view.show_round_recap(
                    game.current_round,
                    game.scores,
                    is_final=status.game_over,
                    is_tiebreaker=status.tied_teams is not None,
                )
            choice = view.show_end_game(game.check_game_over(target))
            if choice == "q":
                break
            if choice == "n":
                target = view.show_landing(selected_target=target)
            # 'r' → rematch: same target, fresh game in the next loop tick.
    except (KeyboardInterrupt, EOFError):
        view.console.print("\nGoodbye.")


if __name__ == "__main__":
    main()
