"""Cross-screen layout helpers for the Rich terminal UI.

Building blocks shared by every screen: the two-column grid that places
panels side by side, the Prompt panel (question + optional rejection
notice), and the rolling event-log panel. Pure builders — they take the
data they render as explicit parameters; ``RichView`` owns the state and
does the printing.
"""

from __future__ import annotations

from typing import Optional

from rich.box import ROUNDED
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contrai_engine.view.theme import (
    BORDER,
    BORDER_DIM,
    DIM,
    TITLE,
    YELLOW,
)


def _two_column(left, right, *, left_width: int) -> Table:
    """Place two panels side-by-side with a fixed-width left column.

    A ``Table.grid`` keeps the row exactly as tall as the panels (unlike
    ``rich.layout.Layout``, which expands to fill the console height).
    """
    grid = Table.grid(expand=False, padding=(0, 1))
    grid.add_column(width=left_width, no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_row(left, right)
    return grid


def _panel_prompt(
    question: Text,
    mandatory: bool,
    notice: Optional[Text] = None,
) -> Panel:
    body = Text()
    # A rejection from the previous input sits above the question, in
    # red, so the player reads *why* the last entry bounced without it
    # ever leaving the frame. The panel grows one row to fit it.
    if notice is not None:
        body.append_text(notice)
        body.append("\n")
    if mandatory:
        q = question.copy()
        q.stylize(f"bold {YELLOW}")
        body.append_text(q)
    else:
        body.append_text(question)
    body.append("\n")
    return Panel(
        body,
        title=Text("Prompt", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=70,
        height=5 if notice is not None else 4,
    )


def _panel_event_log(event_log: list[Text], log_max: int) -> Panel:
    """Bottom panel showing the last ``log_max`` events."""
    body = Text()
    if not event_log:
        body.append("(no events yet)", style=DIM)
    else:
        for i, line in enumerate(event_log):
            if i > 0:
                body.append("\n")
            body.append_text(line)
    return Panel(
        body,
        title=Text("Log", style=f"bold {TITLE}"),
        border_style=BORDER_DIM,
        box=ROUNDED,
        width=70,
        height=log_max + 2,
    )
