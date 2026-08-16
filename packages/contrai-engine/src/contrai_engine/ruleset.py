"""Engine-side ruleset loading: TOML → :class:`RuleConfig`, and back.

Lives in the engine because core stays I/O-free. Unknown sections, unknown
keys, wrong types and unknown enum tokens are **errors**, not warnings — a
typo'd knob that silently kept its default would corrupt a logged
experiment. ``dump_ruleset`` writes the same six-section layout back so a
resolved ruleset can be archived next to a simulation or a scraped game.
"""

from __future__ import annotations

import tomllib
from enum import Enum
from pathlib import Path
from typing import Any, get_type_hints

from contrai_core import PRESETS, RuleConfig


class RulesetError(ValueError):
    """A ruleset document or selection is malformed.

    Bad TOML, an unknown section or key, a value of the wrong type, an
    unknown enum token, an unknown preset name, or both a file and a preset
    given at once.
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

# Resolved once. get_type_hints (not Field.type) so string annotations can
# never sneak in as the compared type.
_FIELD_TYPES: dict[str, type] = get_type_hints(RuleConfig)


def _coerce(section: str, key: str, value: Any) -> Any:
    """Check ``value`` against the field's declared type; map enum tokens.

    Args:
        section: The TOML section the key was read from, for the message.
        key: The :class:`RuleConfig` field name.
        value: The raw value ``tomllib`` produced.

    Returns:
        The value as the field's declared type — an enum *member* for the
        three token-typed knobs, the value itself otherwise.

    Raises:
        RulesetError: If the value is of the wrong type, or is not one of
            an enum field's tokens.
    """
    expected = _FIELD_TYPES[key]
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


def parse_ruleset(text: str) -> RuleConfig:
    """Parse a TOML ruleset document into a validated :class:`RuleConfig`.

    Missing keys keep their §9 defaults, so a file is a *partial override*
    rather than a full specification.

    Args:
        text: The TOML document.

    Returns:
        The :class:`RuleConfig` the document names.

    Raises:
        RulesetError: On bad TOML, an unknown section or key, a wrong type
            or an unknown enum token.
        InvalidRuleConfigError: When the document is well-formed but names
            an impossible table — core's own validation, propagated
            unchanged (it is already a ``ValueError``).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesetError(f"invalid TOML: {exc}") from exc
    kwargs: dict[str, Any] = {}
    for section, body in data.items():
        if section not in SECTIONS:
            raise RulesetError(
                f"unknown section {section!r} (expected one of: {', '.join(SECTIONS)})"
            )
        if not isinstance(body, dict):
            raise RulesetError(f"{section!r} must be a [section] table, not a value")
        allowed = SECTIONS[section]
        for key, value in body.items():
            if key not in allowed:
                raise RulesetError(
                    f"unknown key {key!r} in [{section}] (expected one of: {', '.join(allowed)})"
                )
            kwargs[key] = _coerce(section, key, value)
    return RuleConfig(**kwargs)


def load_ruleset(path: Path) -> RuleConfig:
    """Read a UTF-8 TOML ruleset file.

    Read as text rather than through ``tomllib.load``, which insists on a
    binary handle; TOML mandates UTF-8, so the encoding is not a guess.

    Args:
        path: Path to the ruleset file.

    Returns:
        The :class:`RuleConfig` the file names.

    Raises:
        OSError: If the file cannot be read — a missing file surfaces as
            ``FileNotFoundError``, for the caller to turn into a usage error.
        RulesetError: On a malformed document (see :func:`parse_ruleset`).
    """
    return parse_ruleset(Path(path).read_text(encoding="utf-8"))


def _format(value: Any) -> str:
    """Render one field value as its TOML literal."""
    if isinstance(value, bool):          # before int: bool is an int
        return "true" if value else "false"
    if isinstance(value, Enum):
        return f'"{value.value}"'
    return str(value)


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
    blocks = []
    for section, keys in SECTIONS.items():
        width = max(len(k) for k in keys)
        lines = [f"[{section}]"] + [
            f"{k.ljust(width)} = {_format(getattr(config, k))}" for k in keys
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def resolve_rules(*, preset: str | None, rules_path: Path | None) -> RuleConfig:
    """Turn the CLI's ``--preset`` / ``--rules`` selection into a ``RuleConfig``.

    Args:
        preset: A name from :data:`contrai_core.PRESETS`, or ``None``.
        rules_path: A path to a TOML ruleset file, or ``None``.

    Returns:
        Neither given → ``RuleConfig()``; a preset → ``PRESETS[name]``;
        a path → :func:`load_ruleset`.

    Raises:
        RulesetError: If both are given, or the preset name is unknown.
        OSError: If the file cannot be read.
        InvalidRuleConfigError: If the file names an impossible table.
    """
    if preset is not None and rules_path is not None:
        raise RulesetError("--rules and --preset are mutually exclusive")
    if preset is not None:
        try:
            return PRESETS[preset]
        except KeyError:
            raise RulesetError(
                f"unknown preset {preset!r} (available: {', '.join(sorted(PRESETS))})"
            ) from None
    if rules_path is not None:
        return load_ruleset(rules_path)
    return RuleConfig()
