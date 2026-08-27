"""Table-setup screen rendering for the Rich terminal UI.

The landing screen's setup half: the resolved-table summary shown before
the first deal, the preset radio, and the prompt lines the input loops in
:class:`~contrai_engine.view.rich_view.RichView` print beneath them. Pure
builders consuming a :class:`~contrai_engine.ruleset.TableSetup` or the
scalars derived from one — the loops own the state and do the printing.

The summary deliberately names four things and stops: where the ruleset
came from and how far it sits from the §9 defaults, the target score, the
trump choices, and the live round score. Everything else is one keystroke
away in the per-knob editor, and a wall of 22 rows before the first deal
would bury the three a player actually changes.
"""

from __future__ import annotations

from typing import Sequence

from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text

from contrai_core import RuleConfig

from contrai_engine.ruleset import (
    SECTION_HEADINGS,
    SECTIONS,
    TableSetup,
    knob_value,
    non_default_knobs,
)
from contrai_engine.view.theme import (
    BORDER,
    DIM,
    FG,
    GOLD,
    GOLD_BG,
    GOLD_FG,
    GREEN_FG,
    TARGET_OPTIONS,
    TITLE,
)

WIDTH = 70
"""The landing screen's fixed layout width, shared with ``screens/landing``."""

LABEL_WIDTH = 18
"""Column the summary's values start at, so the four rows line up."""

VALUE_WIDTH = 13
"""Right-aligned value column in the knob grid — ``"anticlockwise"``, the
longest value any knob takes, fits it exactly."""

NAME_WIDTH = max(len(name) for fields in SECTIONS.values() for name in fields)
"""Knob-name column, sized to the longest name in the *whole* catalogue
rather than per section — so the values stay in one column as ``[n]``
walks from a two-row subsection to a ten-row one."""


def _target_annotation(value: int) -> tuple[str, str]:
    """The label and time estimate :data:`TARGET_OPTIONS` gives a target.

    Args:
        value: A target score, normally one of ``TARGET_SCORES``.

    Returns:
        ``(label, estimate)`` — two empty strings for a value the table
        does not describe, so the caller renders the bare number rather
        than raising over presentation.
    """
    for option, label, estimate in TARGET_OPTIONS:
        if option == value:
            return label, estimate
    return "", ""


