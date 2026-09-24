"""Reads the local ``profile.toml`` into frozen, validated sections.

The profile is the **only** place the target site is named. Every selector,
URL, wire token and option id lives here; this module turns the document into
typed sections and refuses anything it does not recognise, so a site change
surfaces as a load error rather than as silently wrong data.

Strictness is the whole design. A profile that is merely *incomplete* — one
rank token missing, one field path renamed — would otherwise produce a record
that is well-formed and wrong, which is exactly the failure the verifier
cannot catch. So the loader consumes every table key by key and refuses
leftovers, and the token maps are checked for completeness once, at load time,
rather than at every lookup.
"""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contrai_core import PRESETS, Position, Rank, Suit

from .exceptions import ProfileError
from .schedule import Schedule, parse_range, timezone_named

#: A UI step is either one selector or a list of candidates tried in order.
Selector = str | tuple[str, ...]

#: Prefix marking a value that names an environment variable instead of
#: holding the secret itself.
_ENV_PREFIX = "env:"

#: Display name for the document's top level, where sections rather than keys
#: are the unit of "unknown".
_ROOT = "profile"

# The three canonical vocabularies a profile's token maps translate *into*.
# They are spelled here rather than taken from ``contrai_core`` because the
# enum values are English words ("Jack", "Spades", "North") and a hand-written
# profile wants the one-letter forms. Seats deliberately do **not** go through
# core's name-to-seat lookup: that maps the four names one to one, and the
# observed site's rotation runs the other way round the table, so a literal
# map is the bug this whole indirection exists to prevent (P-A finding D1).
# The seat map's own check lives in the translator.
_RANK_BY_NAME: Mapping[str, Rank] = {
    "7": Rank.SEVEN,
    "8": Rank.EIGHT,
    "9": Rank.NINE,
    "10": Rank.TEN,
    "J": Rank.JACK,
    "Q": Rank.QUEEN,
    "K": Rank.KING,
    "A": Rank.ACE,
}

_SUIT_BY_NAME: Mapping[str, Suit] = {
    "S": Suit.SPADES,
    "H": Suit.HEARTS,
    "D": Suit.DIAMONDS,
    "C": Suit.CLUBS,
}

_POSITION_BY_NAME: Mapping[str, Position] = {
    "N": Position.NORTH,
    "W": Position.WEST,
    "S": Position.SOUTH,
    "E": Position.EAST,
}

#: Every logical name ``[wire.fields]`` must bind, and no other — bar the
#: lobby's optional group, :data:`LOBBY_FIELD_NAMES`. The set spans
#: both halves of the scraper — the browser half reads ``spectators`` and
#: ``observable_tables``, the parser reads the rest — so the local document is
#: written once and neither half has to grow the schema later.
FIELD_NAMES: frozenset[str] = frozenset({
    "table",
    "table_id",
    "is_tournament",
    "seats",
    "seat_id",
    "seat_placement",
    "state",
    "players",
    "player_id",
    "player_name",
    "player_account",
    "player_level",
    "player_kind",
    "team",
    "round_index",
    "dealer",
    "trump",
    "deck_order",
    "hands",
    "tricks",
    "auction",
    "scores",
    "totals",
    "row_status",
    "row_value",
    "row_suit",
    "row_multiplier",
    "row_declarer",
    "row_winner",
    "side_taken",
    "side_belote",
    "side_marked_made",
    "side_marked_announced",
    "side_marked_belote",
    "bid_owner",
    "bid_suit",
    "bid_value",
    "doubler",
    "redoubler",
    "think_ms",
    "turn_limit_ms",
    "received_ms",
    "game_id",
    "ended",
    "left",
    "spectators",
    "observable_tables",
})

#: The logical names that read the lobby's socket events, all or none of them.
#: Optional because only a fleet waits in the lobby: ``lobby_hash`` is the row
#: an event describes, ``lobby_seats`` its whole seat map, ``lobby_seat_account``
#: the account inside one seat, and ``lobby_full`` the flag the row raises the
#: moment its game starts.
LOBBY_FIELD_NAMES: frozenset[str] = frozenset({
    "lobby_hash",
    "lobby_seats",
    "lobby_seat_account",
    "lobby_full",
})


