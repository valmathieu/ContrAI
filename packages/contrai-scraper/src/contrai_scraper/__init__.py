"""Playwright spectator-mode scraper for ``app.belote-rebelote.fr``.

The package is split along the two phases of a scraping run:

* :mod:`contrai_scraper.session` drives the browser from the lobby to a seated
  spectator view of a tournament table (login, mode selection, table hunting).
* :mod:`contrai_scraper.observer` watches a table already under observation,
  identifying the four players and polling for round changes.

:mod:`contrai_scraper.cli` wires the two together into the ``contrai-scrape``
console script.
"""

from contrai_scraper.config import ACCOUNT_EMAIL, TARGET_URL, VERIFICATION_CODE
from contrai_scraper.observer import (
    get_current_round,
    get_players,
    is_game_scrapeable,
    observe_game,
    wait_for_new_round,
)
from contrai_scraper.session import find_tournament_table, log_in, open_spectator_mode

__all__ = [
    "ACCOUNT_EMAIL",
    "TARGET_URL",
    "VERIFICATION_CODE",
    "find_tournament_table",
    "get_current_round",
    "get_players",
    "is_game_scrapeable",
    "log_in",
    "observe_game",
    "open_spectator_mode",
    "wait_for_new_round",
]
