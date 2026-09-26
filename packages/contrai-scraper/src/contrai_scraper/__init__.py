"""Playwright spectator-mode scraper for online Contrée tournament tables.

The package is a pipeline with a browser at one end and a record at the other:

* :mod:`contrai_scraper.profile` reads the local, git-ignored ``profile.toml``
  that every string in the run comes from, and
  :mod:`contrai_scraper.accounts` the ``accounts.toml`` beside it that a
  fleet logs in with.
* :mod:`contrai_scraper.browser` is the only module that touches a page:
  login, the menu walk, the table hop and the two panels.
* :mod:`contrai_scraper.frames` and :mod:`contrai_scraper.wire` turn socket
  frames into a de-duplicated stream of game events, live or replayed, and
  :mod:`contrai_scraper.lobby` reads the lobby's own events for games
  starting.
* :mod:`contrai_scraper.parse` assembles those events into a ``contrai-data``
  record.
* :mod:`contrai_scraper.recorder` is the loop that drives all of it, one table
  at a time, with :mod:`contrai_scraper.rawlog` keeping the evidence and
  :mod:`contrai_scraper.health` saying what it is doing.
* :mod:`contrai_scraper.shift` runs recorders only inside the
  :mod:`contrai_scraper.schedule` window and only once
  :mod:`contrai_scraper.egress` says traffic leaves through the tunnel.
* :mod:`contrai_scraper.fleet` does the same for several workers on one
  browser, which wait in the lobby and chase each game as it starts, with
  :mod:`contrai_scraper.registry` keeping two of them off one table.

:mod:`contrai_scraper.cli` wires them into the ``contrai-scrape`` console
script.

Everything the site is called — its URL, its selectors, its wire vocabulary —
lives in the profile. The code knows the *structure*; the document knows the
*strings*.
"""

from contrai_scraper.accounts import LabelledAccount, load_accounts
from contrai_scraper.browser import (
    INIT_SCRIPT,
    PANEL_ATTEMPT_TIMEOUT_MS,
    PANEL_ATTEMPTS,
    SEND_SCRIPT,
    STEP_TIMEOUT_MS,
    OptionsReading,
    ScoreboardReading,
    Spectator,
    open_browser,
    open_session,
    open_spectator,
)
from contrai_scraper.egress import (
    EgressGate,
    EgressReading,
    EgressRefusal,
    SharedEgressGate,
    route_device_from,
)
from contrai_scraper.exceptions import (
    BrowserError,
    ParseError,
    ProfileError,
    ScraperError,
    ShiftError,
    WireError,
)
from contrai_scraper.fleet import HALL_POLL_S, SOLE_WORKER, Fleet, FleetSummary, Worker
from contrai_scraper.frames import (
    RECEIVED,
    SENT,
    FrameSource,
    PlaywrightFrameSource,
    RawFrame,
    RawLogFrameSource,
)
from contrai_scraper.health import Counters, HealthLog
from contrai_scraper.lobby import LobbyRoster, LobbyWatcher, lobby_seats
from contrai_scraper.lzstring import compress_to_base64, decompress_from_base64
from contrai_scraper.registry import (
    Claim,
    Sighting,
    TableRegistry,
    WorkerClaims,
    estimate_population,
)
from contrai_scraper.recorder import (
    SCOREBOARD_PANEL,
    ChaseTarget,
    Recorder,
    RecorderLimits,
    SessionSummary,
    StopReason,
)
from contrai_scraper.rawlog import (
    RawLine,
    RawLogWriter,
    new_session_id,
    prune_raw_logs,
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
    DRAW_ROUND,
    LiveRound,
    bid_events,
    collect_rounds,
    is_draw,
    play_events,
)
from contrai_scraper.parse.session import (
    SessionResult,
    parse_session,
    split_visits,
)
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
    EgressSection,
    FleetSection,
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
from contrai_scraper.schedule import (
    ActiveRange,
    Schedule,
    parse_range,
    timezone_named,
)
from contrai_scraper.shift import (
    EGRESS_BUDGET,
    FAILURE_BUDGET,
    Shift,
    ShiftSummary,
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
    "DRAW_ROUND",
    "EGRESS_BUDGET",
    "FAILURE_BUDGET",
    "HALL_POLL_S",
    "INIT_SCRIPT",
    "PANEL_ATTEMPTS",
    "PANEL_ATTEMPT_TIMEOUT_MS",
    "RECEIVED",
    "SCOREBOARD_PANEL",
    "SEND_SCRIPT",
    "SENT",
    "SOLE_WORKER",
    "STEP_TIMEOUT_MS",
    "AccountSection",
    "ActiveRange",
    "BrowserError",
    "BrowserSection",
    "ChaseTarget",
    "Claim",
    "Counters",
    "EgressGate",
    "EgressReading",
    "EgressRefusal",
    "EgressSection",
    "EventKey",
    "Fleet",
    "FleetSection",
    "FleetSummary",
    "FrameSource",
    "HealthLog",
    "LabelledAccount",
    "LiveRound",
    "LobbyRoster",
    "LobbyWatcher",
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
    "Schedule",
    "ScoreRow",
    "ScoreboardReading",
    "ScraperError",
    "Selector",
    "SelectorSection",
    "SessionResult",
    "SessionSummary",
    "SharedEgressGate",
    "Shift",
    "ShiftError",
    "ShiftSummary",
    "Sighting",
    "SiteSection",
    "Snapshot",
    "Spectator",
    "StopReason",
    "TableRegistry",
    "Translator",
    "WireError",
    "WireEvent",
    "WireEvents",
    "WireSection",
    "WireStream",
    "WireTokens",
    "Worker",
    "WorkerClaims",
    "bid_events",
    "collect_rounds",
    "compress_to_base64",
    "deal_hands",
    "decompress_from_base64",
    "dig",
    "duplicate_key",
    "estimate_population",
    "final_trick",
    "is_draw",
    "load_accounts",
    "load_profile",
    "lobby_seats",
    "new_session_id",
    "open_browser",
    "open_session",
    "open_spectator",
    "order_events",
    "parse_key",
    "parse_range",
    "parse_session",
    "play_events",
    "prune_raw_logs",
    "raw_dir",
    "raw_path",
    "read_raw_log",
    "read_snapshot",
    "resolve_dealer",
    "restore_forced_passes",
    "route_device_from",
    "split_visits",
    "timezone_named",
    "unwrap",
]