@dataclass(frozen=True, slots=True)
class SiteSection:
    """Where the site lives and which language it answers in."""

    url: str
    locale: str


@dataclass(frozen=True, slots=True)
class AccountSection:
    """The spectator account the browser half logs in with."""

    email: str
    verification_code: str


@dataclass(frozen=True, slots=True)
class BrowserSection:
    """How the browser half drives Playwright."""

    headless: bool
    slow_mo_ms: int
    screenshot_on_error: bool


@dataclass(frozen=True, slots=True)
class SelectorSection:
    """One entry per UI step; a tuple means "try these in order"."""

    dismiss_tutorial: Selector
    login_start: Selector
    login_email: Selector
    login_continue: Selector
    code_input: Selector
    code_submit: Selector
    mode_online: Selector
    mode_observe: Selector
    variant: Selector
    pledge_dialog: Selector
    pledge_accept: Selector
    tournament_marker: Selector
    tournament_marker_text: str
    next_table: Selector
    rail_show: Selector | None
    """Optional: the control that brings the table's panel rails back.

    A table may slide the rails carrying its panel buttons off the screen
    while a game runs. This control puts them back, and the site shows it
    only while they are away — so a selector filtered on visibility answers
    both "are they away" and "what to click". ``None`` where nothing moves.
    """

    options_button: Selector
    options_row: Selector
    options_id_element: str
    """Looked up inside one option row: the element carrying the option's id."""

    options_state_element: str
    """Looked up inside one option row: the element whose class says on or off."""

    options_id_attr: str
    options_on_class: str
    panel_close: Selector
    scoreboard_button: Selector
    scoreboard_row: Selector
    scoreboard_cell: Selector
    seat_element: str
    """Carries a ``{seat}`` placeholder, filled with a seat token."""

    player_panel: Selector
    player_id_title: Selector
    player_id_prefix: str

    # -- the lobby: optional as a group, and all-or-none -------------------
    #
    # A fleet waits for games on the lobby screen rather than being seated by
    # the server, so it needs the route there and back. `run` does not, which
    # is why a profile naming none of these keys still loads; one naming only
    # some of them is refused, since a lobby half-described fails mid-shift.

    mode_new_games: Selector | None
    """The action beside the observe one that opens the list of games."""

    lobby_variant: Selector | None
    """The variant inside that list's own picker — not the observe branch's."""

    lobby_tables: str | None
    """The list container. Read in the page's own script, so plain CSS."""

    lobby_back: tuple[str, ...] | None
    """Back-ish controls, best first. Plain CSS, and chosen by layer."""

    lobby_layer: str | None
    """What a screen's controls sit in; its computed style says whether it shows."""

    lobby_row_tournament_class: str | None
    """The class marking the tournament row among the list's rows."""

    lobby_row_hash_attr: str | None
    """The row attribute holding the hash the lobby's socket events are keyed by."""

    @property
    def has_lobby(self) -> bool:
        """Whether the profile describes the lobby, which a fleet needs."""

        return self.mode_new_games is not None


#: The ``[selectors]`` keys that describe the lobby, all or none of them.
LOBBY_SELECTOR_KEYS: tuple[str, ...] = (
    "mode_new_games",
    "lobby_variant",
    "lobby_tables",
    "lobby_back",
    "lobby_layer",
    "lobby_row_tournament_class",
    "lobby_row_hash_attr",
)


@dataclass(frozen=True, slots=True)
class WireEvents:
    """The three event names the parser reacts to, and the lobby's own."""

    join_snapshot: str
    table_update: str
    counters: str
    lobby_table: str | None
    """The lobby's event for one row, or ``None`` where no fleet runs.

    Not a table's event at all: it is what drives the lobby screen, and the
    only way to see a tournament game's four players before it starts.
    """


