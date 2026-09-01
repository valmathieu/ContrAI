"""RuleConfig: the table-rule catalogue as one frozen value.

One field per *configurable* row of ``contree-domain.md`` §9, named after
the catalogue's own wording so the two can be read side by side.
``RuleConfig()`` *is* the §9 default set. Nothing consults a field yet —
each knob is wired to behaviour in a later step; this module only makes
the ruleset a nameable, hashable value that a log line, a simulation
result or a scraped game can carry.

The three enums render as their TOML token (``str(Rounding.EXACT) ==
"exact"``), so the same value spells the member in a config file and in a
message.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .exceptions import InvalidRuleConfigError


class TurnDirection(Enum):
    """Which way play proceeds around the table (§9.1)."""

    ANTICLOCKWISE = "anticlockwise"
    CLOCKWISE = "clockwise"

    def __str__(self) -> str:
        """Render as the TOML token, e.g. ``"clockwise"``."""
        return self.value


class AllTrumpBelote(Enum):
    """How many belotes an all-trump contract admits (§9.2)."""

    NONE = "none"
    SINGLE = "single"
    FOUR = "four"

    def __str__(self) -> str:
        """Render as the TOML token, e.g. ``"single"``."""
        return self.value


class Rounding(Enum):
    """How a side's marked points are rounded before adding up (§9.6)."""

    EXACT = "exact"
    NEAREST_10 = "nearest_10"
    NEAREST_5 = "nearest_5"

    def __str__(self) -> str:
        """Render as the TOML token, e.g. ``"nearest_10"``."""
        return self.value


#: The selectable game targets (contree-domain.md §9.1).
TARGET_SCORES: tuple[int, ...] = (500, 1000, 1500, 2000, 3000, 4000, 5000)


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """The 22 configurable table rules of ``contree-domain.md`` §9.

    Defaults are the catalogue's bold values. Two combinations are
    documented as inert rather than rejected: ``failed_slam_marks_announced_points``
    has no effect unless ``any_failure_marks_160`` is on, and
    ``all_trump_belote`` is meaningless while ``extended_trump_choices``
    is off.

    Attributes:
        target_score: Game target, one of :data:`TARGET_SCORES` (§9.1).
        turn_direction: Anticlockwise or clockwise play (§9.1).
        extended_trump_choices: No trump and all trump are biddable (§9.2).
        all_trump_belote: Belote regime under an all-trump contract (§9.2).
        reshuffle_every_round: Shuffle instead of cut between rounds (§9.3).
        solo_slam_available: Solo Slam is on the bid ladder (§9.4).
        slam_can_be_doubled: A Slam may be doubled (§9.4).
        solo_slam_can_be_doubled: A Solo Slam may be doubled (§9.4).
        under_trump_exemption: A void player may discard instead of
            under-trumping (§9.5).
        solo_slam_gives_the_lead: The Solo Slam bidder leads trick 1 (§9.5).
        belote_counts_toward_contract: Belote points help make the
            contract (§9.5).
        belote_lost_when_contract_fails: A failed declarer loses its
            belote (§9.5).
        mark_made_points: Made-points marking convention (§9.6).
        mark_announced_points: Announced-points marking convention (§9.6).
        only_announced_points_multiplied: Doubling multiplies the announced
            component only (§9.6).
        any_failure_marks_160: Every failure marks a flat 160 (§9.6).
        unannounced_slam_substitute: 250 / 500 replaces the pile on an
            unannounced sweep (§9.6).
        failed_slam_marks_made_points: Failed Slam — made component (§9.6).
        failed_slam_marks_announced_points: Failed Slam — announced
            component (§9.6).
        attack_must_outscore_defense: A tie fails the contract (§9.6).
        rounding: Rounding of marked points (§9.6).
        win_on_belote_points_alone: Belote points can cross the target
            (§9.6).

    Raises:
        InvalidRuleConfigError: If both marking conventions are off, or
            ``target_score`` is not on :data:`TARGET_SCORES`.
    """

    # --- §9.1 General ---
    target_score: int = 2000
    turn_direction: TurnDirection = TurnDirection.ANTICLOCKWISE
    # --- §9.2 Trump variants ---
    extended_trump_choices: bool = False
    all_trump_belote: AllTrumpBelote = AllTrumpBelote.SINGLE
    # --- §9.3 Deal ---
    reshuffle_every_round: bool = False
    # --- §9.4 Bidding ---
    solo_slam_available: bool = True
    slam_can_be_doubled: bool = True
    solo_slam_can_be_doubled: bool = True
    # --- §9.5 Card play ---
    under_trump_exemption: bool = True
    solo_slam_gives_the_lead: bool = False
    belote_counts_toward_contract: bool = True
    belote_lost_when_contract_fails: bool = False
    # --- §9.6 Scoring ---
    mark_made_points: bool = True
    mark_announced_points: bool = True
    only_announced_points_multiplied: bool = True
    any_failure_marks_160: bool = False
    unannounced_slam_substitute: bool = True
    failed_slam_marks_made_points: bool = True
    failed_slam_marks_announced_points: bool = True
    attack_must_outscore_defense: bool = True
    rounding: Rounding = Rounding.EXACT
    win_on_belote_points_alone: bool = True

    def __post_init__(self) -> None:
        """Reject the two configurations §9 calls impossible.

        Raises:
            InvalidRuleConfigError: If neither marking convention is on,
                or ``target_score`` is off :data:`TARGET_SCORES`.
        """
        # The one genuine contradiction (§7.3): a table marking neither
        # component keeps no score at all.
        if not (self.mark_made_points or self.mark_announced_points):
            raise InvalidRuleConfigError(
                "mark_made_points and mark_announced_points cannot both be "
                "False: a table marking neither keeps no score at all",
                context="RuleConfig",
            )
        if self.target_score not in TARGET_SCORES:
            raise InvalidRuleConfigError(
                f"target_score must be one of {TARGET_SCORES}, got "
                f"{self.target_score!r}",
                context="RuleConfig",
            )

    @classmethod
    def classic(cls) -> "RuleConfig":
        """The §9 default set, by name — so a log can say *which* defaults.

        Returns:
            A :class:`RuleConfig` with every field at its catalogue default.
        """
        return cls()


#: Named rulesets the engine's ``--preset`` flag can select. ``belote-rebelote``
#: (site parity for the scraper) is deferred until its values are observed.
PRESETS: Mapping[str, RuleConfig] = MappingProxyType(
    {"classic": RuleConfig.classic()}
)
