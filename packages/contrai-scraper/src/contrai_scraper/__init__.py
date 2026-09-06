"""Playwright spectator-mode scraper for online Contrée tournament tables.

The package is split along the two phases of a scraping run:

* :mod:`contrai_scraper.session` drives the browser from the lobby to a seated
  spectator view of a tournament table (login, mode selection, table hunting).
* :mod:`contrai_scraper.observer` watches a table already under observation,
  identifying the four players and polling for round changes.

:mod:`contrai_scraper.cli` wires the two together into the ``contrai-scrape``
console script.

Everything the site is called — its URL, its selectors, its wire vocabulary —
lives in a local, git-ignored ``profile.toml``, read by
:func:`contrai_scraper.profile.load_profile`. The code knows the *structure*;
the document knows the *strings*.
"""

from contrai_scraper.config import ACCOUNT_EMAIL, TARGET_URL, VERIFICATION_CODE
from contrai_scraper.exceptions import (
    ParseError,
    ProfileError,
    ScraperError,
    WireError,
)
from contrai_scraper.observer import (
    get_current_round,
    get_players,
    is_game_scrapeable,
    observe_game,
    wait_for_new_round,
)
from contrai_scraper.profile import (
    AccountSection,
    BrowserSection,
    OutputSection,
    PrivacySection,
    Profile,
    RulesSection,
    Selector,
    SelectorSection,
    SiteSection,
    WireEvents,
    WireSection,
    WireTokens,
    load_profile,
)
from contrai_scraper.session import find_tournament_table, log_in, open_spectator_mode

__all__ = [
    "ACCOUNT_EMAIL",
    "AccountSection",
    "BrowserSection",
    "OutputSection",
    "ParseError",
    "PrivacySection",
    "Profile",
    "ProfileError",
    "RulesSection",
    "ScraperError",
    "Selector",
    "SelectorSection",
    "SiteSection",
    "TARGET_URL",
    "VERIFICATION_CODE",
    "WireError",
    "WireEvents",
    "WireSection",
    "WireTokens",
    "find_tournament_table",
    "get_current_round",
    "get_players",
    "is_game_scrapeable",
    "load_profile",
    "log_in",
    "observe_game",
    "open_spectator_mode",
    "wait_for_new_round",
]
