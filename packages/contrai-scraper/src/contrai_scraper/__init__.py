"""Playwright spectator-mode scraper for online Contrée tournament tables.

The package is a pipeline with a browser at one end and a record at the other:

* :mod:`contrai_scraper.profile` reads the local, git-ignored ``profile.toml``
  that every string in the run comes from.
* :mod:`contrai_scraper.browser` is the only module that touches a page:
  login, the menu walk, the table hop and the two panels.
* :mod:`contrai_scraper.frames` and :mod:`contrai_scraper.wire` turn socket
  frames into a de-duplicated stream of game events, live or replayed.
* :mod:`contrai_scraper.parse` assembles those events into a ``contrai-data``
  record.
* :mod:`contrai_scraper.recorder` is the loop that drives all of it, one table
  at a time, with :mod:`contrai_scraper.rawlog` keeping the evidence and
  :mod:`contrai_scraper.health` saying what it is doing.

:mod:`contrai_scraper.cli` wires them into the ``contrai-scrape`` console
script.

Everything the site is called — its URL, its selectors, its wire vocabulary —
lives in the profile. The code knows the *structure*; the document knows the
*strings*.
"""

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
from contrai_scraper.parse.forced_passes import restore_forced_passes
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
    "DEAL_PACKETS",
    "DEAL_VERB",
    "INIT_SCRIPT",
    "RECEIVED",
    "SCOREBOARD_PANEL",
    "SEND_SCRIPT",
    "SENT",
    "STEP_TIMEOUT_MS",
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
    "RawFrame",
    "RawLine",
    "RawLogFrameSource",
    "RawLogWriter",
    "Recorder",
    "RecorderLimits",
    "RecorderSection",
    "RowContract",
    "RulesSection",
    "ScoreRow",
    "ScoreboardReading",
    "ScraperError",
    "Selector",
    "SelectorSection",
    "SessionResult",
    "SessionSummary",
    "SiteSection",
    "Snapshot",
    "Spectator",
    "Translator",
    "WireError",
    "WireEvent",
    "WireEvents",
    "WireSection",
    "WireStream",
    "WireTokens",
    "bid_events",
    "collect_rounds",
    "compress_to_base64",
    "deal_hands",
    "decompress_from_base64",
    "dig",
    "duplicate_key",
    "final_trick",
    "load_profile",
    "new_session_id",
    "open_spectator",
    "order_events",
    "parse_key",
    "parse_session",
    "play_events",
    "raw_dir",
    "raw_path",
    "read_raw_log",
    "read_snapshot",
    "resolve_dealer",
    "restore_forced_passes",
    "unwrap",
]
