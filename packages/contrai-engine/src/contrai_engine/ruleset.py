"""Engine-side setup loading: TOML → :class:`TableSetup`, and back.

Lives in the engine because core stays I/O-free. A *setup* document is the
six §9 rule sections plus an optional seventh, ``[table_aids]``, holding
the §9.7 interface aids — one file therefore describes both what the table
plays and what its screen shows. Unknown sections, unknown keys, wrong
types and unknown enum tokens are **errors**, not warnings: a typo'd knob
that silently kept its default would corrupt a logged experiment. Because
the extra section is optional on the way in, a hand-written six-section
``--rules`` file stays exactly valid and a saved setup can be replayed
through that same flag; ``dump_ruleset`` still writes six sections on its
own, ``dump_setup`` writes all seven.

Beside the document format this module owns the §9 catalogue *as data*:
:data:`SECTIONS` is the TOML layout, :data:`KNOB_LABELS` the subsection
heading each knob renders under, and :func:`cycle_knob` /
:func:`non_default_knobs` are the two operations a per-knob editor needs.
Keeping them here is what stops the editor, the file format and the
catalogue from drifting apart.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, get_type_hints

from contrai_core import PRESETS, TARGET_SCORES, RuleConfig

from contrai_engine.options import TableAids


class RulesetError(ValueError):
    """A ruleset document or selection is malformed.

    Bad TOML, an unknown section or key, a value of the wrong type, an
    unknown enum token, an unknown preset name, an unknown knob name, or
    both a file and a preset given at once.
    """


#: TOML section → RuleConfig field names, in catalogue (§9 / spec §3.1) order.
SECTIONS: dict[str, tuple[str, ...]] = {
    "general": ("target_score", "turn_direction"),
    "trump": ("extended_trump_choices", "all_trump_belote"),
    "deal": ("reshuffle_every_round",),
    "bidding": ("solo_slam_available", "slam_can_be_doubled", "solo_slam_can_be_doubled"),
    "card_play": ("under_trump_exemption", "solo_slam_gives_the_lead",
                  "belote_counts_toward_contract", "belote_lost_when_contract_fails"),
    "scoring": ("mark_made_points", "mark_announced_points",
                "only_announced_points_multiplied", "any_failure_marks_160",
                "unannounced_slam_substitute", "failed_slam_marks_made_points",
                "failed_slam_marks_announced_points", "attack_must_outscore_defense",
                "rounding", "win_on_belote_points_alone"),
}

#: The optional seventh section: the §9.7 interface aids. Held apart from
#: :data:`SECTIONS` on purpose — an aid changes what the screen shows,
#: never what the cards do, so it is not a rule and has no ``RuleConfig``
#: field to land on.
AID_SECTION: tuple[str, tuple[str, ...]] = ("table_aids", ("live_round_score",))

#: TOML section → the §9 subsection heading it renders under on screen.
SECTION_HEADINGS: dict[str, str] = {
    "general": "General (§9.1)",
    "trump": "Trump variants (§9.2)",
    "deal": "Deal (§9.3)",
    "bidding": "Bidding (§9.4)",
    "card_play": "Card play (§9.5)",
    "scoring": "Scoring (§9.6)",
}

#: ``RuleConfig`` field → its §9 subsection heading. Derived from
#: :data:`SECTIONS`, so a knob that moves section in the TOML layout moves
#: heading here too — there is no second list to keep in step.
KNOB_LABELS: dict[str, str] = {
    name: SECTION_HEADINGS[section]
    for section, fields in SECTIONS.items()
    for name in fields
}

# Resolved once. get_type_hints (not Field.type) so string annotations can
# never sneak in as the compared type.
_FIELD_TYPES: dict[str, type] = get_type_hints(RuleConfig)
_AID_TYPES: dict[str, type] = get_type_hints(TableAids)


@dataclass(frozen=True, slots=True)
class TableSetup:
    """Everything chosen before the first deal, as one value.

    The table's rules and its interface aids travel together because the
    setup screen edits them together and one document persists them
    together — but they stay separate fields, because only ``rules`` ever
    reaches the model.

    Attributes:
        rules: The table ruleset the :class:`~contrai_engine.model.game.Game`
            is built under.
        aids: The §9.7 interface aids the view reads.
        origin: Where this setup came from, for the setup panel's label
            line — a preset name, a file name, ``"last used"``, or
            ``"custom"`` once a knob has been edited by hand.
    """

    rules: RuleConfig = RuleConfig()
    aids: TableAids = TableAids()
    origin: str = "classic"


def _coerce(section: str, key: str, value: Any, types: dict[str, type]) -> Any:
    """Check ``value`` against the field's declared type; map enum tokens.

    Args:
        section: The TOML section the key was read from, for the message.
        key: The field name being read.
        value: The raw value ``tomllib`` produced.
        types: The declaring class's resolved annotations — ``RuleConfig``'s
            for a rule section, ``TableAids``' for the aid section.

    Returns:
        The value as the field's declared type — an enum *member* for the
        three token-typed knobs, the value itself otherwise.

    Raises:
        RulesetError: If the value is of the wrong type, or is not one of
            an enum field's tokens.
    """
    expected = types[key]
    where = f"[{section}] {key}"
    if isinstance(expected, type) and issubclass(expected, Enum):
        if not isinstance(value, str):
            raise RulesetError(f"{where} must be a str token, got {type(value).__name__}")
        try:
            return expected(value)
        except ValueError:
            tokens = ", ".join(m.value for m in expected)
            raise RulesetError(
                f"{where}: unknown value {value!r} (expected one of: {tokens})"
            ) from None
    if expected is bool:
        if not isinstance(value, bool):
            raise RulesetError(f"{where} must be a bool, got {type(value).__name__}")
        return value
    if expected is int:
        # bool is an int subclass — reject it explicitly, or ``true`` would
        # quietly parse as the target score 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise RulesetError(f"{where} must be an int, got {type(value).__name__}")
        return value
    raise RulesetError(f"{where}: unsupported field type {expected!r}")


def _parse_document(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a setup document into rule kwargs and aid kwargs.

    Args:
        text: The TOML document — the six rule sections, the optional
            ``[table_aids]`` section, or any subset of either.

    Returns:
        ``(rule_kwargs, aid_kwargs)``, each ready to splat into its
        dataclass. Keys the document omits are simply absent, which is how
        a file stays a *partial override* rather than a full specification.

    Raises:
        RulesetError: On bad TOML, an unknown section or key, a wrong type
            or an unknown enum token.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesetError(f"invalid TOML: {exc}") from exc
    aid_section, aid_keys = AID_SECTION
    known = (*SECTIONS, aid_section)
    rule_kwargs: dict[str, Any] = {}
    aid_kwargs: dict[str, Any] = {}
    for section, body in data.items():
        if section not in known:
            raise RulesetError(
                f"unknown section {section!r} (expected one of: {', '.join(known)})"
            )
        if not isinstance(body, dict):
            raise RulesetError(f"{section!r} must be a [section] table, not a value")
        is_aid = section == aid_section
        allowed = aid_keys if is_aid else SECTIONS[section]
        types = _AID_TYPES if is_aid else _FIELD_TYPES
        target = aid_kwargs if is_aid else rule_kwargs
        for key, value in body.items():
            if key not in allowed:
                raise RulesetError(
                    f"unknown key {key!r} in [{section}] (expected one of: {', '.join(allowed)})"
                )
            target[key] = _coerce(section, key, value, types)
    return rule_kwargs, aid_kwargs


def parse_setup(text: str) -> TableSetup:
    """Parse a TOML setup document into a validated :class:`TableSetup`.

    Missing keys keep their §9 defaults, so a file is a *partial override*
    rather than a full specification — and a six-section document written
    before ``[table_aids]`` existed still parses, simply taking the aid
    defaults.

    Args:
        text: The TOML document.

    Returns:
        The :class:`TableSetup` the document names, with the default
        ``origin``; callers that know where the text came from replace it.

    Raises:
        RulesetError: On bad TOML, an unknown section or key, a wrong type
            or an unknown enum token.
        InvalidRuleConfigError: When the document is well-formed but names
            an impossible table — core's own validation, propagated
            unchanged (it is already a ``ValueError``).
    """
    rule_kwargs, aid_kwargs = _parse_document(text)
    return TableSetup(rules=RuleConfig(**rule_kwargs), aids=TableAids(**aid_kwargs))


def parse_ruleset(text: str) -> RuleConfig:
    """Parse a TOML document for its rules alone.

    The ruleset half of :func:`parse_setup`. A document carrying a
    ``[table_aids]`` section parses here too and its aids are dropped,
    which is what lets a saved setup be replayed through ``--rules``.

    Args:
        text: The TOML document.

    Returns:
        The :class:`RuleConfig` the document names.

    Raises:
        RulesetError: On bad TOML, an unknown section or key, a wrong type
            or an unknown enum token.
        InvalidRuleConfigError: When the document names an impossible table.
    """
    return parse_setup(text).rules


def load_setup(path: Path) -> TableSetup:
    """Read a UTF-8 TOML setup file, naming the file as the origin.

    Read as text rather than through ``tomllib.load``, which insists on a
    binary handle; TOML mandates UTF-8, so the encoding is not a guess.

    Args:
        path: Path to the setup file.

    Returns:
        The :class:`TableSetup` the file names, its ``origin`` set to the
        file's name so the setup panel can say where the rules came from.

    Raises:
        OSError: If the file cannot be read — a missing file surfaces as
            ``FileNotFoundError``, for the caller to turn into a usage
            error or a row it silently does not offer.
        RulesetError: On a malformed document (see :func:`parse_setup`).
    """
    path = Path(path)
    setup = parse_setup(path.read_text(encoding="utf-8"))
    return dataclasses.replace(setup, origin=path.name)


def load_ruleset(path: Path) -> RuleConfig:
    """Read a UTF-8 TOML file for its rules alone.

    Args:
        path: Path to the ruleset file.

    Returns:
        The :class:`RuleConfig` the file names.

    Raises:
        OSError: If the file cannot be read.
        RulesetError: On a malformed document.
    """
    return load_setup(path).rules


def save_setup(path: Path, setup: TableSetup) -> None:
    """Write ``setup`` as a seven-section UTF-8 TOML document.

    Creates the parent directory when it is missing, so a first run has
    nothing to prepare.

    Args:
        path: Destination file; overwritten if it already exists.
        setup: The setup to persist.

    Raises:
        OSError: If the directory or the file cannot be written — the
            caller decides whether that is fatal.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_setup(setup), encoding="utf-8")


