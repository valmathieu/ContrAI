"""Console entry point for the spectator scraper.

Four subcommands. ``run`` watches tables and is still the default, so a bare
``contrai-scrape`` is ``contrai-scrape run`` with the word left out — but it
now needs a profile, and a bare invocation fails with usage rather than
launching anything. That is the intended break: there is no longer a flow
that works without one.

``fleet`` is ``run`` grown sideways: several accounts on one browser, waiting
in the lobby rather than sitting where the server puts them, so that every
game is caught from its first card. One worker on the profile's own account is
a fleet too, which is how it runs without an accounts file.

``check-profile`` is the other half of unattended operation. It walks the
same site the recorder does and reports one line per check, so a site change
is found by the thing that starts a shift rather than by the shift.

``parse`` turns a raw wire log into a record. That command is why the raw log
exists: frames are stored verbatim *before* anything is interpreted, so when
the parser turns out to have mis-read something the fix applies to games
already watched rather than only to the next ones — and it is the same code
path the recorder runs live, so a bug found offline is the bug that was
happening online.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from contrai_data import GameEvent, RecordWriter, RoundDealt, game_path

from contrai_scraper.accounts import LabelledAccount, load_accounts
from contrai_scraper.browser import open_browser, open_session, open_spectator
from contrai_scraper.egress import EgressGate, EgressReading, SharedEgressGate
from contrai_scraper.exceptions import (
    BrowserError,
    ParseError,
    ScraperError,
    ShiftError,
)
from contrai_scraper.fleet import SOLE_WORKER, Fleet, FleetSummary
from contrai_scraper.frames import RawFrame, RawLogFrameSource
from contrai_scraper.health import HealthLog
from contrai_scraper.lobby import lobby_seats
from contrai_scraper.parse.session import (
    SessionResult,
    parse_session,
    split_visits,
)
from contrai_scraper.parse.snapshot import Snapshot, read_snapshot
from contrai_scraper.parse.translate import Translator
from contrai_scraper.profile import FLEET_CEILING, Profile, load_profile
from contrai_scraper.rawlog import new_session_id, raw_dir
from contrai_scraper.recorder import (
    RecorderLimits,
    # The same two helpers the recorder gates a table with. Imported rather
    # than restated so ``check-profile`` cannot pass a table the recorder
    # would refuse, or the other way round.
    _orientation_holds,
    _wire_pair,
)
from contrai_scraper.registry import TableRegistry
from contrai_scraper.shift import Shift, ShiftSummary
from contrai_scraper.wire import WireEvent, WireStream, order_events

#: The subcommands, and the one a bare invocation means.
SUBCOMMANDS: Final[tuple[str, ...]] = ("run", "fleet", "parse", "check-profile")
DEFAULT_SUBCOMMAND: Final[str] = "run"

#: Where a directory argument is searched for logs.
RAW_GLOB: Final[str] = "*.jsonl"

#: Seconds in a minute, since ``--minutes`` is the friendlier flag and
#: :class:`~contrai_scraper.recorder.RecorderLimits` counts in seconds.
_MINUTE: Final[float] = 60.0

#: The exit status a shell gives a process stopped by an interrupt.
EXIT_INTERRUPTED: Final[int] = 130

#: The exit status of a shift that spent a failure budget: hand the process
#: back to its supervisor for a fresh start.
EXIT_SHIFT_ENDED: Final[int] = 3


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``contrai-scrape`` console script.

    Args:
        argv: Argument strings, excluding the program name. ``None`` (the
            default) reads ``sys.argv[1:]``.

    Returns:
        The process exit code: 0 on success, 1 when a parse produced no
        record at all or a profile check failed.

    Raises:
        SystemExit: On a usage error (exit code 2).
    """

    _reconfigure_streams()
    args = _parse_argv(argv)
    # Built here rather than at import time: a table bound to the module's
    # functions once would keep answering with them after a test, or a
    # future wrapper, rebound one.
    dispatch = {
        "run": _run_recorder,
        "fleet": _run_fleet,
        "check-profile": _run_check,
        "parse": _run_parse,
    }
    return dispatch[args.command](args)


