"""``contrai`` CLI entry point.

Four subcommands, and ``play`` is the default. It is inserted by
:func:`_normalise_argv` whenever the first argument does not name one, so
bare ``contrai`` and every flag that predates subcommands parse exactly
as they did; ``contrai verify PATH...`` replays recorded games and
reports what does not add up, exiting non-zero on a suspect round,
``contrai replay PATH`` steps one through the game's own screens with
every hand face up, and ``contrai catalog [ROOT]`` rebuilds a records
root's SQLite index of what the other two write — or, with ``--player``,
reads one player's games back out of it.

The ``play`` path drives the landing → game loop → end-game flow, wiring
a :class:`RichView` into ``Game.manage_round``. Pure orchestration —
all rendering lives in :mod:`contrai_engine.view.rich_view`.

Also owns the three debug-mode flags (``--debug``, ``--seed``,
``--autoplay``): parsing them into a :class:`DebugOptions`
(:func:`_parse_args`), applying the seed to the global ``random``
module (:func:`_apply_seed`), and threading the result into both the
view (:class:`RichView`) and the game's seating (:func:`_build_game`).

The two mutually exclusive ruleset flags (``--rules FILE`` /
``--preset NAME``) and the ``--no-live-score`` aid switch are resolved in
the same pass, through :func:`contrai_engine.ruleset.resolve_setup`, into
the :class:`~contrai_engine.ruleset.TableSetup` the run starts from: the
:class:`~contrai_core.RuleConfig` the :class:`Game` is built under, plus
the :class:`~contrai_engine.options.TableAids` the view reads. A
malformed, unreadable or impossible ruleset is reported as an
``argparse`` usage error rather than a traceback.

That resolved setup is what the landing screen opens on and edits: a
player can pick a preset, load a file, turn any of the 22 knobs or
switch the live round score without leaving the screen, and what
:meth:`RichView.show_landing` hands back is what the next :class:`Game`
is built from. The model then owns every rule it names —
``Game.check_game_over()`` reads the target off ``game.rules`` and the
loop carries nothing alongside the game — while the interface aids stay
on the view, re-pointed each time the screen returns.

Recording (``--record`` / ``--no-record``, and the ``record`` table knob)
is resolved the same way, into a
:class:`~contrai_engine.recording.RecordRequest` the loop re-reads after
every landing screen. When it names a root, ``main`` drives a
:class:`~contrai_engine.recording.RecordingView` wrapped around the real
view rather than the view itself — which is why the two hooks the CLI
issues (``on_round_complete``, ``show_end_game``) end up in the record
alongside the ones the model fires, with no call site changed.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import random
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from contrai_core.exceptions import IllegalBidError, IllegalPlayError
from contrai_core.position import Position
from contrai_core.rule_config import PRESETS, RuleConfig
from contrai_data import (
    CatalogError,
    CatalogSummary,
    GameVerdict,
    PlayerReport,
    RecordError,
    Verdict,
    build_catalog,
    catalog_path,
    games_dir,
    load_game,
    player_games,
    records_root,
)
from contrai_engine.log_setup import configure_logging
from contrai_engine.model.game import Game
from contrai_engine.model.player import AiPlayer, HumanPlayer
from contrai_engine.options import DebugOptions, TableAids
from contrai_engine.recording import (
    UNSET,
    RecordingView,
    RecordRequest,
    finish_recording,
)
from contrai_engine.replay import (
    ReplayController,
    ReplayError,
    ReplayInterrupt,
    SteppingView,
    read_step_key,
    replay_rows,
    verify_game,
    verify_record,
)
from contrai_engine.replay.verify import default_out_root
from contrai_engine.ruleset import TableSetup, resolve_setup, save_setup, setup_path
from contrai_engine.view.parsing import RoundPick
from contrai_engine.view.rich_view import RichView

if TYPE_CHECKING:
    from contrai_data import GameRecord


# TODO: replace with a seat picker on the landing screen. For now the
# layout matches the design handoff exactly: South is the human, the
# other three seats are AI (expert — the default strategies) — unless
# ``--autoplay`` is set, in which case South is an AI too (see
# ``_build_game``).
HUMAN_SEAT = Position.SOUTH

logger = logging.getLogger(__name__)


#: The subcommands ``contrai`` answers to. Anything else in first
#: position is a flag (or a mistake) belonging to the default one.
SUBCOMMANDS: Final[tuple[str, ...]] = ("play", "replay", "verify", "catalog")

#: The subcommand a bare ``contrai`` means.
DEFAULT_SUBCOMMAND: Final[str] = "play"


def _normalise_argv(argv: list[str]) -> list[str]:
    """Insert the default subcommand when the arguments do not name one.

    This is what keeps ``contrai``, ``contrai --autoplay`` and every
    other invocation that worked before working unchanged: they are
    ``contrai play …`` with the word left out, and it is put back here
    rather than by teaching ``argparse`` an optional subcommand — which
    it does not really have.

    Args:
        argv: The argument strings, excluding the program name.

    Returns:
        The same arguments, with a subcommand guaranteed in front.
    """

    if argv and argv[0] in SUBCOMMANDS:
        return list(argv)
    return [DEFAULT_SUBCOMMAND, *argv]


def _build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """Build the ``contrai`` argument parser and its ``play`` subparser.

    Both are returned because ``parser.error`` reporting has to happen on
    the *subparser*: an invalid ruleset reported through the top-level
    parser would print a usage line naming none of the flags the user
    actually typed.

    Returns:
        The top-level parser, and the ``play`` subparser.
    """

    parser = argparse.ArgumentParser(prog="contrai")
    subcommands = parser.add_subparsers(dest="command", required=True)
    play = subcommands.add_parser(
        "play",
        help="play a game (the default when no subcommand is given)",
        description="Play a game of contrée against three AI seats.",
        # ``contrai --help`` normalises to ``contrai play --help``, so
        # this is where a reader finds out the other subcommand exists.
        epilog="other subcommands: replay, verify, catalog "
        "(see: contrai replay --help)",
    )
    _add_replay_parser(subcommands)
    _add_verify_parser(subcommands)
    _add_catalog_parser(subcommands)
    play.add_argument(
        "--debug",
        action="store_true",
        help="face-up hands and DEBUG-level diagnostics written to "
        "contrai-debug.log",
    )
    play.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed the game's RNG for a reproducible deal and dealer",
    )
    play.add_argument(
        "--autoplay",
        action="store_true",
        help="run one full unattended game with an AI at every seat",
    )
    play.add_argument(
        "--no-live-score",
        action="store_true",
        help="hide the running round points from the in-game Round panel",
    )
    # A file and a named preset are two ways of saying the same thing, so
    # argparse refuses the pair itself — ``resolve_rules`` guards the same
    # case for non-CLI callers.
    rules = play.add_mutually_exclusive_group()
    rules.add_argument(
        "--rules",
        type=Path,
        default=None,
        metavar="FILE",
        help="play under the table ruleset in this TOML file",
    )
    rules.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default=None,
        help="play under a named built-in ruleset",
    )
    # Recording is off unless asked for, and asked for two ways that cannot
    # both be meant, so argparse refuses the pair itself.
    record = play.add_mutually_exclusive_group()
    record.add_argument(
        "--record",
        nargs="?",
        const=None,
        default=UNSET,
        type=Path,
        metavar="DIR",
        help="write this game to a record under DIR (default: "
        "$CONTRAI_HOME/records)",
    )
    record.add_argument(
        "--no-record",
        action="store_true",
        help="do not record, whatever the table's record knob says",
    )
    return parser, play


def _add_replay_parser(subcommands: Any) -> argparse.ArgumentParser:
    """Register the ``replay`` subcommand.

    Args:
        subcommands: The top-level parser's subcommand group.

    Returns:
        The ``replay`` subparser.
    """

    replay = subcommands.add_parser(
        "replay",
        help="step through a recorded game in the terminal",
        description=(
            "Replay a record through the engine and walk it one action at "
            "a time, every hand face up. Opens on the game's rounds with "
            "their verification verdicts."
        ),
    )
    replay.add_argument(
        "path",
        type=Path,
        metavar="PATH",
        help="the record file to step through",
    )
    replay.add_argument(
        "--round",
        type=int,
        default=None,
        metavar="N",
        help="open straight on this round, in the record's own numbering",
    )
    return replay


def _add_verify_parser(subcommands: Any) -> argparse.ArgumentParser:
    """Register the ``verify`` subcommand.

    Args:
        subcommands: The top-level parser's subcommand group.

    Returns:
        The ``verify`` subparser.
    """

    verify = subcommands.add_parser(
        "verify",
        help="replay recorded games and report what does not add up",
        description=(
            "Replay each record through the engine and report a per-round "
            "verdict. A directory is searched for records, its games/ "
            "subdirectory included."
        ),
    )
    verify.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="record files, or directories holding them",
    )
    verify.add_argument(
        "--json",
        action="store_true",
        help="write the verdicts to stdout as JSON instead of a table",
    )
    verify.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="DIR",
        help="records root to write verdicts/<game_id>.json under "
        "(default: the record's own root)",
    )
    verify.add_argument(
        "--no-write",
        action="store_true",
        help="report only; do not write any verdict file",
    )
    return verify


def _add_catalog_parser(subcommands: Any) -> argparse.ArgumentParser:
    """Register the ``catalog`` subcommand.

    Args:
        subcommands: The top-level parser's subcommand group.

    Returns:
        The ``catalog`` subparser.
    """

    catalog = subcommands.add_parser(
        "catalog",
        help="index a records root into catalog.sqlite",
        description=(
            "Rebuild ROOT/catalog.sqlite, a SQLite index of every record "
            "and verdict under a records root, and print what it holds. "
            "Run contrai verify first: a game without a fresh verdict has "
            "no clean rounds."
        ),
    )
    catalog.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=None,
        metavar="ROOT",
        help="the records root (default: $CONTRAI_HOME/records); a games/ "
        "directory stands for its parent",
    )
    catalog.add_argument(
        "--player",
        default=None,
        metavar="ID_OR_NAME",
        help="list this player's games from the existing catalog, without "
        "rebuilding it",
    )
    return catalog


def _parse_argv(
    argv: list[str] | None = None,
) -> tuple[argparse.Namespace, argparse.ArgumentParser]:
    """Parse ``argv`` into a namespace, inserting the default subcommand.

    The order is load-bearing: ``sys.argv`` is resolved **before**
    normalising, never after. ``main`` calls this with no arguments, so
    normalising only an explicitly passed list would leave the one path
    that matters un-normalised — ``contrai --autoplay`` would exit 2 on
    an unrecognised argument, and a bare ``contrai`` would come back
    naming no subcommand at all.

    Args:
        argv: Argument strings, excluding the program name. ``None``
            (the default) reads ``sys.argv[1:]``.

    Returns:
        The parsed namespace, and the ``play`` subparser for later
        ``error`` reporting.

    Raises:
        SystemExit: If the arguments fail to parse (exit code 2).
    """

    resolved = list(sys.argv[1:] if argv is None else argv)
    parser, play = _build_parser()
    _refuse_a_bare_record(parser, resolved)
    return parser.parse_args(_normalise_argv(resolved)), play


def _refuse_a_bare_record(
    parser: argparse.ArgumentParser, argv: list[str]
) -> None:
    """Turn ``contrai some-game.jsonl`` into a message that helps.

    Without this it normalises to ``contrai play some-game.jsonl`` and
    argparse reports an unrecognised argument, which is true and useless.

    Args:
        parser: The top-level parser, for the usage error.
        argv: The raw arguments, before normalising.

    Raises:
        SystemExit: If the first argument looks like a record (exit 2).
    """

    if not argv or argv[0] in SUBCOMMANDS or argv[0].startswith("-"):
        return
    first = Path(argv[0])
    if first.suffix == ".jsonl" or first.is_file():
        parser.error(
            f"{argv[0]}: to check a record, write: contrai verify "
            f"{argv[0]}; to step through it: contrai replay {argv[0]}"
        )


def _parse_args(
    argv: list[str] | None = None,
) -> tuple[DebugOptions, TableSetup, RecordRequest]:
    """Parse the CLI's flags into the three values a game is driven from.

    Args:
        argv: Argument strings to parse, excluding the program name.
            ``None`` (the default) parses ``sys.argv[1:]``. A ``play``
            subcommand is inserted when none is named, so every
            invocation that predates subcommands parses unchanged.

    Returns:
        The parsed debug flags, the resolved table setup — the ruleset
        the game is built under plus the interface aids the view reads —
        and what the record flags asked for. No seed generation happens
        here — that is :func:`_apply_seed`'s job — so ``seed`` is ``None``
        unless ``--seed`` was passed explicitly, the setup is
        ``TableSetup()`` unless a flag named another, and the record
        request is empty unless ``--record`` / ``--no-record`` was given.

    Raises:
        SystemExit: If ``argv`` fails to parse (e.g. a non-integer
            ``--seed`` value, or ``--rules`` and ``--preset`` together),
            or if the selected ruleset is unreadable, malformed or names
            an impossible table — all reported as ``argparse`` usage
            errors (exit code 2).
    """

    args, play = _parse_argv(argv)
    return _play_setup(args, play)


def _play_setup(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> tuple[DebugOptions, TableSetup, RecordRequest]:
    """Turn a parsed ``play`` namespace into the run's three values.

    Args:
        args: The parsed ``play`` arguments.
        parser: The ``play`` subparser, used for usage errors so the
            message names the flags the user actually typed.

    Returns:
        The debug flags, the resolved table setup, and the record request.

    Raises:
        SystemExit: If the selected ruleset is unreadable, malformed or
            names an impossible table (exit code 2).
    """

    options = DebugOptions(debug=args.debug, autoplay=args.autoplay, seed=args.seed)
    # ``None`` rather than ``TableAids()`` when the flag is absent: only an
    # explicitly typed flag may override a setup file's own [table_aids].
    aids = TableAids(live_round_score=False) if args.no_live_score else None
    try:
        setup = resolve_setup(preset=args.preset, rules_path=args.rules, aids=aids)
    except (ValueError, OSError) as exc:
        # RulesetError, core's InvalidRuleConfigError, or an unreadable
        # file. ``parser.error`` prints usage + the message to stderr and
        # exits 2 — the same shape as any other bad flag.
        parser.error(str(exc))
    return options, setup, RecordRequest(
        directory=args.record, disabled=args.no_record
    )


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


def _build_game(autoplay: bool = False, rules: RuleConfig | None = None) -> Game:
    """Instantiate a fresh Game with the seating this run calls for.

    Args:
        autoplay: When ``False`` (the default), :data:`HUMAN_SEAT`
            (South) is a :class:`HumanPlayer` and the other three seats
            are :class:`AiPlayer`. When ``True``, every seat — South
            included — is an :class:`AiPlayer`, built the same way as
            the other AI seats: one unattended 4-AI game.
        rules: The table ruleset to play under. ``None`` (the default)
            leaves the game on the §9 catalogue defaults.

    Returns:
        A freshly constructed :class:`Game`, not yet dealt.
    """
    players = []
    for seat in Position:
        if seat is HUMAN_SEAT and not autoplay:
            players.append(HumanPlayer("You", position=seat))
        else:
            players.append(AiPlayer(seat.value, position=seat))
    return Game(players, rules=rules)


def _remember(setup: TableSetup, options: DebugOptions) -> None:
    """Persist the setup a player left the landing screen with.

    Never under ``--autoplay``: an unattended run must not rewrite what a
    player chose. An unwritable home is not fatal either — the cache is a
    convenience, and a game that refused to start because it could not
    write one would be the worse trade.

    Args:
        setup: The setup to remember.
        options: The run's debug flags, read for ``autoplay``.
    """
    if options.autoplay:
        return
    try:
        save_setup(setup_path(), setup)
    except OSError:
        logger.debug("could not remember the table setup", exc_info=True)


def _attach_recorder(
    view: RichView, record: RecordRequest, setup: TableSetup
) -> RichView | RecordingView:
    """Wrap ``view`` in a :class:`RecordingView` when this run records.

    Re-evaluated after every landing screen rather than once at startup:
    the record knob lives on the table setup, so toggling it with ``[r]``
    takes effect on the next deal instead of the next run.

    Args:
        view: The real view, always the one the wrapper decorates — never
            a wrapper from a previous game.
        record: What the CLI flags asked for.
        setup: The setup the next game will be dealt under.

    Returns:
        A :class:`RecordingView` around ``view``, or ``view`` itself.
    """
    root = record.resolve(setup.aids)
    if root is None:
        return view
    return RecordingView(view, root, preset=setup.origin)


def _record_paths(paths: list[Path]) -> list[Path]:
    """Expand the ``verify`` arguments into record files.

    A directory stands for the records in it — its ``games`` subdirectory
    included, so pointing at a records root does the obvious thing. This
    also means the command is usable from a shell that does not expand
    globs, which on Windows is every shell.

    Args:
        paths: The paths given on the command line.

    Returns:
        The record files to verify, deduplicated and in a stable order.
    """

    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.glob("*.jsonl")))
            found.extend(sorted((path / "games").glob("*.jsonl")))
        else:
            found.append(path)
    seen: set[Path] = set()
    unique = []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _verdict_lines(verdict: GameVerdict) -> list[str]:
    """The stdout table for one record's verdict.

    Args:
        verdict: The record's verdict.

    Returns:
        A headline, any notes, and one line per round that is not
        ``verified`` — a clean record is one line, which is what makes a
        corpus run readable.
    """

    counts = verdict.counts
    tally = ", ".join(
        f"{counts[member]} {member}"
        for member in Verdict
        if counts[member]
    )
    lines = [
        f"{verdict.game_id}  {verdict.verdict}  "
        f"[{verdict.preset}]  {len(verdict.rounds)} rounds: {tally}"
    ]
    lines.extend(f"    note: {note}" for note in verdict.notes)
    for round_ in verdict.rounds:
        if round_.verdict is Verdict.VERIFIED:
            continue
        if round_.mismatches:
            for mismatch in round_.mismatches:
                where = ", ".join(
                    f"{name} {value}"
                    for name, value in (
                        ("seat", mismatch.position),
                        ("trick", mismatch.trick),
                        ("bid", mismatch.seq),
                    )
                    if value is not None
                )
                detail = f"{mismatch.kind}: {mismatch.detail}"
                if where:
                    detail = f"{detail} ({where})"
                lines.append(f"    round {round_.number}  {detail}")
                if mismatch.expected is not None:
                    lines.append(
                        f"        engine: {mismatch.expected}"
                    )
                    lines.append(f"        record: {mismatch.observed}")
        else:
            why = (
                "not replayable"
                if not round_.replayed
                else f"nothing to check against: {', '.join(round_.unchecked)}"
            )
            lines.append(f"    round {round_.number}  {round_.verdict} — {why}")
    return lines


def _run_verify(args: argparse.Namespace) -> int:
    """Verify every record the ``verify`` arguments name.

    Args:
        args: The parsed ``verify`` arguments.

    Returns:
        The process exit code: ``0`` when every round is ``verified`` or
        ``partial``, ``1`` when any round is ``suspect`` or any record
        could not be read at all.
    """

    paths = _record_paths(args.paths)
    if not paths:
        print("no records found", file=sys.stderr)
        return 1

    failed = False
    payloads: list[dict] = []
    for path in paths:
        try:
            verdict = verify_record(
                path,
                out=None
                if args.no_write
                else (args.out or default_out_root(path)),
            )
        except (OSError, RecordError) as exc:
            # A record that cannot be read has no verdict — there is
            # nothing to verify. It is still a failure of the run.
            failed = True
            print(f"{path}: cannot be read: {exc}", file=sys.stderr)
            continue
        if verdict.verdict is Verdict.SUSPECT:
            failed = True
        if args.json:
            payloads.append(verdict.as_json())
        else:
            for line in _verdict_lines(verdict):
                print(line)
    if args.json:
        print(json.dumps(payloads, indent=2, sort_keys=True))
    return 1 if failed else 0


def _catalog_root(path: Path | None) -> Path:
    """The records root a ``catalog`` argument names.

    Args:
        path: The ``ROOT`` argument, or ``None`` when it was left out.

    Returns:
        ``$CONTRAI_HOME/records`` for no argument — where the engine
        records — the parent of a ``games`` directory, since pointing at
        the records themselves is the obvious mistake to forgive, and the
        path itself otherwise.
    """

    if path is None:
        return records_root()
    if path.name == "games":
        return path.parent
    return path


def _tally_text(counts: Mapping[str, int]) -> str:
    """``{"partial": 130, "suspect": 5}`` as ``partial 130, suspect 5``.

    Args:
        counts: A token-to-count mapping.

    Returns:
        The counts in token order, or ``none`` when there are none.
    """

    if not counts:
        return "none"
    return ", ".join(f"{key} {counts[key]}" for key in sorted(counts))


def _catalog_lines(summary: CatalogSummary) -> list[str]:
    """The stdout report for a freshly built catalog.

    Args:
        summary: What the build produced.

    Returns:
        One line per figure, a pointer at ``contrai verify`` when some
        games have no fresh verdict, and one line per skipped file.
    """

    statuses = summary.verdict_statuses
    lines = [
        f"catalog: {summary.path}  (built {summary.built_at})",
        f"games: {summary.game_count} ({_tally_text(summary.games_by_source)})",
        f"rounds: {summary.round_count}, complete "
        f"{summary.complete_round_count}, clean {summary.clean_round_count}",
        f"game verdicts: {_tally_text(summary.game_verdicts)}",
        f"verdict status: {_tally_text(statuses)}",
        f"round verdicts: {_tally_text(summary.round_verdicts)}",
        f"players: {summary.player_count}",
    ]
    unverified = statuses.get("missing", 0) + statuses.get("stale", 0)
    if unverified:
        # A game without a fresh verdict contributes no clean round, which
        # reads as a quiet shortfall unless the report says why.
        lines.append(
            f"    without a fresh verdict: {unverified} — run: "
            f"contrai verify {summary.path.parent}"
        )
    if summary.skipped:
        lines.append(f"skipped: {len(summary.skipped)}")
        lines.extend(
            f"    {item.kind} {item.path} — {item.reason}"
            for item in summary.skipped
        )
    return lines


def _player_lines(report: PlayerReport) -> list[str]:
    """The stdout report for one player's games.

    Args:
        report: What the catalog holds for the player.

    Returns:
        A header naming the catalog's build time, then one line per game.
    """

    # A name can sit in several seats of one game — every engine AI seat
    # is ``ai:<level>`` — so games and seats are counted apart.
    games = len({game.game_id for game in report.games})
    seats = "" if games == len(report.games) else f", seats: {len(report.games)}"
    lines = [
        f"{report.player} — games: {games}{seats} "
        f"(catalog built {report.built_at})"
    ]
    for game in report.games:
        outcome = game.result or game.end_reason or "unfinished"
        ns = "?" if game.total_ns is None else game.total_ns
        ew = "?" if game.total_ew is None else game.total_ew
        if game.verdict_status == "missing":
            verdict = "no verdict"
        elif game.verdict_status == "unreadable":
            verdict = "unreadable verdict"
        elif game.verdict_status == "stale":
            verdict = f"{game.verdict} (stale)"
        else:
            verdict = game.verdict
        lines.append(
            f"{game.created_at[:10]}  {game.game_id}  {game.player_id or '-'}  "
            f"{game.name}  seat {game.position}  partner {game.partner or '-'}  "
            f"{outcome}  NS {ns} – EW {ew}  {verdict}"
        )
    return lines


def _run_catalog(args: argparse.Namespace) -> int:
    """Rebuild a records root's catalog, or read one player back from it.

    Args:
        args: The parsed ``catalog`` arguments.

    Returns:
        ``0`` on success; ``1`` when there is nothing to index, when the
        catalog cannot be replaced, or when ``--player`` finds no catalog
        or no game.
    """

    root = _catalog_root(args.root)
    if args.player is not None:
        # Read-only by design: a lookup must not cost a rebuild, and must
        # not quietly create an empty catalog where none was built.
        try:
            report = player_games(root, args.player)
        except FileNotFoundError:
            print(
                f"no catalog under {root} — build it first: contrai catalog {root}",
                file=sys.stderr,
            )
            return 1
        except CatalogError as exc:
            print(f"{exc}", file=sys.stderr)
            return 1
        if not report.games:
            print(
                f"{args.player}: no game in the catalog built {report.built_at}",
                file=sys.stderr,
            )
            return 1
        for line in _player_lines(report):
            print(line)
        return 0

    if not games_dir(root).is_dir():
        print(f"{root}: no games/ directory, nothing to index", file=sys.stderr)
        return 1
    try:
        summary = build_catalog(root)
    except PermissionError as exc:
        # Windows refuses to replace a file another process holds open —
        # a sqlite3 shell or a notebook still connected to the catalog.
        print(
            f"{catalog_path(root)} cannot be replaced ({exc}). Close the "
            "program holding it open and run again; the old catalog is "
            "unchanged.",
            file=sys.stderr,
        )
        return 1
    for line in _catalog_lines(summary):
        print(line)
    return 0


def _replay_quietly(controller: ReplayController, index: int) -> None:
    """Replay the rounds before ``controller.rounds[index]``, unseen.

    The scripted deal source indexes on the game's own round counter and
    cannot seek, so reaching a round means playing every round before it.
    The controller's view must already be quiet.

    Args:
        controller: The controller, fresh.
        index: The position, in ``controller.rounds``, of the round the
            viewer asked for.
    """

    for earlier in controller.rounds[:index]:
        try:
            controller.replay_round(earlier)
        except (IllegalBidError, IllegalPlayError, ReplayError):
            # A suspect earlier round is the verifier's business, not
            # this screen's: clear the hands it left behind and carry on
            # to the round the viewer asked for.
            controller.clear_hands()


def _show_round_grid(record: "GameRecord", number: int, view: Any) -> None:
    """Replay one recorded round unseen, then show all its tricks at once.

    Args:
        record: The record being replayed.
        number: The record's number for the round to show.
        view: The view to render through.
    """

    # Quiet from start to finish: nothing but the grid reaches the screen.
    stepper = SteppingView(view)
    controller = ReplayController(record, view=stepper)
    index = [r.number for r in controller.rounds].index(number)
    stepper.attach(controller.game, controller.game.rules.target_score)
    _replay_quietly(controller, index)
    try:
        controller.replay_round(controller.rounds[index])
    except (IllegalBidError, IllegalPlayError, ReplayError) as exc:
        view.show_replay_notice(
            f"Round {number} diverges from the record: {exc}"
        )
        # ``g`` still answers here: the grid of the round as far as it
        # got is the quickest look at where it went wrong.
        read_step_key(
            view,
            controller.game.current_round,
            stepper.bids,
            can_go_back=False,
        )
        return
    round_ = controller.game.current_round
    view.show_replay_grid(
        round_, round_.auction.bids if round_.auction is not None else []
    )


def _step_round(record: "GameRecord", number: int, view: Any) -> None:
    """Step one recorded round, restarting it whenever the viewer goes back.

    Every attempt builds a fresh :class:`ReplayController` and replays the
    rounds before this one in silence, because
    :class:`~contrai_engine.replay.deal.ScriptedDealSource` indexes on the
    game's own round counter and cannot seek. That is also how stepping
    back works: the engine keeps no earlier state, so the round is simply
    played again with more of its stops passed over. A round replays in
    milliseconds, which is what makes both affordable.

    Args:
        record: The record being replayed.
        number: The record's number for the round to step.
        view: The view to render through.
    """

    resume_at = 0
    while True:
        stepper = SteppingView(view, resume_at=resume_at)
        # Explained only when the view has a rationale panel to fill —
        # which ``_run_replay`` decides once, from the record's seats.
        controller = ReplayController(
            record,
            view=stepper,
            explain=getattr(view, "rationale", None) is not None,
        )
        index = [r.number for r in controller.rounds].index(number)
        stepper.attach(controller.game, controller.game.rules.target_score)
        try:
            _replay_quietly(controller, index)
            stepper.quiet = False
            controller.replay_round(controller.rounds[index])
        except ReplayInterrupt as interrupt:
            if interrupt.resume_at is None:
                return
            resume_at = interrupt.resume_at
            continue
        except (IllegalBidError, IllegalPlayError, ReplayError) as exc:
            view.show_replay_notice(
                f"Round {number} diverges from the record: {exc}"
            )
            read_step_key(
                view,
                controller.game.current_round,
                stepper.bids,
                can_go_back=False,
            )
            return
        round_ = controller.game.current_round
        view.show_round_recap(round_, controller.game.scores)
        key = read_step_key(
            view, round_, stepper.bids, can_go_back=stepper.stops > 0
        )
        if key == "p":
            resume_at = max(stepper.stops - 1, 0)
            continue
        return


def _run_replay(args: argparse.Namespace) -> int:
    """Step through a recorded game in the terminal.

    Args:
        args: The parsed ``replay`` namespace.

    Returns:
        ``0`` normally, ``1`` when the record cannot be read or names no
        such replayable round.
    """

    try:
        record = load_game(args.path)
    except (OSError, RecordError) as exc:
        print(f"{args.path}: cannot be read: {exc}", file=sys.stderr)
        return 1
    # Verified live rather than read back from ``verdicts/``: a replay is
    # fast, and a verdict file beside a record may describe an older one.
    rows = replay_rows(record, verify_game(record))
    steppable = [row.number for row in rows if row.steppable]
    if args.round is not None and args.round not in steppable:
        print(
            f"{args.path}: round {args.round} is not there, or cannot be "
            "replayed",
            file=sys.stderr,
        )
        return 1
    view = RichView(options=DebugOptions(replay=True))
    # The AI's reasons, compact to start with, when the record seats an AI
    # this engine can ask; a game of humans or observed players shows no
    # panel at all and offers no ``[w]``.
    if ReplayController.explainable(record):
        view.rationale = "compact"
    pick = RoundPick(args.round) if args.round is not None else None
    try:
        while True:
            if pick is None:
                pick = view.show_replay_summary(rows, record.header.game_id)
                if pick is None:
                    return 0
            if pick.grid:
                _show_round_grid(record, pick.number, view)
            else:
                _step_round(record, pick.number, view)
            pick = None
    except (KeyboardInterrupt, EOFError):
        view.console.print("\nGoodbye.")
        return 0


def _force_utf8_streams() -> None:
    """Switch stdout and stderr to UTF-8, where the streams allow it.

    Both subcommands need this, which is why it runs before the dispatch
    rather than inside the game path. A card renders with its suit glyph
    (``K♠``), so cp1252 — still the default code page on a legacy Windows
    console — raises ``UnicodeEncodeError`` on encode rather than
    degrading: a game would crash on its first trick, and ``verify``
    would crash reporting a belote mismatch, which names the two cards.

    A stream that cannot be reconfigured is left alone: some are already
    UTF-8, some are pipes, and none of that is worth failing a run over.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def main() -> None:
    """Entry point registered as the ``contrai`` console script.

    Dispatches on the subcommand: ``verify``, ``replay`` and ``catalog``
    each report and exit with their own code, everything else is a game.

    Raises:
        SystemExit: With the ``verify``, ``replay`` or ``catalog`` exit
            code, or on a usage error.
    """
    _force_utf8_streams()
    args, play = _parse_argv()
    if args.command == "verify":
        raise SystemExit(_run_verify(args))
    if args.command == "replay":
        raise SystemExit(_run_replay(args))
    if args.command == "catalog":
        raise SystemExit(_run_catalog(args))
    options, setup, record = _play_setup(args, play)
    options = _apply_seed(options)
    configure_logging(options)

    view = RichView(options=options, aids=setup.aids)
    setup = view.show_landing(setup)
    view.aids = setup.aids
    _remember(setup, options)
    # The recorder decorates the view, and ``main`` holds the wrapper: the
    # two hooks the CLI issues itself — ``on_round_complete`` and
    # ``show_end_game`` — are recorded for the same reason the model's are.
    driver = _attach_recorder(view, record, setup)
    try:
        while True:
            game = _build_game(autoplay=options.autoplay, rules=setup.rules)
            driver.attach(game, target_score=game.rules.target_score)
            while not game.check_game_over().game_over:
                game.manage_round(view=driver)
                driver.on_round_complete(game.current_round, game.scores)
                # Show a between-round recap (contract, made/failed,
                # round points, running totals). Always shown, including
                # before the end-game banner so the player can read the
                # final round's breakdown before the scoreboard takes
                # over — the prompt adapts to the final-round and
                # sudden-death (tie at/above target) cases, and the panel
                # names any side the §8 belote gate is holding back.
                status = game.check_game_over()
                driver.show_round_recap(
                    game.current_round,
                    game.scores,
                    is_final=status.game_over,
                    is_tiebreaker=status.tied_teams is not None,
                    belote_gated=status.belote_gated,
                )
            choice = driver.show_end_game(game.check_game_over())
            if choice == "q":
                break
            if choice == "n":
                finish_recording(driver)
                # The landing screen goes to the real view, not the
                # driver: it is not part of a game and has no place in a
                # record.
                setup = view.show_landing(setup)
                view.aids = setup.aids
                _remember(setup, options)
                driver = _attach_recorder(view, record, setup)
            # 'r' → rematch: same setup, fresh game in the next loop tick.
    except (KeyboardInterrupt, EOFError):
        view.console.print("\nGoodbye.")
    finally:
        # A game abandoned mid-round still has a file open. Closing it
        # with ``interrupted`` is what keeps "why does this record stop?"
        # a question the record itself answers.
        finish_recording(driver)


if __name__ == "__main__":
    main()