@dataclass(frozen=True, slots=True)
class WireTokens:
    """The site's vocabulary, mapped onto ``contrai_core`` values."""

    ranks: Mapping[str, Rank]
    suits: Mapping[str, Suit]
    suit_words: Mapping[str, Suit]
    seats: Mapping[str, Position]
    seat_rotation: tuple[str, ...]
    team_letters: tuple[str, str]
    bid_slam: str
    bid_solo_slam: str
    pass_is_null: bool
    score_made: str
    """The status token a made contract carries on a score row."""

    def __post_init__(self) -> None:
        # A partial token map is the failure mode that produces a record with
        # one card missing rather than an error, so completeness is checked at
        # load time, once, instead of at every lookup.
        if set(self.ranks.values()) != set(Rank):
            raise ProfileError("[wire.tokens].ranks must name all eight ranks")
        if set(self.suits.values()) != set(Suit):
            raise ProfileError("[wire.tokens].suits must name all four suits")
        if set(self.seats.values()) != set(Position):
            raise ProfileError("[wire.tokens].seats must name all four seats")
        if set(self.seat_rotation) != set(self.seats):
            raise ProfileError(
                "[wire.tokens].seat_rotation must list the four seat names"
            )
        if len(self.team_letters) != 2:
            raise ProfileError("[wire.tokens].team_letters must hold two letters")


@dataclass(frozen=True, slots=True)
class WireSection:
    """How a socket frame is recognised, unwrapped and read."""

    socket_url_pattern: re.Pattern[str]
    game_envelope_kind: str
    keepalive_frame: str
    key_fields: tuple[str, ...]
    play_verb: str
    bid_verb_prefix: str
    deal_key_arity: int
    round_state_prefix: str
    resume_action: str
    resume_room_prefix: str
    resume_param: str
    draw_verb: str | None
    """The verb the pre-game draw is keyed by, at round 0.

    Every player draws a real card with its place in the deck, and none of
    those cards belongs to a hand. Round 0 is left out of every record either
    way; naming the verb is what lets a parse say so when something *other*
    than the draw turns up there, rather than dropping it unremarked.
    """

    events: WireEvents
    fields: Mapping[str, str]
    tokens: WireTokens

    @property
    def has_lobby(self) -> bool:
        """Whether the profile can read the lobby's socket, which a fleet needs."""

        return self.events.lobby_table is not None


@dataclass(frozen=True, slots=True)
class RulesSection:
    """The table's ruleset: a core preset plus the site's own option ids."""

    preset: str
    options: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class RecorderSection:
    """Thresholds the per-table loop runs under."""

    hop_after_rows: int
    stale_after_s: int
    health_interval_s: int
    snapshot_timeout_s: int


@dataclass(frozen=True, slots=True)
class EgressSection:
    """The gate every check runs against before any traffic reaches the site."""

    home_ip: str
    """The address that must never be the exit. May be indirected."""

    expected_country: str
    """Two letters, compared case-insensitively."""

    probe_url: str
    """A service answering with the caller's public address and country."""

    probe_ip_field: str
    probe_country_field: str
    tunnel_interface: str | None
    """The device the site's route must leave through, where one can be asked."""

    def __post_init__(self) -> None:
        try:
            ipaddress.ip_address(self.home_ip)
        except ValueError:
            # The value is never echoed: it may well be a real address, and a
            # refusal message is the one place it must not appear.
            raise ProfileError("[egress].home_ip is not an IP address") from None
        if len(self.expected_country) != 2 or not self.expected_country.isalpha():
            raise ProfileError(
                "[egress].expected_country must be a two-letter country code"
            )


#: The most workers a fleet may run. Ten is about the whole tournament
#: population: a courtesy ceiling, since every worker is one more spectator
#: the tables can count.
FLEET_CEILING: int = 10


