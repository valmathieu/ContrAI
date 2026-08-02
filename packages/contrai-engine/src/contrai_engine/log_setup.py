"""Stdlib-logging setup for the engine's debug mode.

Logging is infrastructure, not presentation: model and view code only
ever emits through the standard ``logging`` module (``logging.getLogger(
__name__)`` calls scattered across ``contrai_engine``/``contrai_core``),
and never configures a handler itself. :func:`configure_logging` is the
one place that wires those emissions to a file, and it is meant to be
called exactly once, by the CLI, after parsing :class:`DebugOptions`.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from .options import DebugOptions

_LOG_FILENAME = "contrai-debug.log"
_PACKAGE_LOGGER_NAMES = ("contrai_engine", "contrai_core")
_HANDLER_NAME = "contrai-debug-file-handler"
_FORMATTER = logging.Formatter("%(levelname)-7s %(name)s: %(message)s")


def configure_logging(options: DebugOptions) -> None:
    """Attach a DEBUG-level file handler to the package-root loggers.

    A no-op unless ``options.debug`` is set. When active, one
    ``logging.FileHandler`` writing ``contrai-debug.log`` (overwritten
    per run, UTF-8) is attached to both the ``contrai_engine`` and
    ``contrai_core`` package-root loggers — never the global root
    logger, so third-party library logs are unaffected. The formatter
    deliberately omits per-line timestamps so two runs with the same
    seed produce diff-identical logs; the run's timestamp is instead
    recorded once, in an INFO line emitted here at startup.

    Idempotent: calling this twice does not stack a second handler.

    Args:
        options: The parsed debug-mode flags for this run.
    """

    if not options.debug:
        return

    loggers = [logging.getLogger(name) for name in _PACKAGE_LOGGER_NAMES]

    # The package loggers are only ever attached and detached together —
    # here, and in the tests' teardown fixture — so finding the handler
    # on any one of them means the whole set is configured. Deliberately
    # not a per-logger re-attach: that would build a second FileHandler
    # in ``mode="w"`` and truncate the log this run is already writing.
    already_configured = any(
        handler.get_name() == _HANDLER_NAME
        for logger in loggers
        for handler in logger.handlers
    )
    if already_configured:
        return

    try:
        handler = logging.FileHandler(_LOG_FILENAME, mode="w", encoding="utf-8")
    except OSError as error:
        # Debug logging is a diagnostic nicety, not a game requirement — a
        # locked or unwritable path must never stop the game from starting.
        print(
            f"warning: could not open {_LOG_FILENAME!r} for debug logging: {error}",
            file=sys.stderr,
        )
        return

    handler.set_name(_HANDLER_NAME)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_FORMATTER)

    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logging.getLogger("contrai_engine").info(
        "run started %s, flags debug=%s autoplay=%s, seed %s",
        timestamp,
        options.debug,
        options.autoplay,
        options.seed,
    )