def _reconfigure_streams() -> None:
    """Switch stdout and stderr to UTF-8, where the streams allow it.

    Before the dispatch rather than inside one command: the health log
    carries the parser's notes, a note names a card, and a card carries a
    suit glyph — which cp1252, still the default code page on a legacy
    Windows console, raises on rather than degrading.

    A stream that cannot be reconfigured is left alone: some are already
    UTF-8, some are pipes, and none of that is worth failing a run over.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001 - a stream that refuses is fine
                pass


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _treat_sigterm_as_interrupt() -> None:
    """Make SIGTERM take the path Ctrl+C takes.

    A service manager stops a process with SIGTERM, whose default is to end
    it without raising — so nothing unwinds and the game being watched is
    never written. Inside ``asyncio.run`` the SIGINT handler is asyncio's
    own, which cancels the main task; installing it for SIGTERM too is what
    lets the recorder close its record as ``interrupted``.

    Must be called from inside the running coroutine: asyncio installs its
    handler only for the duration of ``run``.
    """

    handler = signal.getsignal(signal.SIGINT)
    if callable(handler):
        signal.signal(signal.SIGTERM, handler)


def _run_recorder(args: argparse.Namespace) -> int:
    """Watch tables until the limits are reached.

    Args:
        args: The parsed ``run`` arguments.

    Returns:
        0; 130 when an interrupt stopped the run; 3 when a failure budget was
        spent. A shift that watched nothing is not a failure — an empty shift
        is what a quiet evening looks like, and the health log says so.

    Raises:
        SystemExit: If the profile cannot be read (exit code 2).
    """

    profile = _profile_or_exit(args)
    limits = RecorderLimits(
        max_games=args.max_games,
        max_seconds=None if args.minutes is None else args.minutes * _MINUTE,
    )
    try:
        asyncio.run(_shift(profile, limits, args.headless))
    except KeyboardInterrupt:
        # Both Ctrl+C and, through the handler above, a service stop: the
        # recorder has already written the game in hand as interrupted.
        return EXIT_INTERRUPTED
    except ShiftError as error:
        # A spent budget. A code of its own hands the process back to its
        # supervisor, which starts a fresh one.
        print(f"contrai-scrape: {error}", file=sys.stderr)
        return EXIT_SHIFT_ENDED
    return 0


async def _shift(  # pragma: no cover - needs a real browser
    profile: Profile, limits: RecorderLimits, headless: bool | None
) -> ShiftSummary:
    """Run shifts until the limits stop them, printing what they did.

    Args:
        profile: The loaded profile.
        limits: When to stop, across every session.
        headless: Override for ``[browser].headless``; ``None`` takes it.

    Returns:
        What the shift did, across its sessions.
    """

    _treat_sigterm_as_interrupt()
    shift = Shift(
        profile,
        HealthLog(),
        open_session=open_spectator,
        egress=EgressGate(profile.egress, profile.site.url),
        headless=headless,
        limits=limits,
    )
    summary = await shift.run()
    print(
        f"{summary.sessions} sessions, {summary.games_recorded} games, "
        f"{summary.tables_seated} tables seated, {summary.tables_rejected} rejected"
    )
    for path in summary.records:
        print(f"  -> {path}")
    return summary


# ---------------------------------------------------------------------------
# fleet
# ---------------------------------------------------------------------------


def _run_fleet(args: argparse.Namespace) -> int:
    """Run workers that wait in the lobby and chase every game that starts.

    Args:
        args: The parsed ``fleet`` arguments.

    Returns:
        0; 130 when an interrupt stopped the run; 3 when the fleet handed
        itself back — egress refused too often, or most workers down.

    Raises:
        SystemExit: If the profile cannot run a fleet, or the accounts do
            not add up (exit code 2).
    """

    profile = _profile_or_exit(args)
    gaps = profile.fleet_gaps()
    if gaps:
        args.parser.error(f"this profile cannot run a fleet; it needs {', '.join(gaps)}")
    accounts = _fleet_accounts(args, profile)
    limits = RecorderLimits(
        max_games=args.max_games,
        max_seconds=None if args.minutes is None else args.minutes * _MINUTE,
    )
    try:
        asyncio.run(_fleet(profile, accounts, limits, args.headless))
    except KeyboardInterrupt:
        # Every worker's recorder has written its game in hand as interrupted.
        return EXIT_INTERRUPTED
    except ShiftError as error:
        print(f"contrai-scrape: {error}", file=sys.stderr)
        return EXIT_SHIFT_ENDED
    return 0


def _fleet_accounts(
    args: argparse.Namespace, profile: Profile
) -> tuple[LabelledAccount, ...]:
    """The accounts the fleet's workers log in with, one each.

    Without ``--accounts`` a fleet is one worker on the profile's own
    ``[account]`` — the fleet's way of working, at the size ``run`` works at.
    With it, ``--workers`` (or ``[fleet].workers``) takes that many accounts
    from the top of the file.

    Raises:
        SystemExit: If the accounts cannot be read, or there are fewer of
            them than workers asked for, or the count is out of range.
    """

    assert profile.fleet is not None
    if args.accounts is None:
        if args.workers not in (None, 1):
            args.parser.error(
                "more than one worker needs --accounts: one account per worker"
            )
        return (LabelledAccount(label=SOLE_WORKER, account=profile.account),)
    try:
        accounts = load_accounts(args.accounts)
    except ScraperError as error:
        args.parser.error(str(error))
    wanted = profile.fleet.workers if args.workers is None else args.workers
    if not 1 <= wanted <= FLEET_CEILING:
        args.parser.error(f"--workers must be between 1 and {FLEET_CEILING}")
    if wanted > len(accounts):
        args.parser.error(
            f"{wanted} workers asked for, but {args.accounts} holds "
            f"{len(accounts)} account(s)"
        )
    return accounts[:wanted]


async def _fleet(  # pragma: no cover - needs a real browser
    profile: Profile,
    accounts: tuple[LabelledAccount, ...],
    limits: RecorderLimits,
    headless: bool | None,
) -> FleetSummary:
    """Run the fleet until its limits stop it, printing what it did.

    Args:
        profile: The loaded profile, which can run a fleet.
        accounts: One per worker.
        limits: When to stop, across the fleet.
        headless: Override for ``[browser].headless``; ``None`` takes it.

    Returns:
        What the fleet did.
    """

    _treat_sigterm_as_interrupt()
    section = profile.fleet
    assert section is not None
    fleet = Fleet(
        profile,
        accounts,
        HealthLog(),
        open_browser=open_browser,
        open_session=open_session,
        egress=SharedEgressGate(
            EgressGate(profile.egress, profile.site.url),
            max_age_s=section.egress_cache_s,
        ),
        registry=TableRegistry(claim_ttl_s=section.claim_ttl_s),
        headless=headless,
        limits=limits,
    )
    summary = await fleet.run()
    print(
        f"{summary.windows} windows, {summary.chases} chases "
        f"({summary.chases_given_up} given up), {summary.games_recorded} games, "
        f"{summary.tables_seated} tables seated, {summary.tables_rejected} rejected"
    )
    for path in summary.records:
        print(f"  -> {path}")
    if summary.workers_down:
        print(f"  workers down: {', '.join(summary.workers_down)}")
    return summary


# ---------------------------------------------------------------------------
# check-profile
# ---------------------------------------------------------------------------


def _run_check(args: argparse.Namespace) -> int:
    """Validate a profile against the live site, one line per check.

    The two checks that need no browser run first and separately: a profile
    that does not load, or a seat map that turns the table backwards, makes
    every later answer meaningless.

    Args:
        args: The parsed ``check-profile`` arguments.

    Returns:
        0 when every check passed, 1 otherwise.
    """

    try:
        profile = load_profile(args.profile)
    except ScraperError as error:
        return _report([("profile loads", False, str(error))])

    results = [("profile loads", True, str(args.profile))]
    try:
        Translator(profile)
    except ScraperError as error:
        return _report([*results, ("rotation holds", False, str(error))])

    results.append(("rotation holds", True, "the seat map walks the table's cycle"))

    egress = _egress_result(_egress_reading(profile))
    results.append(egress)
    if not egress[1]:
        # A refused egress means the next step would walk the site from the
        # wrong address. Stop here rather than report on a walk never taken.
        return _report(results)

    results += asyncio.run(_check(profile, args.headless))
    return _report(results)


def _egress_reading(profile: Profile) -> EgressReading:  # pragma: no cover - real network
    """Ask the network where this machine's traffic leaves."""

    return EgressGate(profile.egress, profile.site.url).check_now()