@dataclass(frozen=True, slots=True)
class FleetSection:
    """How a fleet of workers waits, chases and shares its gates. Optional."""

    workers: int
    """How many workers to run, at most :data:`FLEET_CEILING`."""

    login_stagger_s: int
    """Seconds between one worker's first login and the next's."""

    scan_distinct_budget: int
    """Distinct tables a chase judges before giving up."""

    scan_deadline_s: int
    """Seconds a chase looks for its table before giving up."""

    roster_max_age_s: int
    """How old a starting roster may be and still be chased."""

    claim_ttl_s: int
    """How long an unrefreshed registry claim holds against other workers."""

    egress_cache_s: int
    """How long a passing egress reading answers the fleet's later checks."""

    census_enabled: bool
    """Whether each worker sweeps a few tables before its first lobby."""

    census_hops: int
    """Tables each worker's startup sweep looks at."""

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= FLEET_CEILING:
            raise ProfileError(
                f"[fleet].workers must be between 1 and {FLEET_CEILING}"
            )
        for name in ("login_stagger_s", "egress_cache_s"):
            if getattr(self, name) < 0:
                raise ProfileError(f"[fleet].{name} may not be negative")
        for name in ("scan_distinct_budget", "scan_deadline_s", "roster_max_age_s",
                     "claim_ttl_s", "census_hops"):
            if getattr(self, name) <= 0:
                raise ProfileError(f"[fleet].{name} must be positive")


@dataclass(frozen=True, slots=True)
class OutputSection:
    """Where records and raw logs are written, and for how long they are kept."""

    root: Path
    raw_root: Path
    raw_retention_days: int


@dataclass(frozen=True, slots=True)
class PrivacySection:
    """Inputs to the pseudonymisation step, which is not built yet."""

    pseudonym_salt: str | None


@dataclass(frozen=True, slots=True)
class Profile:
    """A whole profile document, validated."""

    site: SiteSection
    account: AccountSection
    browser: BrowserSection
    selectors: SelectorSection
    wire: WireSection
    rules: RulesSection
    recorder: RecorderSection
    schedule: Schedule
    egress: EgressSection
    output: OutputSection
    privacy: PrivacySection
    fleet: FleetSection | None
    """``None`` where no fleet runs; ``run`` never reads it."""

    def fleet_gaps(self) -> tuple[str, ...]:
        """What the profile lacks for ``contrai-scrape fleet``, by section.

        Returns:
            One phrase per missing group, empty when a fleet can run.
        """

        gaps = []
        if not self.selectors.has_lobby:
            gaps.append("the lobby's [selectors] keys")
        if not self.wire.has_lobby:
            gaps.append("[wire.events].lobby_table and the [wire.fields] lobby paths")
        if self.fleet is None:
            gaps.append("a [fleet] section")
        return tuple(gaps)