def setup_path() -> Path:
    """Where the last-used setup is remembered.

    ``CONTRAI_HOME`` overrides the location — the user's escape hatch, and
    how the tests stay out of the real home directory.

    Returns:
        ``$CONTRAI_HOME/last-setup.toml``, or ``~/.contrai/last-setup.toml``
        when the variable is unset.
    """
    home = os.environ.get("CONTRAI_HOME")
    base = Path(home) if home else Path.home() / ".contrai"
    return base / "last-setup.toml"


def _format(value: Any) -> str:
    """Render one field value as its TOML literal."""
    if isinstance(value, bool):          # before int: bool is an int
        return "true" if value else "false"
    if isinstance(value, Enum):
        return f'"{value.value}"'
    return str(value)


def _display(value: Any) -> str:
    """Render one field value for a human reading a panel.

    Bools become ``on`` / ``off`` rather than ``true`` / ``false``: the
    screen is describing a table, not quoting a config file.
    """
    if isinstance(value, bool):          # before int: bool is an int
        return "on" if value else "off"
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _section_block(section: str, keys: tuple[str, ...], source: Any) -> str:
    """Render one ``[section]`` and its keys, ``=`` aligned at the longest.

    Args:
        section: The TOML section name.
        keys: The field names to write, in catalogue order.
        source: The object to read them off — a ``RuleConfig`` for a rule
            section, a ``TableAids`` for the aid section.

    Returns:
        The block, with no trailing newline.
    """
    width = max(len(key) for key in keys)
    lines = [f"[{section}]"] + [
        f"{key.ljust(width)} = {_format(getattr(source, key))}" for key in keys
    ]
    return "\n".join(lines)


