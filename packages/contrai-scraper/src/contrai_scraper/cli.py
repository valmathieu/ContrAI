"""Console entry point for the spectator scraper.

Two subcommands. ``run`` is the v1 browser flow, unchanged and still the
default: a bare ``contrai-scrape`` is ``contrai-scrape run`` with the word
left out, put back before the parser sees it, so every invocation that
worked before works now. ``parse`` turns a raw wire log into a record.

That second command is why the raw log exists. Frames are stored verbatim
*before* anything is interpreted, so when the parser turns out to have
mis-read something, the fix applies to games already watched rather than
only to the next ones — and it is the same code path the recorder will run
live, so a bug found offline is the bug that was happening online.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from playwright.async_api import async_playwright

from contrai_data import GameEvent, RecordWriter, RoundDealt, game_path

from contrai_scraper.exceptions import ScraperError
from contrai_scraper.frames import RawFrame, RawLogFrameSource
from contrai_scraper.observer import observe_game
from contrai_scraper.parse.session import SessionResult, parse_session
from contrai_scraper.profile import Profile, load_profile
from contrai_scraper.session import find_tournament_table, log_in, open_spectator_mode
from contrai_scraper.wire import WireStream, order_events

#: The subcommands, and the one a bare invocation means.
SUBCOMMANDS: Final[tuple[str, ...]] = ("run", "parse")
DEFAULT_SUBCOMMAND: Final[str] = "run"

#: Where a directory argument is searched for logs.
RAW_GLOB: Final[str] = "*.jsonl"


async def scrape() -> None:
    """Runs one full scraping session, from cold browser to observed table.

    The browser stays headed and slowed down: the site is watched, not driven
    hard, and a visible window makes the run auditable while the flow is still
    being reverse-engineered.
    """
    print("🚀 Bot starts...")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, slow_mo=500)
        page = await browser.new_page()

        await log_in(page)
        await open_spectator_mode(page)

        if await find_tournament_table(page):
            await observe_game(page)
        else:
            print("❌ Could not find a suitable table.")

        await browser.close()
        print("🏁 Script finished.")


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``contrai-scrape`` console script.

    Args:
        argv: Argument strings, excluding the program name. ``None`` (the
            default) reads ``sys.argv[1:]``.

    Returns:
        The process exit code: 0 on success, 1 when a parse produced no
        record at all.

    Raises:
        SystemExit: On a usage error (exit code 2).
    """

    args = _parse_argv(argv)
    if args.command == "parse":
        return _run_parse(args)
    _run_browser(args)
    return 0


def _run_browser(args: argparse.Namespace) -> None:
    """Run the v1 browser flow.

    Playwright's subprocess transport needs the proactor event loop on
    Windows; the default selector loop cannot spawn the browser driver. The
    ``parse`` path is synchronous and deliberately never reaches this.
    """

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(scrape())


def _run_parse(args: argparse.Namespace) -> int:
    """Re-parse raw logs into records.

    Args:
        args: The parsed ``parse`` arguments.

    Returns:
        0 when at least one record was produced, 1 when none was.

    Raises:
        SystemExit: If the profile or a path cannot be read (exit code 2).
    """

    try:
        profile = load_profile(args.profile)
    except ScraperError as error:
        args.parser.error(str(error))

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

    This is what keeps a bare ``contrai-scrape`` working: it is
    ``contrai-scrape run`` with the word left out, and it is put back here
    rather than by teaching ``argparse`` an optional subcommand, which it
    does not really have.
    """

    if argv and argv[0] in SUBCOMMANDS:
        return list(argv)
    return [DEFAULT_SUBCOMMAND, *argv]


def _build_parser() -> tuple[
    argparse.ArgumentParser, dict[str, argparse.ArgumentParser]
]:
    """Build the ``contrai-scrape`` parser and its subparsers."""

    parser = argparse.ArgumentParser(prog="contrai-scrape")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser(
        "run",
        help="watch a table (the default when no subcommand is given)",
        description="Drive a browser to a spectator seat and watch a table.",
        epilog="other subcommands: parse (see: contrai-scrape parse --help)",
    )

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
    return parser, {"run": run, "parse": parse}


if __name__ == "__main__":  # pragma: no cover - console-script shim
    raise SystemExit(main())