class _Table:
    """One TOML table, consumed key by key so leftovers can be refused.

    Every reader pops the key it reads, which turns "the document has a key
    nobody asked for" into a check on what remains at the end rather than a
    hand-kept list of allowed names that would drift from the dataclasses.
    """

    __slots__ = ("_name", "_data")

    def __init__(self, name: str, data: Mapping[str, Any]) -> None:
        self._name = name
        self._data = dict(data)

    @property
    def label(self) -> str:
        """The bracketed table name used in error messages."""

        return _ROOT if self._name == _ROOT else f"[{self._name}]"

    def _pop(self, key: str, expected: type) -> Any:
        """Read one key, checking its type.

        ``type(value) is not expected`` rather than ``isinstance``: TOML's
        ``true`` is a ``bool``, and a ``bool`` *is* an ``int``, so an
        ``isinstance`` check would quietly accept ``headless = 3``.
        """

        try:
            value = self._data.pop(key)
        except KeyError:
            raise ProfileError(
                f"{self.label} is missing the required key {key!r}"
            ) from None
        if type(value) is not expected:
            raise ProfileError(
                f"{self.label}.{key} must be {expected.__name__}, "
                f"not {type(value).__name__}"
            )
        return value

    def string(self, key: str) -> str:
        """Read a string key."""

        return self._pop(key, str)

    def integer(self, key: str) -> int:
        """Read an integer key."""

        return self._pop(key, int)

    def boolean(self, key: str) -> bool:
        """Read a boolean key."""

        return self._pop(key, bool)

    def strings(self, key: str) -> tuple[str, ...]:
        """Read a key holding a list of strings."""

        values = self._pop(key, list)
        for value in values:
            if type(value) is not str:
                raise ProfileError(
                    f"{self.label}.{key} must hold strings, "
                    f"not {type(value).__name__}"
                )
        return tuple(values)

    def selector(self, key: str) -> Selector:
        """Read a key holding one selector or a list of candidates."""

        value = self._data.get(key)
        if type(value) is list:
            return self.strings(key)
        return self.string(key)

    def section(self, key: str) -> _Table:
        """Read a key holding a sub-table."""

        value = self._pop(key, dict)
        name = key if self._name == _ROOT else f"{self._name}.{key}"
        return _Table(name, value)

    def mapping(self, key: str) -> Mapping[str, Any]:
        """Read a key holding a table whose *keys* are site-chosen."""

        return self._pop(key, dict)

    def optional_string(self, key: str) -> str | None:
        """Read a string key that may be absent."""

        if key not in self._data:
            return None
        return self.string(key)

    def has(self, key: str) -> bool:
        """Whether a key (or, at the top level, a section) is present."""

        return key in self._data

    def group(self, keys: tuple[str, ...], name: str) -> bool:
        """Whether an optional group of keys is present — all of it, or none.

        Args:
            keys: The group's keys.
            name: What the group describes, for the refusal.

        Returns:
            Whether every key is there. ``False`` means none of them is.

        Raises:
            ProfileError: Some of the group's keys are there and some are not.
                A half-described group loads cleanly and fails the first time
                it is used, which for a lobby is hours into a shift.
        """

        present = [key for key in keys if key in self._data]
        if present and len(present) != len(keys):
            missing = ", ".join(key for key in keys if key not in self._data)
            raise ProfileError(
                f"{self.label} describes {name} only in part; missing: {missing}"
            )
        return bool(present)

    def optional_selector(self, key: str) -> Selector | None:
        """Read a selector key that may be absent."""

        if key not in self._data:
            return None
        return self.selector(key)

    def booleans(self) -> Mapping[str, bool]:
        """Consume the whole table as site-chosen names bound to booleans.

        Used for ``[rules.options]``, whose keys are the site's own option
        ids: nothing here can be spelled out in advance, so only the values
        are checked.

        Returns:
            Option id to whether it is switched on.

        Raises:
            ProfileError: A value is not a boolean.
        """

        for name, value in self._data.items():
            if type(value) is not bool:
                raise ProfileError(
                    f"{self.label}.{name} must be a boolean, "
                    f"not {type(value).__name__}"
                )
        options = dict(self._data)
        self._data.clear()
        return options

    def done(self) -> None:
        """Refuse whatever nobody read.

        Raises:
            ProfileError: The table holds keys (or, at the top level,
                sections) this loader does not know.
        """

        if not self._data:
            return
        names = ", ".join(sorted(self._data))
        if self._name == _ROOT:
            raise ProfileError(f"the profile has unknown sections: {names}")
        raise ProfileError(f"{self.label} has unknown keys: {names}")


def _secret(label: str, value: str) -> str:
    """Resolve an ``env:NAME`` indirection, or pass the literal through.

    Args:
        label: The profile key, for the error message.
        value: The raw value read from the document.

    Returns:
        The secret itself.

    Raises:
        ProfileError: The named environment variable is not set.
    """

    if not value.startswith(_ENV_PREFIX):
        return value
    name = value[len(_ENV_PREFIX):]
    try:
        return os.environ[name]
    except KeyError:
        raise ProfileError(
            f"{label} reads {name}, which is not set in the environment"
        ) from None


def _tokens(table: _Table, key: str, vocabulary: Mapping[str, Any]) -> Mapping[str, Any]:
    """Map a site-chosen token table onto one of the canonical vocabularies.

    Args:
        table: The ``[wire.tokens]`` table being read.
        key: Which token map to read.
        vocabulary: Canonical name to ``contrai_core`` value.

    Returns:
        Site token to the core value it names.

    Raises:
        ProfileError: A value is not a string, or names nothing canonical.
    """

    raw = table.mapping(key)
    mapped: dict[str, Any] = {}
    for token, name in raw.items():
        if type(name) is not str or name not in vocabulary:
            raise ProfileError(
                f"[wire.tokens].{key}.{token} must name one of "
                f"{', '.join(vocabulary)}, not {name!r}"
            )
        mapped[token] = vocabulary[name]
    return mapped


