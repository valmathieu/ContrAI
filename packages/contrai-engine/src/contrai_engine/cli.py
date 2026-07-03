"""``contrai`` CLI entry point.

Drives the landing → game loop → end-game flow, wiring a
:class:`RichView` into ``Game.manage_round``. Pure orchestration —
all rendering lives in :mod:`contrai_engine.view.rich_view`.
"""

from __future__ import annotations

import sys

from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.view.rich_view import RichView


# TODO: replace with a seat picker on the landing screen. For now the
# layout matches the design handoff exactly: South is the human, the
# other three seats are AI (expert — the default strategies).
HUMAN_SEAT = "South"
SEATS = ("North", "East", "South", "West")


def _build_game() -> Game:
    """Instantiate a fresh Game with one HumanPlayer (South) + 3 AiPlayers."""
    players = []
    for seat in SEATS:
        if seat == HUMAN_SEAT:
            players.append(HumanPlayer("You", position=seat))
        else:
            players.append(AiPlayer(seat, position=seat))
    return Game(players)


def main() -> None:
    """Entry point registered as the ``contrai`` console script."""
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

    view = RichView()
    target = view.show_landing()
    try:
        while True:
            game = _build_game()
            view.attach(game, target_score=target)
            while not game.check_game_over(target).game_over:
                game.manage_round(view=view)
                view.on_round_complete(game.current_round, game.scores)
                # Show a between-round recap (contract, made/failed,
                # round points, running totals). Always shown, including
                # before the end-game banner so the player can read the
                # final round's breakdown before the scoreboard takes
                # over — the prompt adapts to the final-round case.
                is_final = game.check_game_over(target).game_over
                view.show_round_recap(
                    game.current_round, game.scores, is_final=is_final
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
