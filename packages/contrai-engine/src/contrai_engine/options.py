"""Debug-mode options shared between the CLI and the view.

``DebugOptions`` is the single value object the three orthogonal debug
flags (``--debug``, ``--seed``, ``--autoplay``) get parsed into. It is
stdlib-only and importable from both the CLI (which parses the flags)
and the view (which reads them to decide, e.g., whether to show
face-up hands) — but never from the model layer, which stays unaware
that a debug mode exists at all.
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