def _fields(table: _Table) -> Mapping[str, str]:
    """Read ``[wire.fields]``: exactly :data:`FIELD_NAMES`, plus the lobby's or not.

    Args:
        table: The ``[wire]`` table the field map hangs off.

    Returns:
        Logical name to the dotted path that reads it.

    Raises:
        ProfileError: A logical name is unknown or missing, the lobby's names
            are there only in part, or a path is not a string.
    """

    raw = table.mapping("fields")
    lobby = set(raw) & LOBBY_FIELD_NAMES
    if lobby and lobby != LOBBY_FIELD_NAMES:
        raise ProfileError(
            "[wire.fields] describes the lobby only in part; missing: "
            + ", ".join(sorted(LOBBY_FIELD_NAMES - lobby))
        )
    unknown = sorted(set(raw) - FIELD_NAMES - LOBBY_FIELD_NAMES)
    if unknown:
        raise ProfileError(
            f"[wire.fields] names fields this parser does not read: "
            f"{', '.join(unknown)}"
        )
    missing = sorted(FIELD_NAMES - set(raw))
    if missing:
        raise ProfileError(
            f"[wire.fields] is missing: {', '.join(missing)}"
        )
    for name, path in raw.items():
        if type(path) is not str:
            raise ProfileError(
                f"[wire.fields].{name} must be a dotted path, "
                f"not {type(path).__name__}"
            )
    return dict(raw)


def _site(table: _Table) -> SiteSection:
    """Read ``[site]``."""

    section = SiteSection(url=table.string("url"), locale=table.string("locale"))
    table.done()
    return section


def _account(table: _Table) -> AccountSection:
    """Read ``[account]``, resolving both values' indirection."""

    section = AccountSection(
        email=_secret("[account].email", table.string("email")),
        verification_code=_secret(
            "[account].verification_code", table.string("verification_code")
        ),
    )
    table.done()
    return section


def _browser(table: _Table) -> BrowserSection:
    """Read ``[browser]``."""

    section = BrowserSection(
        headless=table.boolean("headless"),
        slow_mo_ms=table.integer("slow_mo_ms"),
        screenshot_on_error=table.boolean("screenshot_on_error"),
    )
    table.done()
    return section


def _selectors(table: _Table) -> SelectorSection:
    """Read ``[selectors]``."""

    seat_element = table.string("seat_element")
    if "{seat}" not in seat_element:
        raise ProfileError(
            "[selectors].seat_element must carry a {seat} placeholder"
        )
    lobby = table.group(LOBBY_SELECTOR_KEYS, "the lobby")
    back = table.selector("lobby_back") if lobby else None
    section = SelectorSection(
        dismiss_tutorial=table.selector("dismiss_tutorial"),
        login_start=table.selector("login_start"),
        login_email=table.selector("login_email"),
        login_continue=table.selector("login_continue"),
        code_input=table.selector("code_input"),
        code_submit=table.selector("code_submit"),
        mode_online=table.selector("mode_online"),
        mode_observe=table.selector("mode_observe"),
        variant=table.selector("variant"),
        pledge_dialog=table.selector("pledge_dialog"),
        pledge_accept=table.selector("pledge_accept"),
        tournament_marker=table.selector("tournament_marker"),
        tournament_marker_text=table.string("tournament_marker_text"),
        next_table=table.selector("next_table"),
        rail_show=table.optional_selector("rail_show"),
        options_button=table.selector("options_button"),
        options_row=table.selector("options_row"),
        options_id_element=table.string("options_id_element"),
        options_state_element=table.string("options_state_element"),
        options_id_attr=table.string("options_id_attr"),
        options_on_class=table.string("options_on_class"),
        panel_close=table.selector("panel_close"),
        scoreboard_button=table.selector("scoreboard_button"),
        scoreboard_row=table.selector("scoreboard_row"),
        scoreboard_cell=table.selector("scoreboard_cell"),
        seat_element=seat_element,
        player_panel=table.selector("player_panel"),
        player_id_title=table.selector("player_id_title"),
        player_id_prefix=table.string("player_id_prefix"),
        mode_new_games=table.selector("mode_new_games") if lobby else None,
        lobby_variant=table.selector("lobby_variant") if lobby else None,
        lobby_tables=table.string("lobby_tables") if lobby else None,
        # Normalised to a tuple: the list is a ranking, and a single control
        # is a ranking of one.
        lobby_back=(back,) if isinstance(back, str) else back,
        lobby_layer=table.string("lobby_layer") if lobby else None,
        lobby_row_tournament_class=(
            table.string("lobby_row_tournament_class") if lobby else None
        ),
        lobby_row_hash_attr=table.string("lobby_row_hash_attr") if lobby else None,
    )
    table.done()
    return section


