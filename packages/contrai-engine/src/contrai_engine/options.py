"""Runtime options shared between the CLI and the view.

``DebugOptions`` is the single value object the three orthogonal debug
flags (``--debug``, ``--seed``, ``--autoplay``) get parsed into.
``TableAids`` is its §9.7 neighbour: the interface aids a table can
switch on or off. Both are stdlib-only and importable from the CLI
(which parses the flags) and the view (which reads them to decide, e.g.,
whether to show face-up hands or the running round score) — but never
from the model layer, which stays unaware that either exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DebugOptions:
    """Parsed state of the engine's debug-mode CLI flags.

    All fields default to "off", so ``DebugOptions()`` is the back-compat
    anchor: constructing one with no arguments reproduces today's
    runtime behavior exactly.

    Attributes:
        debug: Whether debug mode is active — face-up hands, extra
            diagnostics, and DEBUG-level file logging.
        autoplay: Whether the game runs unattended with four AI seats.
        seed: The seed passed to ``random.seed`` at startup, or
            ``None`` when none was requested (in which case debug mode
            generates and records one).
    """

    debug: bool = False
    autoplay: bool = False
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class TableAids:
    """Interface aids the table can switch on or off (§9.7).

    Not table *rules*: an aid changes what the screen shows, never what
    the cards do, so a round played with the running score hidden scores
    exactly as one played with it visible. That is why these live beside
    :class:`DebugOptions` rather than on ``RuleConfig`` — the CLI and the
    view read them, the model never does.

    All fields default to the catalogue's "on", so ``TableAids()``
    reproduces today's runtime behavior exactly.

    Attributes:
        live_round_score: Whether the in-game Round panel shows each
            side's running card points as the tricks are collected.
    """

    live_round_score: bool = True