def _egress_result(reading: EgressReading) -> tuple[str, bool, str]:
    """One check line for an egress reading, never naming the home address.

    Args:
        reading: What the gate answered.

    Returns:
        The ``(name, passed, detail)`` triple the report prints.
    """

    name = "egress leaves through the tunnel"
    if reading.ok:
        route = reading.route_device or "not checked on this machine"
        return (name, True, f"exit {reading.exit_ip} ({reading.country}), route {route}")
    detail = ", ".join(f"{key} {value}" for key, value in reading.fields().items() if value)
    return (name, False, f"refused, nothing sent to the site: {detail}")


def _report(results: Sequence[tuple[str, bool, str]]) -> int:
    """Print one line per check and decide the exit code.

    Args:
        results: ``(name, passed, detail)`` per check.

    Returns:
        0 when every check passed, 1 otherwise.
    """

    for name, passed, detail in results:
        mark = "ok  " if passed else "FAIL"
        print(f"{mark}  {name}" + (f" — {detail}" if detail else ""))
    failed = [name for name, passed, _ in results if not passed]
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
    return 1 if failed else 0


async def _check(  # pragma: no cover - needs a real browser
    profile: Profile, headless: bool | None
) -> list[tuple[str, bool, str]]:
    """Walk the site once, cross-checking everything the profile claims.

    Args:
        profile: The loaded profile.
        headless: Override for ``[browser].headless``; ``None`` takes it.

    Returns:
        ``(name, passed, detail)`` per live check, in the order they ran.
    """

    async with open_browser(profile, headless=headless) as browser:
        async with open_session(browser, profile) as (spectator, frames):
            results = await _live_checks(spectator, frames, profile)
        if not profile.selectors.has_lobby:
            return [*results, LOBBY_NOT_DESCRIBED]
        # A session of its own, logged in afresh: the lobby is checked by the
        # route a fleet takes into it, not from the table the checks above
        # left the page on — the way back is checked from a table the lobby
        # itself led to, as a fleet meets it.
        async with open_session(browser, profile) as (spectator, frames):
            return [*results, *await _lobby_checks(spectator, frames, profile)]


