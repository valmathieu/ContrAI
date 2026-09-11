"""Domain value <-> record token, in one place and strictly.

The record's spelling is deliberately narrow ASCII — ``10S``, ``JH``,
``NT``, ``NS``, ``slam`` — so a file stays greppable, diffable and
independent of any wire's glyphs. Everything that turns a
:class:`~contrai_core.Card`, a seat, a side, a bid or a whole
:class:`~contrai_core.RuleConfig` into one of those tokens, and back,
lives here; :mod:`contrai_data.events` never sees a token and
:mod:`contrai_data.codec` never sees a domain value it did not get from
this module.

**Parsing is strict in both directions.** An unrecognised token raises
:class:`~contrai_data.RecordFormatError` rather than resolving to a
default, because a record is read long after the run that wrote it: a
token quietly read as something else produces a plausible game that
never happened, and no downstream check can tell. The ruleset is the one
place that rule needs a second half — see :func:`parse_ruleset`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import get_type_hints

from contrai_core import (
    Bid,
    Card,
    ContractBid,
    ContractSuit,
    DoubleBid,
    PassBid,
    Position,
    Rank,
    RedoubleBid,
    RuleConfig,
    SlamLevel,
    Suit,
    TeamSide,
    TrumpVariant,
)

from .exceptions import RecordFormatError

# ----------------------------------------------------------------------
# Lookup tables, built once and frozen
# ----------------------------------------------------------------------

_RANK_TOKENS: Mapping[Rank, str] = MappingProxyType(
    {
        Rank.SEVEN: "7",
        Rank.EIGHT: "8",
        Rank.NINE: "9",
        Rank.TEN: "10",
        Rank.JACK: "J",
        Rank.QUEEN: "Q",
        Rank.KING: "K",
        Rank.ACE: "A",
    }
)
_RANKS_BY_TOKEN: Mapping[str, Rank] = MappingProxyType(
    {token: rank for rank, token in _RANK_TOKENS.items()}
)

_SUIT_TOKENS: Mapping[Suit, str] = MappingProxyType(
    {
        Suit.SPADES: "S",
        Suit.HEARTS: "H",
        Suit.DIAMONDS: "D",
        Suit.CLUBS: "C",
    }
)
_SUITS_BY_TOKEN: Mapping[str, Suit] = MappingProxyType(
    {token: suit for suit, token in _SUIT_TOKENS.items()}
)

_VARIANT_TOKENS: Mapping[TrumpVariant, str] = MappingProxyType(
    {TrumpVariant.NO_TRUMP: "NT", TrumpVariant.ALL_TRUMP: "AT"}
)
_VARIANTS_BY_TOKEN: Mapping[str, TrumpVariant] = MappingProxyType(
    {token: variant for variant, token in _VARIANT_TOKENS.items()}
)

_SLAM_TOKENS: Mapping[SlamLevel, str] = MappingProxyType(
    {SlamLevel.SLAM: "slam", SlamLevel.SOLO_SLAM: "solo_slam"}
)
_SLAMS_BY_TOKEN: Mapping[str, SlamLevel] = MappingProxyType(
    {token: level for level, token in _SLAM_TOKENS.items()}
)

# North, West, South, East — the four initials are distinct, which is
# exactly what makes a one-letter seat token unambiguous. Derived from the
# enum rather than written out, so a renamed seat cannot drift from its
# token silently.
_POSITIONS_BY_TOKEN: Mapping[str, Position] = MappingProxyType(
    {position.name[0]: position for position in Position}
)

_SIDES_BY_TOKEN: Mapping[str, TeamSide] = MappingProxyType(
    {side.value: side for side in TeamSide}
)


# ----------------------------------------------------------------------
# Seats and sides
# ----------------------------------------------------------------------


def position_token(position: Position) -> str:
    """Spell a seat as its one-letter token.

    Args:
        position: The seat.

    Returns:
        ``"N"``, ``"W"``, ``"S"`` or ``"E"``.
    """

    return position.name[0]


def parse_position(token: object) -> Position:
    """Read a one-letter seat token back into a :class:`Position`.

    Args:
        token: The raw token off a record line.

    Returns:
        The seat it names.

    Raises:
        RecordFormatError: If it is not one of the four seat tokens.
    """

    if not isinstance(token, str) or token not in _POSITIONS_BY_TOKEN:
        raise RecordFormatError(
            f"Unknown seat token: {token!r}. Must be one of "
            f"{sorted(_POSITIONS_BY_TOKEN)}."
        )
    return _POSITIONS_BY_TOKEN[token]


def side_token(side: TeamSide) -> str:
    """Spell a side as its token.

    Args:
        side: The side.

    Returns:
        ``"NS"`` or ``"EW"``.
    """

    return side.value


def parse_side(token: object) -> TeamSide:
    """Read a side token back into a :class:`TeamSide`.

    Args:
        token: The raw token off a record line.

    Returns:
        The side it names.

    Raises:
        RecordFormatError: If it is not one of the two side tokens.
    """

    if not isinstance(token, str) or token not in _SIDES_BY_TOKEN:
        raise RecordFormatError(
            f"Unknown side token: {token!r}. Must be one of "
            f"{sorted(_SIDES_BY_TOKEN)}."
        )
    return _SIDES_BY_TOKEN[token]


# ----------------------------------------------------------------------
# Cards
# ----------------------------------------------------------------------


def card_token(card: Card) -> str:
    """Spell a card as rank followed by suit.

    Args:
        card: The card.

    Returns:
        Its token, e.g. ``"10S"``, ``"JH"``, ``"AD"``.
    """

    return f"{_RANK_TOKENS[card.rank]}{_SUIT_TOKENS[card.suit]}"


def parse_card(token: object) -> Card:
    """Read a card token back into a :class:`Card`.

    Args:
        token: The raw token off a record line.

    Returns:
        The card it names.

    Raises:
        RecordFormatError: If the rank or the suit half is unknown.
    """

    if not isinstance(token, str) or len(token) < 2:
        raise RecordFormatError(
            f"Unknown card token: {token!r}. Expected a rank followed by a "
            f"suit, e.g. '10S'."
        )
    # The rank is everything but the last character, not a fixed offset:
    # ``10`` is the only two-character rank, so slicing at position 1 would
    # read the suit off the wrong character for all four tens.
    rank_token, suit_token = token[:-1], token[-1]
    if rank_token not in _RANKS_BY_TOKEN or suit_token not in _SUITS_BY_TOKEN:
        raise RecordFormatError(
            f"Unknown card token: {token!r}. Ranks are "
            f"{sorted(_RANKS_BY_TOKEN)}, suits are {sorted(_SUITS_BY_TOKEN)}."
        )
    return Card(_SUITS_BY_TOKEN[suit_token], _RANKS_BY_TOKEN[rank_token])


# ----------------------------------------------------------------------
# Contract terms
# ----------------------------------------------------------------------


def contract_suit_token(suit: ContractSuit) -> str:
    """Spell a contract trump, including the two whole-hand variants.

    Args:
        suit: One of the four card suits, or a
            :class:`~contrai_core.TrumpVariant`.

    Returns:
        ``"S"`` / ``"H"`` / ``"D"`` / ``"C"`` / ``"NT"`` / ``"AT"``.
    """

    if isinstance(suit, TrumpVariant):
        return _VARIANT_TOKENS[suit]
    return _SUIT_TOKENS[suit]


def parse_contract_suit(token: object) -> ContractSuit:
    """Read a contract-trump token back into a :class:`ContractSuit`.

    Args:
        token: The raw token off a record line.

    Returns:
        The card suit or trump variant it names.

    Raises:
        RecordFormatError: If it is none of the six.
    """

    if isinstance(token, str):
        if token in _SUITS_BY_TOKEN:
            return _SUITS_BY_TOKEN[token]
        if token in _VARIANTS_BY_TOKEN:
            return _VARIANTS_BY_TOKEN[token]
    raise RecordFormatError(
        f"Unknown contract suit token: {token!r}. Must be one of "
        f"{sorted(_SUITS_BY_TOKEN) + sorted(_VARIANTS_BY_TOKEN)}."
    )


def contract_value_token(value: int | SlamLevel) -> int | str:
    """Spell a contract value: a number stays a number, a Slam is a word.

    Args:
        value: A step on the 80–240 ladder, or a
            :class:`~contrai_core.SlamLevel`.

    Returns:
        The int itself, or ``"slam"`` / ``"solo_slam"``.
    """

    if isinstance(value, SlamLevel):
        return _SLAM_TOKENS[value]
    return value


def parse_contract_value(token: object) -> int | SlamLevel:
    """Read a contract value back, checking it is on core's own ladder.

    Reuses :attr:`~contrai_core.ContractBid.VALID_VALUES` rather than
    restating the ladder, so the format cannot drift from the bid rules.

    Args:
        token: The raw value off a record line.

    Returns:
        The numeric step or the :class:`~contrai_core.SlamLevel`.

    Raises:
        RecordFormatError: If it is neither a Slam word nor a step on the
            ladder. ``True`` is refused with it: a bool is an ``int`` to
            ``isinstance``, so the check is on the exact type.
    """

    if isinstance(token, str) and token in _SLAMS_BY_TOKEN:
        return _SLAMS_BY_TOKEN[token]
    if type(token) is int and token in ContractBid.VALID_VALUES:
        return token
    raise RecordFormatError(
        f"Unknown contract value: {token!r}. Must be a step on "
        f"{[v for v in ContractBid.VALID_VALUES if isinstance(v, int)]} or "
        f"one of {sorted(_SLAMS_BY_TOKEN)}."
    )


# ----------------------------------------------------------------------
# Bids
# ----------------------------------------------------------------------


def bid_payload(bid: Bid[Position]) -> dict[str, object]:
    """Spell a bid as its record payload.

    The bidder is **not** part of the payload: a ``bid`` event already
    carries its own ``position`` field, and writing the seat twice inside
    one event would just be a second place for it to be wrong.

    Args:
        bid: The bid, seated on a bare :class:`Position`.

    Returns:
        ``{"kind": …}``, plus ``value`` and ``suit`` for a contract bid.

    Raises:
        RecordFormatError: If the bid is a variant the format cannot
            spell.
    """

    match bid:
        case ContractBid():
            return {
                "kind": "contract",
                "value": contract_value_token(bid.value),
                "suit": contract_suit_token(bid.suit),
            }
        case RedoubleBid():
            return {"kind": "redouble"}
        case DoubleBid():
            return {"kind": "double"}
        case PassBid():
            return {"kind": "pass"}
        case _:
            raise RecordFormatError(
                f"Unknown bid variant: {type(bid).__name__}"
            )


def _require(payload: Mapping[str, object], key: str, where: str) -> object:
    """Read a required key off a payload.

    Args:
        payload: The mapping to read from.
        key: The key that must be present.
        where: What is being parsed, for the message.

    Returns:
        The value at ``key``.

    Raises:
        RecordFormatError: If the key is absent.
    """

    if key not in payload:
        raise RecordFormatError(f"A {where} is missing field {key!r}")
    return payload[key]


def parse_bid(payload: object, position: Position) -> Bid[Position]:
    """Read a bid payload back, seating it at ``position``.

    Args:
        payload: The ``bid`` object off a record line.
        position: The seat the enclosing event files the bid under.

    Returns:
        The bid, seated on ``position``.

    Raises:
        RecordFormatError: If the payload is not a mapping, names no
            kind, names an unknown one, or omits a contract's terms.
    """

    if not isinstance(payload, Mapping):
        raise RecordFormatError(
            f"A bid is a mapping with a kind, got {type(payload).__name__}"
        )
    kind = _require(payload, "kind", "bid")
    match kind:
        case "pass":
            return PassBid(player=position)
        case "double":
            return DoubleBid(player=position)
        case "redouble":
            return RedoubleBid(player=position)
        case "contract":
            return ContractBid(
                player=position,
                value=parse_contract_value(_require(payload, "value", "contract bid")),
                suit=parse_contract_suit(_require(payload, "suit", "contract bid")),
            )
        case _:
            raise RecordFormatError(
                f"Unknown bid kind: {kind!r}. Must be one of "
                f"['contract', 'double', 'pass', 'redouble']."
            )


# ----------------------------------------------------------------------
# The table ruleset
# ----------------------------------------------------------------------


def ruleset_payload(rules: RuleConfig) -> dict[str, object]:
    """Spell a whole ruleset as a flat mapping of knob to value.

    One key per :class:`~contrai_core.RuleConfig` field, in field order,
    with each enum knob written as its token. Flat rather than nested:
    the knobs have no hierarchy in core either, and a flat payload is
    what makes a missing key mean exactly "this producer predates the
    knob".

    Args:
        rules: The table ruleset.

    Returns:
        The payload, one entry per knob.
    """

    payload: dict[str, object] = {}
    for field in dataclasses.fields(RuleConfig):
        value = getattr(rules, field.name)
        payload[field.name] = value.value if isinstance(value, Enum) else value
    return payload


def parse_ruleset(payload: object) -> RuleConfig:
    """Read a flat ruleset payload back into a :class:`RuleConfig`.

    Asymmetric on purpose. An **unknown** key is refused: it names a rule
    this build has no field for, and dropping it would replay the game
    under a ruleset it was never played under — exactly the kind of
    silent divergence the verifier cannot catch. A **missing** key takes
    its ``RuleConfig`` default: the record predates the knob, so its
    producer was by construction playing that default.

    Args:
        payload: The ``ruleset.config`` mapping off a ``game_started``
            event.

    Returns:
        The table ruleset the game was played under.

    Raises:
        RecordFormatError: If ``payload`` is not a mapping, names a knob
            this build does not have, or gives one a value of the wrong
            type or an unknown enum token.
        InvalidRuleConfigError: If the knobs describe a table
            ``contrai-core`` calls impossible.
    """

    if not isinstance(payload, Mapping):
        raise RecordFormatError(
            f"A ruleset is a mapping of knob to value, got {type(payload).__name__}"
        )
    hints = get_type_hints(RuleConfig)
    unknown = sorted(set(payload) - set(hints))
    if unknown:
        raise RecordFormatError(
            f"Unknown ruleset knobs: {unknown}. This build knows "
            f"{sorted(hints)}."
        )
    settings: dict[str, object] = {}
    for name, value in payload.items():
        expected = hints[name]
        if isinstance(expected, type) and issubclass(expected, Enum):
            if not isinstance(value, str):
                raise RecordFormatError(
                    f"Ruleset knob {name} is a token, got {type(value).__name__}"
                )
            try:
                settings[name] = expected(value)
            except ValueError:
                raise RecordFormatError(
                    f"Unknown token for ruleset knob {name}: {value!r}. Must be "
                    f"one of {[m.value for m in expected]}."
                ) from None
        # ``type(...) is`` rather than ``isinstance``: ``True`` is an int to
        # ``isinstance``, so a bool would slip into ``target_score`` and an
        # int into any of the nineteen switches.
        elif type(value) is not expected:
            raise RecordFormatError(
                f"Ruleset knob {name} is a {expected.__name__}, got "
                f"{type(value).__name__}"
            )
        else:
            settings[name] = value
    return RuleConfig(**settings)


# ----------------------------------------------------------------------
# Timestamps
# ----------------------------------------------------------------------


def parse_timestamp(value: object) -> str:
    """Check that ``value`` is an ISO-8601 instant in UTC, unchanged.

    Returned verbatim rather than as a :class:`~datetime.datetime`: the
    record is the archive, and re-rendering a timestamp on the way in
    would let a round-trip change bytes the producer wrote. What is
    checked is the one thing that cannot be checked later — that the
    instant carries UTC. A naive or locally-offset timestamp silently
    mis-orders events the moment two machines contribute to one corpus.

    Args:
        value: The raw ``ts`` field off a record line.

    Returns:
        ``value`` unchanged.

    Raises:
        RecordFormatError: If it is not a string, is not ISO-8601, or
            does not resolve to UTC.
    """

    if not isinstance(value, str):
        raise RecordFormatError(
            f"A timestamp is an ISO-8601 string, got {type(value).__name__}"
        )
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise RecordFormatError(
            f"Malformed timestamp: {value!r}. Expected ISO-8601 UTC, e.g. "
            f"2026-09-10T18:18:15Z."
        ) from None
    if moment.utcoffset() != timedelta(0):
        raise RecordFormatError(
            f"Timestamp {value!r} is not UTC. Records are written in UTC so "
            f"two machines' events order against each other."
        )
    return value