def dump_ruleset(config: RuleConfig) -> str:
    """Render ``config`` in the spec's six-section layout, ``=`` aligned per section.

    The inverse of :func:`parse_ruleset` — ``parse_ruleset(dump_ruleset(c))
    == c`` for every valid ``c`` — so a resolved ruleset can be written out
    beside a simulation result or a scraped game and read back later.

    Args:
        config: The ruleset to render.

    Returns:
        A TOML document, newline-terminated.
    """
    blocks = [_section_block(section, keys, config) for section, keys in SECTIONS.items()]
    return "\n\n".join(blocks) + "\n"


def dump_setup(setup: TableSetup) -> str:
    """Render ``setup`` as the six rule sections plus ``[table_aids]``.

    The inverse of :func:`parse_setup`. Because the extra section is
    optional on the way in, the result is also a valid ``--rules`` file:
    replaying a saved setup through that flag reads its rules and ignores
    its aids rather than failing on an unknown section.

    Args:
        setup: The setup to render.

    Returns:
        A TOML document, newline-terminated.
    """
    aid_section, aid_keys = AID_SECTION
    return (
        dump_ruleset(setup.rules)
        + "\n"
        + _section_block(aid_section, aid_keys, setup.aids)
        + "\n"
    )


def cycle_knob(rules: RuleConfig, field: str) -> RuleConfig:
    """Step one knob to its next value, wrapping at the end.

    The editor's single mutation, so a screen never has to know a knob's
    type: a ``bool`` toggles, an enum advances to the next member, and
    ``target_score`` climbs :data:`~contrai_core.TARGET_SCORES`. Each wraps
    round, which is what makes one key enough to reach every value.

    Args:
        rules: The ruleset to step. Left untouched — ``RuleConfig`` is
            frozen and a fresh one is returned.
        field: The ``RuleConfig`` field name to step.

    Returns:
        A new :class:`RuleConfig` differing from ``rules`` in ``field``.

    Raises:
        RulesetError: If ``field`` is not a ``RuleConfig`` field.
        InvalidRuleConfigError: If the stepped value names an impossible
            table — the caller renders the message and keeps the config it
            already had.
    """
    if field not in _FIELD_TYPES:
        raise RulesetError(f"unknown knob {field!r}")
    current = getattr(rules, field)
    if field == "target_score":
        index = TARGET_SCORES.index(current)
        value: Any = TARGET_SCORES[(index + 1) % len(TARGET_SCORES)]
    elif isinstance(current, Enum):
        members = list(type(current))
        value = members[(members.index(current) + 1) % len(members)]
    else:
        value = not current
    return dataclasses.replace(rules, **{field: value})