#: The line a profile describing no lobby gets: a fact, not a failure, since
#: only a fleet needs one.
LOBBY_NOT_DESCRIBED: Final[tuple[str, bool, str]] = (
    "lobby described", True, "no — only `fleet` needs it",
)

#: The line a lobby without a table exit gets: again a fact, since a fleet
#: still works without one, only by rebuilding its session after each table.
TABLE_EXIT_NOT_DESCRIBED: Final[tuple[str, bool, str]] = (
    "back to the lobby from a table", True,
    "not checked — no [selectors].table_exit, so each return rebuilds the session",
)


async def _lobby_checks(
    spectator: Any, frames: Any, profile: Profile
) -> list[tuple[str, bool, str]]:
    """The lobby, walked the way a fleet walks it: in, read, and out to a table.

    Split from :func:`_check` so the walk runs against a scripted spectator.
    A browser step that fails ends it with a failed line naming the check it
    was on, exactly as the table checks do.

    Args:
        spectator: A fresh browser half, not yet logged in.
        frames: The frame source its page feeds.
        profile: The loaded profile, which describes the lobby.

    Returns:
        ``(name, passed, detail)`` per check, in the order they ran.
    """

    results: list[tuple[str, bool, str]] = []
    step = "lobby entered"
    try:
        await spectator.log_in()
        await spectator.enter_lobby()
        results.append((step, True, "the list of games is showing"))

        step = "tournament row found"
        table_hash = await spectator.read_tournament_hash()
        results.append((
            step,
            table_hash is not None,
            "the list shows a tournament row" if table_hash is not None
            else "no row carries [selectors].lobby_row_tournament_class",
        ))

        step = "lobby events read"
        if profile.wire.has_lobby:
            event = await _first_event(frames, profile, profile.wire.events.lobby_table)
            results.append(_lobby_event_result(event, profile, table_hash))
        else:
            results.append((
                step, False,
                "the profile names no [wire.events].lobby_table; a fleet needs it "
                "and the [wire.fields] lobby paths",
            ))

        step = "back to a table from the lobby"
        await spectator.enter_table_from_lobby()
        event = await _first_snapshot(frames, profile)
        results.append((
            step,
            event is not None,
            "the server chose a table" if event is not None
            else "no snapshot within the timeout",
        ))
        if event is None:
            return results

        step = "back to the lobby from a table"
        if profile.selectors.table_exit is None:
            results.append(TABLE_EXIT_NOT_DESCRIBED)
            return results
        # The route a fleet worker takes after every table, so it is walked
        # here rather than first met mid-shift.
        await spectator.return_to_lobby()
        table_hash = await spectator.read_tournament_hash()
        results.append((
            step,
            table_hash is not None,
            "the list of games is showing again" if table_hash is not None
            else "no tournament row in the list reached",
        ))
    except BrowserError as error:
        detail = str(error)
        evidence = await _capture_failure(spectator, profile)
        results.append((step, False, f"{detail} — {evidence}" if evidence else detail))
    return results