def _wire(table: _Table) -> WireSection:
    """Read ``[wire]`` and its three sub-tables."""

    pattern = table.string("socket_url_pattern")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ProfileError(
            f"[wire].socket_url_pattern is not a valid regex: {error}"
        ) from error

    events_table = table.section("events")
    events = WireEvents(
        join_snapshot=events_table.string("join_snapshot"),
        table_update=events_table.string("table_update"),
        counters=events_table.string("counters"),
        lobby_table=events_table.optional_string("lobby_table"),
    )
    events_table.done()

    fields = _fields(table)
    if (events.lobby_table is None) != LOBBY_FIELD_NAMES.isdisjoint(fields):
        # The event and the paths that read it are one description: an event
        # nothing can read, or paths for an event nobody names, both load and
        # then read nothing at all.
        raise ProfileError(
            "[wire.events].lobby_table and the [wire.fields] lobby paths "
            f"({', '.join(sorted(LOBBY_FIELD_NAMES))}) go together: name all or none"
        )

    tokens_table = table.section("tokens")
    tokens = WireTokens(
        ranks=_tokens(tokens_table, "ranks", _RANK_BY_NAME),
        suits=_tokens(tokens_table, "suits", _SUIT_BY_NAME),
        suit_words=_tokens(tokens_table, "suit_words", _SUIT_BY_NAME),
        seats=_tokens(tokens_table, "seats", _POSITION_BY_NAME),
        seat_rotation=tokens_table.strings("seat_rotation"),
        team_letters=tokens_table.strings("team_letters"),  # type: ignore[arg-type]
        bid_slam=tokens_table.string("bid_slam"),
        bid_solo_slam=tokens_table.string("bid_solo_slam"),
        pass_is_null=tokens_table.boolean("pass_is_null"),
        score_made=tokens_table.string("score_made"),
    )
    tokens_table.done()

    section = WireSection(
        socket_url_pattern=compiled,
        game_envelope_kind=table.string("game_envelope_kind"),
        keepalive_frame=table.string("keepalive_frame"),
        key_fields=table.strings("key_fields"),
        play_verb=table.string("play_verb"),
        bid_verb_prefix=table.string("bid_verb_prefix"),
        deal_key_arity=table.integer("deal_key_arity"),
        round_state_prefix=table.string("round_state_prefix"),
        resume_action=table.string("resume_action"),
        resume_room_prefix=table.string("resume_room_prefix"),
        resume_param=table.string("resume_param"),
        draw_verb=table.optional_string("draw_verb"),
        events=events,
        fields=fields,
        tokens=tokens,
    )
    table.done()
    return section


def _rules(table: _Table) -> RulesSection:
    """Read ``[rules]`` and its ``[rules.options]`` sub-table."""

    preset = table.string("preset")
    if preset not in PRESETS:
        raise ProfileError(
            f"[rules].preset names {preset!r}, which is not one of "
            f"{', '.join(PRESETS)}"
        )
    options_table = table.section("options")
    section = RulesSection(preset=preset, options=options_table.booleans())
    table.done()
    return section