def non_default_knobs(rules: RuleConfig) -> tuple[tuple[str, str], ...]:
    """The knobs ``rules`` sets away from the §9 catalogue defaults.

    The compact answer to "how far from classic is this table?" — the
    setup panel counts them, and a simulation log can print them instead
    of dumping all 22 rows.

    Args:
        rules: The ruleset to compare against ``RuleConfig()``.

    Returns:
        ``(field_name, displayed_value)`` pairs in catalogue order; empty
        for the defaults.
    """
    baseline = RuleConfig()
    return tuple(
        (name, _display(getattr(rules, name)))
        for fields in SECTIONS.values()
        for name in fields
        if getattr(rules, name) != getattr(baseline, name)
    )


def resolve_setup(
    *,
    preset: str | None,
    rules_path: Path | None,
    aids: TableAids | None = None,
) -> TableSetup:
    """Turn the CLI's ``--preset`` / ``--rules`` / ``--no-live-score`` into a setup.

    Args:
        preset: A name from :data:`contrai_core.PRESETS`, or ``None``.
        rules_path: A path to a TOML setup file, or ``None``.
        aids: Interface aids named explicitly on the command line, or
            ``None`` when none were. ``None`` lets a file's own
            ``[table_aids]`` stand; anything else overrides it, because a
            flag typed just now outranks a file written earlier.

    Returns:
        Neither source given → the catalogue defaults; a preset →
        ``PRESETS[name]`` under its own name; a path → the file's setup.

    Raises:
        RulesetError: If both sources are given, or the preset is unknown.
        OSError: If the file cannot be read.
        InvalidRuleConfigError: If the file names an impossible table.
    """
    if preset is not None and rules_path is not None:
        raise RulesetError("--rules and --preset are mutually exclusive")
    if preset is not None:
        try:
            rules = PRESETS[preset]
        except KeyError:
            raise RulesetError(
                f"unknown preset {preset!r} (available: {', '.join(sorted(PRESETS))})"
            ) from None
        return TableSetup(rules=rules, aids=aids or TableAids(), origin=preset)
    if rules_path is not None:
        loaded = load_setup(rules_path)
        return loaded if aids is None else dataclasses.replace(loaded, aids=aids)
    return TableSetup(aids=aids or TableAids())


def resolve_rules(*, preset: str | None, rules_path: Path | None) -> RuleConfig:
    """Turn the CLI's ``--preset`` / ``--rules`` selection into a ``RuleConfig``.

    The ruleset half of :func:`resolve_setup`.

    Args:
        preset: A name from :data:`contrai_core.PRESETS`, or ``None``.
        rules_path: A path to a TOML ruleset file, or ``None``.

    Returns:
        Neither given → ``RuleConfig()``; a preset → ``PRESETS[name]``;
        a path → the file's rules.

    Raises:
        RulesetError: If both are given, or the preset name is unknown.
        OSError: If the file cannot be read.
        InvalidRuleConfigError: If the file names an impossible table.
    """
    return resolve_setup(preset=preset, rules_path=rules_path).rules