async def _live_checks(
    spectator: Any, frames: Any, profile: Profile
) -> list[tuple[str, bool, str]]:
    """The live checks, over a browser that is already open.

    Split from :func:`_check` so the walk runs against a scripted spectator.
    A browser step that fails ends the walk with a failed line naming the
    check it was on: ``check-profile`` exists to find a site change, and a
    traceback would bury the one line that says which profile key stopped
    matching.

    Args:
        spectator: The browser half, or anything with its surface.
        frames: The frame source the spectator's page feeds.
        profile: The loaded profile.

    Returns:
        ``(name, passed, detail)`` per check, in the order they ran.
    """

    translator = Translator(profile)
    results: list[tuple[str, bool, str]] = []
    step = "login"
    try:
        await spectator.log_in()
        results.append(("login", True, profile.account.email))
        step = "variant entered"
        # The pledge is drawn between the menu steps, so only the walk into the
        # variant can see it: a probe made before that walk found nothing.
        answered = await spectator.enter_variant()
        results.append(("pledge", True, "answered" if answered else "not showing"))
        results.append(("variant entered", True, "the server chose a table"))

        event = await _first_snapshot(frames, profile)
        if event is None:
            results.append(("snapshot arrives", False, "none within the timeout"))
            return results
        results.append(("snapshot arrives", True, "the table described itself"))

        # Named before the read, not after: a snapshot the profile cannot
        # read would otherwise be reported against the menu step that last
        # set ``step``, which is the one part of the walk that did work.
        step = "the snapshot reads"
        snapshot = read_snapshot(event.data, translator, at=event.received_ms)
        step = "marker agrees with the wire"
        marker = await spectator.read_tournament_marker()
        results.append((
            step,
            marker == bool(snapshot.is_tournament),
            f"rendered {marker}, wire {bool(snapshot.is_tournament)}",
        ))

        step = "options match [rules.options]"
        reading = await spectator.read_options(profile.rules.options)
        results.append((
            step,
            reading.matches,
            f"missing {list(reading.missing)}, extra {list(reading.extra)}, "
            f"differing {list(reading.differing)}",
        ))

        step = "panel ids equal the wire's accounts"
        results.append(await _seat_ids_agree(spectator, snapshot, translator))

        step = "us is the south seat's side"
        board = await spectator.read_scoreboard()
        results.append(_orientation_result(snapshot, board))
    except (BrowserError, ParseError) as error:
        # A snapshot the profile cannot read is as much a profile fault as a
        # selector that stopped matching, and this command exists to name the
        # key that broke rather than to print a traceback over it.
        detail = str(error)
        evidence = await _capture_failure(spectator, profile)
        results.append((step, False, f"{detail} — {evidence}" if evidence else detail))
    return results


async def _capture_failure(spectator: Any, profile: Profile) -> str:
    """Save the page a failed live check was looking at.

    The failed line names the profile key that stopped matching, which says
    *which* selector broke and never *why*. On a host reached through a
    console there is no second chance to look: the browser is gone by the
    time the line is read, and the walk cannot be repeated by hand. So the
    page is kept, exactly as ``run`` keeps it — the same
    ``[browser].screenshot_on_error`` switch, the same two files, written
    under the profile's raw root where a session's own evidence already
    lands.

    Args:
        spectator: The browser half, still open on the failing page.
        profile: The loaded profile.

    Returns:
        A phrase naming the files, for the failed line to carry, or the
        empty string when the profile does not ask for them or nothing
        could be written. A diagnosis never replaces the failure it was
        taken for, so this reports nothing rather than raising.
    """

    if not profile.browser.screenshot_on_error:
        return ""
    directory = raw_dir(profile.output.raw_root)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        # An unwritable raw root is worth knowing about, but not here: the
        # line this decorates is already reporting a failure of its own.
        return ""
    saved = await spectator.capture(directory / f"check-profile-{new_session_id()}")
    if not saved:
        return ""
    return f"page saved to {', '.join(str(path) for path in saved)}"


