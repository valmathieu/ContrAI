"""Console entry point for the spectator scraper.

Three subcommands. ``run`` watches tables and is still the default, so a bare
``contrai-scrape`` is ``contrai-scrape run`` with the word left out — but it
now needs a profile, and a bare invocation fails with usage rather than
launching anything. That is the intended break: there is no longer a flow
that works without one.

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
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from contrai_data import GameEvent, RecordWriter, RoundDealt, game_path

from contrai_scraper.browser import open_spectator
from contrai_scraper.egress import EgressGate, EgressReading
from contrai_scraper.exceptions import BrowserError, ScraperError, ShiftError
from contrai_scraper.frames import RawFrame, RawLogFrameSource
from contrai_scraper.health import HealthLog
from contrai_scraper.parse.session import SessionResult, parse_session
from contrai_scraper.parse.snapshot import Snapshot, read_snapshot
from contrai_scraper.parse.translate import Translator
from contrai_scraper.profile import Profile, load_profile
from contrai_scraper.recorder import (
    RecorderLimits,
    # The same two helpers the recorder gates a table with. Imported rather
    # than restated so ``check-profile`` cannot pass a table the recorder
    # would refuse, or the other way round.
    _orientation_holds,
    _wire_pair,
)
from contrai_scraper.shift import Shift, ShiftSummary
from contrai_scraper.wire import WireEvent, WireStream, order_events

#: The subcommands, and the one a bare invocation means.
SUBCOMMANDS: Final[tuple[str, ...]] = ("run", "parse", "check-profile")
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

    async with open_spectator(profile, headless=headless) as (spectator, frames):
        return await _live_checks(spectator, frames, profile)


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
    except BrowserError as error:
        results.append((step, False, str(error)))
    return results


async def _first_snapshot(frames: Any, profile: Profile) -> WireEvent | None:
    """The first join snapshot off the socket, or ``None`` if none arrives."""

    stream = WireStream(profile.wire)
    name = profile.wire.events.join_snapshot
    iterator = frames.__aiter__()
    timeout = profile.recorder.snapshot_timeout_s
    while True:
        try:
            frame = await asyncio.wait_for(anext(iterator), timeout)
        except (TimeoutError, StopAsyncIteration):
            return None
        event = stream.ingest(frame.text, frame.socket)
        if event is not None and event.kind == name:
            return event


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
        try:
            result = _parse_one(log, profile)
        except ScraperError as error:
            print(f"{log.name}: {error}")
            continue

        rounds = _round_count(result.events)
        header = result.events[0]
        summary = f"{log.name}: {header.game_id}, {rounds} rounds"
        if result.skipped_rounds:
            summary += (
                f", {len(result.skipped_rounds)} skipped "
                f"({', '.join(str(n) for n in result.skipped_rounds)})"
            )
        print(summary)
        for note in result.notes:
            print(f"  - {note}")

        if args.dry_run:
            written += 1
            continue
        path = game_path(root, header.game_id)
        with RecordWriter(path) as writer:
            for event in result.events:
                writer.write(event)
        print(f"  -> {path}")
        written += 1

    return 0 if written else 1


def _parse_one(log: Path, profile: Profile) -> SessionResult:
    """Replay one raw log through the whole pipeline.

    A frame source is an async iterator, because the live one has to be —
    Playwright hands frames over through callbacks. Draining it here is the
    price of the replay and the live run being literally the same path.
    """

    frames = asyncio.run(_drain(RawLogFrameSource(log)))
    stream = WireStream(profile.wire)
    events = [
        event
        for frame in frames
        if (event := stream.ingest(frame.text, frame.socket)) is not None
    ]
    return parse_session(order_events(events), profile)


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
        epilog="other subcommands: parse, check-profile",
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
    return parser, {"run": run, "check-profile": check, "parse": parse}


if __name__ == "__main__":  # pragma: no cover - console-script shim
    raise SystemExit(main())
