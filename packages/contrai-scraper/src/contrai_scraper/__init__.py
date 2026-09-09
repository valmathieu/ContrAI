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
from contrai_scraper.browser import (
    INIT_SCRIPT,
    SEND_SCRIPT,
    STEP_TIMEOUT_MS,
    OptionsReading,
    ScoreboardReading,
    Spectator,
    open_spectator,
)
from contrai_scraper.exceptions import (
    BrowserError,
    ParseError,
    ProfileError,
    ScraperError,
    WireError,
)
from contrai_scraper.frames import (
    RECEIVED,
    SENT,
    FrameSource,
    PlaywrightFrameSource,
    RawFrame,
    RawLogFrameSource,
)
from contrai_scraper.health import Counters, HealthLog
from contrai_scraper.lzstring import compress_to_base64, decompress_from_base64
from contrai_scraper.observer import (
    get_current_round,
    get_players,
    is_game_scrapeable,
    observe_game,
    wait_for_new_round,
)
from contrai_scraper.recorder import (
    SCOREBOARD_PANEL,
    Recorder,
    RecorderLimits,
    SessionSummary,
)
from contrai_scraper.rawlog import (
    RawLine,
    RawLogWriter,
    new_session_id,
    raw_dir,
    raw_path,
    read_raw_log,
)
from contrai_scraper.parse.deal import (
    DEAL_PACKETS,
    deal_hands,
    final_trick,
    resolve_dealer,
)
from contrai_scraper.parse.live import (
    LiveRound,
    bid_events,
    collect_rounds,
    play_events,
)
from contrai_scraper.parse.session import SessionResult, parse_session
from contrai_scraper.parse.snapshot import (
    PlayerInfo,
    RowContract,
    ScoreRow,
    Snapshot,
    read_snapshot,
)
from contrai_scraper.parse.translate import Translator
from contrai_scraper.profile import (
    AccountSection,
    BrowserSection,
    OutputSection,
    PrivacySection,
    Profile,
    RecorderSection,
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
from contrai_scraper.wire import (
    DEAL_VERB,
    EventKey,
    WireEvent,
    WireStream,
    dig,
    duplicate_key,
    order_events,
    parse_key,
    unwrap,
)

__all__ = [
    "ACCOUNT_EMAIL",
    "INIT_SCRIPT",
    "SEND_SCRIPT",
    "STEP_TIMEOUT_MS",
    "DEAL_PACKETS",
    "DEAL_VERB",
    "AccountSection",
    "BrowserError",
    "BrowserSection",
    "Counters",
    "EventKey",
    "FrameSource",
    "HealthLog",
    "LiveRound",
    "OptionsReading",
    "OutputSection",
    "ParseError",
    "PlayerInfo",
    "PlaywrightFrameSource",
    "PrivacySection",
    "Profile",
    "ProfileError",
    "RecorderSection",
    "RECEIVED",
    "SCOREBOARD_PANEL",
    "RawFrame",
    "RawLine",
    "RawLogFrameSource",
    "RawLogWriter",
    "Recorder",
    "RecorderLimits",
    "RowContract",
    "RulesSection",
    "SENT",
    "ScoreRow",
    "ScoreboardReading",
    "SessionResult",
    "SessionSummary",
    "ScraperError",
    "Selector",
    "SelectorSection",
    "SiteSection",
    "Snapshot",
    "Spectator",
    "Translator",
    "TARGET_URL",
    "VERIFICATION_CODE",
    "WireError",
    "WireEvent",
    "WireEvents",
    "WireSection",
    "WireStream",
    "WireTokens",
    "compress_to_base64",
    "bid_events",
    "collect_rounds",
    "deal_hands",
    "decompress_from_base64",
    "dig",
    "duplicate_key",
    "final_trick",
    "find_tournament_table",
    "get_current_round",
    "get_players",
    "is_game_scrapeable",
    "load_profile",
    "new_session_id",
    "open_spectator",
    "log_in",
    "observe_game",
    "open_spectator_mode",
    "order_events",
    "parse_key",
    "parse_session",
    "play_events",
    "raw_dir",
    "resolve_dealer",
    "raw_path",
    "read_raw_log",
    "read_snapshot",
    "unwrap",
    "wait_for_new_round",
]