async def _first_snapshot(frames: Any, profile: Profile) -> WireEvent | None:
    """The first join snapshot off the socket, or ``None`` if none arrives."""

    return await _first_event(frames, profile, profile.wire.events.join_snapshot)


async def _first_event(frames: Any, profile: Profile, kind: str) -> WireEvent | None:
    """The first event of one kind off the socket, within ``snapshot_timeout_s``.

    The timeout is one deadline for the whole wait, not a fresh one per frame:
    the socket carries a keepalive about once a second, so a per-frame wait
    for an event that is not coming would never end.

    Args:
        frames: The frame source.
        profile: The loaded profile.
        kind: The event name to wait for.

    Returns:
        The event, or ``None`` when none arrived in time or the frames ended.
    """

    stream = WireStream(profile.wire)
    iterator = frames.__aiter__()
    deadline = time.monotonic() + profile.recorder.snapshot_timeout_s
    while True:
        try:
            frame = await asyncio.wait_for(
                anext(iterator), max(0.0, deadline - time.monotonic())
            )
        except (TimeoutError, StopAsyncIteration):
            return None
        event = stream.ingest(frame.text, frame.socket)
        if event is not None and event.kind == kind:
            return event


def _lobby_event_result(
    event: WireEvent | None, profile: Profile, table_hash: str | None
) -> tuple[str, bool, str]:
    """Whether the profile reads the lobby's socket, judged on the first event.

    Silence is not a failure: the lobby speaks only when a seat changes, and
    the first event after arriving has been measured at 27 s and at 82 s. So
    a quiet wait passes and says it proved nothing; an event that arrived and
    cannot be read is what fails, naming the path that read nothing.

    Args:
        event: The first lobby event, or ``None`` when none came.
        profile: The loaded profile, which reads the lobby's socket.
        table_hash: The tournament row's hash as the page gave it.

    Returns:
        One check result.
    """

    name = "lobby events read"
    if event is None:
        return (
            name, True,
            f"none arrived in {profile.recorder.snapshot_timeout_s} s — a quiet "
            "lobby looks the same, so this proves nothing yet",
        )
    translator = Translator(profile)
    row = translator.field(event.data, "lobby_hash")
    if not isinstance(row, str):
        return (
            name, False,
            "an event arrived, but [wire.fields].lobby_hash names no row in it",
        )
    seats = lobby_seats(event.data, translator)
    if seats is None:
        return (
            name, False,
            "an event arrived, but [wire.fields].lobby_seats reads no seat map in it",
        )
    where = "the tournament row" if row == table_hash else "another row"
    return (name, True, f"an event for {where} named {len(seats)} seated account(s)")


async def _seat_ids_agree(
    spectator: Any, snapshot: Snapshot, translator: Translator
) -> tuple[str, bool, str]:
    """Whether every seat's panel shows the account the wire named.

    Measured equal on a live table, which is what makes the per-seat panel
    read redundant for the recorder — and what makes a disagreement here a
    site change worth stopping for.
    """

    mismatched = []
    for handle, position in snapshot.seats.items():
        player = snapshot.players.get(handle)
        expected = None if player is None else player.account
        shown = await spectator.read_player_id(position)
        if shown != expected:
            mismatched.append(f"{position.value}: panel {shown}, wire {expected}")
    return (
        "panel ids equal the wire's accounts",
        not mismatched,
        "; ".join(mismatched) or "all four seats agree",
    )