def _panel_table_setup(setup: TableSetup) -> Panel:
    """Four-row summary of the table the next deal will be played at.

    Args:
        setup: The setup being edited.

    Returns:
        The Table setup ``Panel``.
    """
    rules = setup.rules
    body = Text()

    # Where the rules came from, and how far they have been pushed. The
    # count is the honest headline: "classic" alone would be a lie once a
    # knob has been turned, and listing all 22 rows would not be a summary.
    changed = non_default_knobs(rules)
    body.append(f"{'Ruleset':<{LABEL_WIDTH}}", style=DIM)
    body.append(setup.origin, style=f"bold {GOLD}")
    if changed:
        plural = "" if len(changed) == 1 else "s"
        body.append(
            f"   (+{len(changed)} change{plural} from classic)", style=DIM
        )
    body.append("\n")

    body.append(f"{'Target score':<{LABEL_WIDTH}}", style=DIM)
    body.append(str(rules.target_score), style=f"bold {FG}")
    label, estimate = _target_annotation(rules.target_score)
    if label:
        body.append(f"   {label}  ·  {estimate}", style=DIM)
    body.append("\n")

    body.append(f"{'Trump choices':<{LABEL_WIDTH}}", style=DIM)
    body.append(
        "suits · no trump · all trump"
        if rules.extended_trump_choices
        else "suits only",
        style=FG,
    )
    body.append("\n")

    body.append(f"{'Live round score':<{LABEL_WIDTH}}", style=DIM)
    live = setup.aids.live_round_score
    body.append("on" if live else "off", style=f"bold {GREEN_FG}" if live else DIM)

    return Panel(
        body,
        title=Text("Table setup", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=WIDTH,
    )


def _panel_preset_list(names: Sequence[str], selected: str) -> Panel:
    """One numbered radio row per offered ruleset, highlighting ``selected``.

    Args:
        names: The rulesets on offer, in the order they are numbered.
        selected: The origin of the setup currently in play. A value not
            in ``names`` — a loaded file, or an edited "custom" table —
            simply leaves every row unfilled, which is the truth: none of
            the offers is what the table is playing.

    Returns:
        The Presets ``Panel``.
    """
    rows = Text()
    rows.append("Ruleset", style=f"bold {FG}")
    rows.append("   (a named set of the 22 table rules)\n\n", style=DIM)
    for index, name in enumerate(names, start=1):
        line = Text()
        if name == selected:
            line.append(f" (●) {index}.  ", style=f"bold {GOLD_FG} on {GOLD_BG}")
            line.append(name, style=f"bold {GOLD_FG} on {GOLD_BG}")
            # Pad to fill the panel width with the gold background.
            line.append(
                " " * max(0, 60 - line.cell_len), style=f"on {GOLD_BG}"
            )
        else:
            line.append(" ( ) ", style=DIM)
            line.append(f"{index}.  ", style=DIM)
            line.append(name, style=FG)
        rows.append_text(line)
        rows.append("\n")
    return Panel(
        rows,
        title=Text("Presets", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=WIDTH,
    )


def _panel_knobs(rules: RuleConfig, section: str) -> Panel:
    """One §9 subsection's knobs, numbered, with their current values.

    Knobs are listed under their ``RuleConfig`` / TOML field name rather
    than a prose paraphrase: that is the name the catalogue, the config
    file and the docs all use, so what a player reads here is what they
    would write in a file or grep for later. A value that differs from
    the §9 default is picked out in gold, which turns the grid into its
    own diff against ``classic``.

    Args:
        rules: The ruleset being edited.
        section: A key of :data:`~contrai_engine.ruleset.SECTIONS`.

    Returns:
        The Table rules ``Panel``.

    Raises:
        KeyError: If ``section`` is not a catalogue section.
    """
    fields = SECTIONS[section]
    order = list(SECTIONS)
    baseline = RuleConfig()
    body = Text()
    body.append(SECTION_HEADINGS[section], style=f"bold {FG}")
    body.append(
        f"   section {order.index(section) + 1} of {len(order)}\n\n", style=DIM
    )
    for index, name in enumerate(fields, start=1):
        changed = getattr(rules, name) != getattr(baseline, name)
        body.append(f" {index:>2}. ", style=DIM)
        body.append(name.ljust(NAME_WIDTH), style=FG)
        body.append("   ")
        body.append(
            knob_value(rules, name).rjust(VALUE_WIDTH),
            style=f"bold {GOLD}" if changed else FG,
        )
        body.append("\n")
    return Panel(
        body,
        title=Text("Table rules", style=f"bold {TITLE}"),
        border_style=BORDER,
        box=ROUNDED,
        width=WIDTH,
    )


def _knobs_prompt_text(count: int) -> Text:
    """Prompt for the knob editor.

    Args:
        count: How many knobs the section on screen offers.

    Returns:
        The prompt ``Text``.
    """
    t = Text()
    t.append(f"[1-{count}]", style=f"bold {FG}")
    t.append(" cycle a knob  ·  ", style=FG)
    t.append("[n]", style=f"bold {FG}")
    t.append(" next section  ·  ", style=FG)
    t.append("[b]", style=f"bold {FG}")
    t.append(" back  ·  ", style=FG)
    t.append("[Enter]", style=f"bold {GOLD}")
    t.append(" done", style=FG)
    return t


def _setup_prompt_text(setup: TableSetup) -> Text:
    """The landing dispatcher's key list.

    ``[l]`` names the state it would move *to* rather than the setting it
    acts on, so the line reads as an action and never as a claim about
    what is currently on — the summary panel above already says that.

    Args:
        setup: The setup being edited, read for the live-score wording.

    Returns:
        The prompt ``Text``.
    """
    t = Text()
    t.append("[Enter]", style=f"bold {GOLD}")
    t.append(" deal  ·  ", style=FG)
    t.append("[p]", style=f"bold {FG}")
    t.append(" preset  ·  ", style=FG)
    t.append("[f]", style=f"bold {FG}")
    t.append(" load file  ·  ", style=FG)
    t.append("[k]", style=f"bold {FG}")
    t.append(" knobs  ·  ", style=FG)
    t.append("[l]", style=f"bold {FG}")
    t.append(
        " live score " + ("off" if setup.aids.live_round_score else "on"),
        style=FG,
    )
    return t


def _preset_prompt_text(names: Sequence[str]) -> Text:
    """Prompt for the preset picker, offering the numbers and the names.

    Args:
        names: The rulesets on offer, in the order the panel numbered them.

    Returns:
        The prompt ``Text``.
    """
    t = Text()
    t.append(f"Ruleset? [1-{len(names)}] or a name (", style=FG)
    t.append(", ".join(names), style=DIM)
    t.append(")  ·  ", style=FG)
    t.append("[Enter]", style=f"bold {GOLD}")
    t.append(" keeps the current one", style=FG)
    return t


def _file_prompt_text() -> Text:
    """Prompt for the file loader.

    Returns:
        The prompt ``Text``.
    """
    t = Text()
    t.append("Path to a setup file (TOML)  ·  ", style=FG)
    t.append("[Enter]", style=f"bold {GOLD}")
    t.append(" cancels", style=FG)
    return t