def _recorder(table: _Table) -> RecorderSection:
    """Read ``[recorder]``."""

    section = RecorderSection(
        hop_after_rows=table.integer("hop_after_rows"),
        stale_after_s=table.integer("stale_after_s"),
        health_interval_s=table.integer("health_interval_s"),
        snapshot_timeout_s=table.integer("snapshot_timeout_s"),
    )
    table.done()
    return section


def _schedule(table: _Table) -> Schedule:
    """Read ``[schedule]``."""

    section = Schedule(
        timezone=timezone_named(table.string("timezone")),
        active=tuple(parse_range(text) for text in table.strings("active")),
        finish_current_game=table.boolean("finish_current_game"),
        max_overrun_minutes=table.integer("max_overrun_minutes"),
        idle_poll_minutes=table.integer("idle_poll_minutes"),
    )
    table.done()
    return section


def _egress(table: _Table) -> EgressSection:
    """Read ``[egress]``, resolving the home address's indirection."""

    section = EgressSection(
        home_ip=_secret("[egress].home_ip", table.string("home_ip")),
        expected_country=table.string("expected_country"),
        probe_url=table.string("probe_url"),
        probe_ip_field=table.string("probe_ip_field"),
        probe_country_field=table.string("probe_country_field"),
        tunnel_interface=table.optional_string("tunnel_interface"),
    )
    table.done()
    return section


def _output(table: _Table, base: Path) -> OutputSection:
    """Read ``[output]``, resolving both roots against the profile's directory.

    Args:
        table: The ``[output]`` table.
        base: The directory the profile file sits in, so a profile can be
            moved with the data it points at.
    """

    retention = table.integer("raw_retention_days")
    if retention < 0:
        raise ProfileError("[output].raw_retention_days may not be negative")
    section = OutputSection(
        root=(base / table.string("root")).resolve(),
        raw_root=(base / table.string("raw_root")).resolve(),
        raw_retention_days=retention,
    )
    table.done()
    return section


def _fleet(table: _Table) -> FleetSection:
    """Read ``[fleet]``."""

    section = FleetSection(
        workers=table.integer("workers"),
        login_stagger_s=table.integer("login_stagger_s"),
        scan_distinct_budget=table.integer("scan_distinct_budget"),
        scan_deadline_s=table.integer("scan_deadline_s"),
        roster_max_age_s=table.integer("roster_max_age_s"),
        claim_ttl_s=table.integer("claim_ttl_s"),
        egress_cache_s=table.integer("egress_cache_s"),
        census_enabled=table.boolean("census_enabled"),
        census_hops=table.integer("census_hops"),
    )
    table.done()
    return section


def _privacy(table: _Table) -> PrivacySection:
    """Read ``[privacy]``; the salt may be absent entirely."""

    raw = table.optional_string("pseudonym_salt")
    salt = None if raw is None else _secret("[privacy].pseudonym_salt", raw)
    table.done()
    return PrivacySection(pseudonym_salt=salt)


def load_profile(path: Path | str) -> Profile:
    """Read and validate a profile document.

    Args:
        path: The ``profile.toml`` to read.

    Returns:
        The parsed :class:`Profile`.

    Raises:
        ProfileError: The file is missing or unreadable, a section or key is
            unknown, a required key is absent, a value has the wrong type, or
            a token map is incomplete.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ProfileError(f"cannot read the profile at {path}: {error}") from error
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{path} is not valid TOML: {error}") from error

    root = _Table(_ROOT, raw)
    profile = Profile(
        site=_site(root.section("site")),
        account=_account(root.section("account")),
        browser=_browser(root.section("browser")),
        selectors=_selectors(root.section("selectors")),
        wire=_wire(root.section("wire")),
        rules=_rules(root.section("rules")),
        recorder=_recorder(root.section("recorder")),
        schedule=_schedule(root.section("schedule")),
        egress=_egress(root.section("egress")),
        output=_output(root.section("output"), path.parent),
        privacy=_privacy(root.section("privacy")),
        fleet=_fleet(root.section("fleet")) if root.has("fleet") else None,
    )
    root.done()
    return profile