def _orientation_result(snapshot, board) -> tuple[str, bool, str]:
    """Whether the scoreboard's left-hand column is the south seat's side.

    Args:
        snapshot: The table's own account of the rounds scored so far.
        board: What the scoreboard panel showed.

    Returns:
        One check result. With no scored round there is nothing to compare,
        which is reported as passing and said so in the detail.
    """

    if not snapshot.score_rows or not board.rows:
        return ("us is the south seat's side", True, "no scored round to compare")
    return (
        "us is the south seat's side",
        _orientation_holds(snapshot, board.rows),
        f"panel {list(board.rows[-1])}, wire {list(_wire_pair(snapshot.score_rows[-1]))}",
    )


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def _run_parse(args: argparse.Namespace) -> int:
    """Re-parse raw logs into records.

    Args:
        args: The parsed ``parse`` arguments.

    Returns:
        0 when at least one record was produced, 1 when none was.

    Raises:
        SystemExit: If the profile or a path cannot be read (exit code 2).
    """

    profile = _profile_or_exit(args)
    logs = _logs(args.paths, args.parser)
    root = args.out or profile.output.root
    written = 0
    for log in logs:
        results, visits = _parse_log(log, profile)
        print(f"{log.name}: {visits} table visits, {len(results)} with rounds")
        for result in results:
            rounds = _round_count(result.events)
            header = result.events[0]
            summary = f"  {header.game_id}, {rounds} rounds"
            if result.skipped_rounds:
                summary += (
                    f", {len(result.skipped_rounds)} skipped "
                    f"({', '.join(str(n) for n in result.skipped_rounds)})"
                )
            print(summary)
            for note in result.notes:
                print(f"    - {note}")

            if args.dry_run:
                written += 1
                continue
            path = game_path(root, header.game_id)
            with RecordWriter(path) as writer:
                for event in result.events:
                    writer.write(event)
            print(f"    -> {path}")
            written += 1

    return 0 if written else 1


def _parse_log(log: Path, profile: Profile) -> tuple[list[SessionResult], int]:
    """Replay one raw log, one record per table visit that held a game.

    A frame source is an async iterator, because the live one has to be —
    Playwright hands frames over through callbacks. Draining it here is the
    price of the replay and the live run being literally the same path.

    The log is cut into visits before anything is parsed: it holds every
    table the session looked at, and one game is what ``parse_session``
    builds. A visit that yielded no round is not a game and gets no record —
    most of them are tables a gate refused seconds after arriving — and a
    visit this profile cannot read is reported rather than raised, so one
    bad table does not cost the rest of the log.

    Args:
        log: The raw log to replay.
        profile: The loaded profile.

    Returns:
        The records worth writing, and how many visits the log held.
    """

    frames = asyncio.run(_drain(RawLogFrameSource(log)))
    stream = WireStream(profile.wire)
    events = [
        event
        for frame in frames
        if (event := stream.ingest(frame.text, frame.socket)) is not None
    ]
    visits = split_visits(events, profile)
    results: list[SessionResult] = []
    for visit in visits:
        try:
            result = parse_session(order_events(visit), profile)
        except ScraperError as error:
            print(f"  a visit could not be read: {error}")
            continue
        if _round_count(result.events):
            results.append(result)
    return results, len(visits)


async def _drain(source: RawLogFrameSource) -> list[RawFrame]:
    """Collect every frame a source yields."""

    return [frame async for frame in source]


def _round_count(events: Sequence[GameEvent]) -> int:
    """How many rounds a record holds."""

    return sum(1 for event in events if isinstance(event, RoundDealt))


def _logs(paths: list[Path], parser: argparse.ArgumentParser) -> list[Path]:
    """Resolve the path arguments into raw log files.

    A directory is searched for logs, its ``raw`` subdirectory included, which
    mirrors how ``contrai verify`` takes either a record or a corpus.

    Args:
        paths: The path arguments.
        parser: The subparser, for usage errors.

    Returns:
        The log files, de-duplicated and in a stable order.

    Raises:
        SystemExit: If a path does not exist (exit code 2).
    """

    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(path.glob(RAW_GLOB))
            found += sorted((path / "raw").glob(RAW_GLOB))
        elif path.is_file():
            found.append(path)
        else:
            parser.error(f"{path}: no such file or directory")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# arguments
# ---------------------------------------------------------------------------


def _profile_or_exit(args: argparse.Namespace) -> Profile:
    """Load the profile, or fail with usage rather than a traceback.

    Raises:
        SystemExit: If the profile cannot be read (exit code 2).
    """

    try:
        return load_profile(args.profile)
    except ScraperError as error:
        args.parser.error(str(error))


def _parse_argv(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse ``argv``, inserting the default subcommand.

    The order is load-bearing: ``sys.argv`` is resolved **before**
    normalising, never after, so the one path that matters — a bare
    ``contrai-scrape`` — is normalised too.

    Args:
        argv: Argument strings, excluding the program name.

    Returns:
        The parsed namespace, carrying the subparser that produced it so a
        later usage error is reported against the flags the user typed.

    Raises:
        SystemExit: If the arguments fail to parse (exit code 2).
    """

    resolved = list(sys.argv[1:] if argv is None else argv)
    parser, subparsers = _build_parser()
    args = parser.parse_args(_normalise_argv(resolved))
    args.parser = subparsers[args.command]
    return args


def _normalise_argv(argv: list[str]) -> list[str]:
    """Insert the default subcommand when the arguments do not name one.

    This is what keeps ``contrai-scrape --profile P`` working: it is
    ``contrai-scrape run --profile P`` with the word left out, and it is put
    back here rather than by teaching ``argparse`` an optional subcommand,
    which it does not really have.
    """

    if argv and argv[0] in SUBCOMMANDS:
        return list(argv)
    return [DEFAULT_SUBCOMMAND, *argv]


def _add_headless(parser: argparse.ArgumentParser) -> None:
    """Add the headless pair, which defaults to whatever the profile says."""

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=None,
        help="run the browser without a window (default: the profile's)",
    )
    group.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="run the browser with a window",
    )


def _build_parser() -> tuple[
    argparse.ArgumentParser, dict[str, argparse.ArgumentParser]
]:
    """Build the ``contrai-scrape`` parser and its subparsers."""

    parser = argparse.ArgumentParser(prog="contrai-scrape")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser(
        "run",
        help="watch tables (the default when no subcommand is given)",
        description=(
            "Seat a spectator at tournament tables and write one record per "
            "game watched."
        ),
        epilog="other subcommands: fleet, parse, check-profile",
    )
    run.add_argument(
        "--profile",
        type=Path,
        required=True,
        metavar="FILE",
        help="the profile describing the site to watch",
    )
    run.add_argument(
        "--max-games",
        type=int,
        default=None,
        metavar="N",
        help="stop after this many games (default: no limit)",
    )
    run.add_argument(
        "--minutes",
        type=float,
        default=None,
        metavar="N",
        help="stop after this long (default: no limit)",
    )
    _add_headless(run)

    fleet = subcommands.add_parser(
        "fleet",
        help="run workers that wait in the lobby and chase every game that starts",
        description=(
            "Log spectator accounts in on one browser, wait in the lobby, and "
            "chase each tournament game to its table from its first card, one "
            "record per game."
        ),
    )
    fleet.add_argument(
        "--profile",
        type=Path,
        required=True,
        metavar="FILE",
        help="the profile describing the site, its lobby and [fleet]",
    )
    fleet.add_argument(
        "--accounts",
        type=Path,
        default=None,
        metavar="FILE",
        help="the accounts, one per worker (default: one worker, the profile's [account])",
    )
    fleet.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=f"how many workers, at most {FLEET_CEILING} (default: [fleet].workers)",
    )
    fleet.add_argument(
        "--max-games",
        type=int,
        default=None,
        metavar="N",
        help="take no new chase after this many games (default: no limit)",
    )
    fleet.add_argument(
        "--minutes",
        type=float,
        default=None,
        metavar="N",
        help="stop after this long (default: no limit)",
    )
    _add_headless(fleet)

    check = subcommands.add_parser(
        "check-profile",
        help="validate a profile against the live site",
        description=(
            "Walk the site once and report one line per check. Exits 1 on the "
            "first failure, so it can gate a shift before it starts."
        ),
    )
    check.add_argument(
        "profile",
        type=Path,
        metavar="FILE",
        help="the profile to validate",
    )
    _add_headless(check)

    parse = subcommands.add_parser(
        "parse",
        help="turn raw wire logs into records",
        description=(
            "Re-parse raw wire logs into contrai-data records. A directory "
            "argument is searched for logs, its raw/ subdirectory included."
        ),
    )
    parse.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="RAW",
        help="raw log files, or directories holding them",
    )
    parse.add_argument(
        "--profile",
        type=Path,
        required=True,
        metavar="FILE",
        help="the profile describing the site the logs came from",
    )
    parse.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="ROOT",
        help="write records under this root (default: the profile's)",
    )
    parse.add_argument(
        "--dry-run",
        action="store_true",
        help="report only; do not write any record",
    )
    return parser, {"run": run, "fleet": fleet, "check-profile": check, "parse": parse}


if __name__ == "__main__":  # pragma: no cover - console-script shim
    raise SystemExit(main())
