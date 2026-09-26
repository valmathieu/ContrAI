"""One event, one JSON line — and back.

The boundary between :mod:`contrai_data.events`, which speaks domain
values, and the file on disk, which speaks ASCII tokens. Nothing else in
the package reads or writes JSON.

Two rules shape it. **The format carries a major version**, in the
header's ``format`` field: a loader that meets a major it does not
implement says so, instead of producing a pile of confusing field
errors. And **decoding is total** — every field of a line is consumed
and every field an event needs must be present, so a producer that
writes a field this build ignores, or omits one it needs, is a producer
this build refuses to read half of.

Lines are written with the event name first, so ``head`` on a record
says what each line is without scrolling, and with ``ensure_ascii``
off, so a player named *Zoé* is stored as their name rather than as
escapes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import TypeVar

from contrai_core import Card, Position, TeamSide

from .events import (
    BeloteHeld,
    BidMade,
    CardPlayed,
    ContractTerms,
    EndReason,
    GameEnded,
    GameEvent,
    GameStarted,
    HandsDerivation,
    Header,
    JoinPhase,
    ObservedFrom,
    RecordSource,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    Ruleset,
    ScoreSource,
    Seat,
    SeatKind,
    SideMark,
    SlamOutcome,
)
from .exceptions import RecordFormatError, UnsupportedFormatError
from .tokens import (
    bid_payload,
    card_token,
    contract_suit_token,
    contract_value_token,
    parse_bid,
    parse_card,
    parse_contract_suit,
    parse_contract_value,
    parse_position,
    parse_ruleset,
    parse_side,
    parse_timestamp,
    position_token,
    ruleset_payload,
    side_token,
)

#: The format family this build writes and reads.
FORMAT_FAMILY = "contrai-record"
#: The major version this build writes.
FORMAT_MAJOR = 2
#: Every major this build reads. A ``/1`` record is a ``/2`` record written
#: before the ``held`` outcome and an unknown carry existed, so it reads
#: unchanged; any other major is refused outright rather than parsed
#: field by field.
READABLE_MAJORS: frozenset[int] = frozenset({1, 2})
#: The full ``format`` string a header carries.
FORMAT = f"{FORMAT_FAMILY}/{FORMAT_MAJOR}"

_EVENT_NAMES: Mapping[type, str] = MappingProxyType(
    {
        Header: "header",
        GameStarted: "game_started",
        RoundDealt: "round_dealt",
        BidMade: "bid",
        CardPlayed: "card_played",
        BeloteHeld: "belote",
        RoundScored: "round_scored",
        GameEnded: "game_ended",
    }
)
_EVENT_TYPES: Mapping[str, type] = MappingProxyType(
    {name: cls for cls, name in _EVENT_NAMES.items()}
)

_EnumT = TypeVar("_EnumT", bound=Enum)


# ----------------------------------------------------------------------
# Encoding
# ----------------------------------------------------------------------


def _sides(mapping: Mapping[TeamSide, int]) -> dict[str, int]:
    """Spell a side-keyed mapping with token keys."""

    return {side_token(side): value for side, value in mapping.items()}


def _seat_payload(seat: Seat) -> dict[str, object]:
    """Spell one seat's occupant."""

    return {
        "id": seat.id,
        "name": seat.name,
        "account": seat.account,
        "kind": seat.kind.value,
        "level": seat.level,
    }


def _contract_payload(terms: ContractTerms) -> dict[str, object]:
    """Spell a score line's contract terms."""

    return {
        "value": contract_value_token(terms.value),
        "suit": contract_suit_token(terms.suit),
        "multiplier": terms.multiplier,
    }


def encode(event: GameEvent) -> str:
    """Render one event as a single JSON line.

    The event name is the first key, so ``head`` on a record says what
    each line is without scrolling, and non-ASCII is written as itself
    rather than escaped.

    Args:
        event: The event to write.

    Returns:
        One JSON object, with no trailing newline.

    Raises:
        RecordFormatError: If ``event`` is not one of the eight.
    """

    match event:
        case Header():
            payload: dict[str, object] = {
                "event": "header",
                "format": event.format,
                "source": event.source.value,
                "generator": event.generator,
                "game_id": event.game_id,
                "created_at": event.created_at,
            }
        case GameStarted():
            payload = {
                "event": "game_started",
                "ruleset": {
                    "preset": event.ruleset.preset,
                    "config": ruleset_payload(event.ruleset.config),
                },
                "seats": {
                    position_token(position): _seat_payload(seat)
                    for position, seat in event.seats.items()
                },
                "observed_from": (
                    None
                    if event.observed_from is None
                    else {
                        "round": event.observed_from.round,
                        "phase": event.observed_from.phase.value,
                        "totals": _sides(event.observed_from.totals),
                    }
                ),
                "ts": event.ts,
            }
        case RoundDealt():
            payload = {
                "event": "round_dealt",
                "round": event.round,
                "dealer": position_token(event.dealer),
                "hands": {
                    position_token(position): [card_token(card) for card in hand]
                    for position, hand in event.hands.items()
                },
                "hands_derivation": event.hands_derivation.value,
                "ts": event.ts,
            }
        case BidMade():
            payload = {
                "event": "bid",
                "round": event.round,
                "seq": event.seq,
                "position": position_token(event.position),
                "bid": bid_payload(event.bid),
                "think_ms": event.think_ms,
                "ts": event.ts,
            }
        case CardPlayed():
            payload = {
                "event": "card_played",
                "round": event.round,
                "trick": event.trick,
                "position": position_token(event.position),
                "card": card_token(event.card),
                "derived": event.derived,
                "think_ms": event.think_ms,
                "ts": event.ts,
            }
        case BeloteHeld():
            payload = {
                "event": "belote",
                "round": event.round,
                "position": position_token(event.position),
                "cards": [card_token(card) for card in event.cards],
                "announced": event.announced,
                "ts": event.ts,
            }
        case RoundScored():
            payload = {
                "event": "round_scored",
                "round": event.round,
                "outcome": event.outcome.value,
                "declarer": (
                    None if event.declarer is None else position_token(event.declarer)
                ),
                "contract": (
                    None if event.contract is None else _contract_payload(event.contract)
                ),
                "taken": _sides(event.taken),
                "belote": _sides(event.belote),
                "announcements": _sides(event.announcements),
                "carried_over": (
                    None if event.carried_over is None
                    else _sides(event.carried_over)
                ),
                "marked": {
                    side_token(side): {"made": mark.made, "announced": mark.announced}
                    for side, mark in event.marked.items()
                },
                "totals": None if event.totals is None else _sides(event.totals),
                "last_trick": (
                    None if event.last_trick is None else side_token(event.last_trick)
                ),
                "slam": event.slam.value,
                "source": event.source.value,
                "ts": event.ts,
            }
        case GameEnded():
            payload = {
                "event": "game_ended",
                "totals": None if event.totals is None else _sides(event.totals),
                "winner": None if event.winner is None else side_token(event.winner),
                "reason": event.reason.value,
                "ts": event.ts,
            }
        case _:
            raise RecordFormatError(
                f"Unknown event type: {type(event).__name__}"
            )
    return json.dumps(payload, ensure_ascii=False)


# ----------------------------------------------------------------------
# Decoding
# ----------------------------------------------------------------------


class _Reader:
    """Consumes a decoded line field by field, and refuses leftovers.

    Every ``take`` removes the field it read, so :meth:`done` can say
    whether the producer wrote anything this build does not know about.
    That is the check that turns a format drift into an exception at the
    first line rather than a subtly incomplete record much later.
    """

    def __init__(self, payload: Mapping[str, object], event: str) -> None:
        """Wrap one decoded line.

        Args:
            payload: The line's fields, minus its event name.
            event: The event name, for messages.
        """

        self._left = dict(payload)
        self._event = event

    def take(self, key: str) -> object:
        """Remove and return one required field.

        Args:
            key: The field name.

        Returns:
            Its raw JSON value.

        Raises:
            RecordFormatError: If the field is absent.
        """

        try:
            return self._left.pop(key)
        except KeyError:
            raise RecordFormatError(
                f"Event {self._event} is missing field {key!r}"
            ) from None

    def done(self) -> None:
        """Refuse any field this build did not consume.

        Raises:
            RecordFormatError: If unread fields remain.
        """

        if self._left:
            raise RecordFormatError(
                f"Event {self._event} carries fields this build does not "
                f"know: {sorted(self._left)}"
            )


def _int(reader: _Reader, key: str) -> int:
    """Read a required integer field.

    ``type(...) is int`` rather than ``isinstance``: a JSON ``true`` is
    an ``int`` to ``isinstance`` and would silently become round 1.

    Args:
        reader: The line being consumed.
        key: The field name.

    Returns:
        The integer.

    Raises:
        RecordFormatError: If the value is not exactly an ``int``.
    """

    value = reader.take(key)
    if type(value) is not int:
        raise RecordFormatError(
            f"Field {key!r} is a whole number, got {type(value).__name__}"
        )
    return value


def _opt_int(reader: _Reader, key: str) -> int | None:
    """Read an integer field that may be ``null``.

    Args:
        reader: The line being consumed.
        key: The field name.

    Returns:
        The integer, or ``None``.

    Raises:
        RecordFormatError: If the value is neither ``null`` nor an ``int``.
    """

    value = reader.take(key)
    if value is None:
        return None
    if type(value) is not int:
        raise RecordFormatError(
            f"Field {key!r} is a whole number or null, got "
            f"{type(value).__name__}"
        )
    return value


def _bool(reader: _Reader, key: str) -> bool:
    """Read a required boolean field.

    Args:
        reader: The line being consumed.
        key: The field name.

    Returns:
        The boolean.

    Raises:
        RecordFormatError: If the value is not exactly a ``bool``.
    """

    value = reader.take(key)
    if type(value) is not bool:
        raise RecordFormatError(
            f"Field {key!r} is true or false, got {type(value).__name__}"
        )
    return value


def _opt_bool(reader: _Reader, key: str) -> bool | None:
    """Read a boolean field that may be ``null``.

    Args:
        reader: The line being consumed.
        key: The field name.

    Returns:
        The boolean, or ``None``.

    Raises:
        RecordFormatError: If the value is neither ``null`` nor a ``bool``.
    """

    value = reader.take(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise RecordFormatError(
            f"Field {key!r} is true, false or null, got {type(value).__name__}"
        )
    return value


def _str(reader: _Reader, key: str) -> str:
    """Read a required string field.

    Args:
        reader: The line being consumed.
        key: The field name.

    Returns:
        The string.

    Raises:
        RecordFormatError: If the value is not a string.
    """

    value = reader.take(key)
    if not isinstance(value, str):
        raise RecordFormatError(
            f"Field {key!r} is a string, got {type(value).__name__}"
        )
    return value


def _opt_str(value: object, key: str) -> str | None:
    """Check an already-read value is a string or ``null``.

    Args:
        value: The raw value.
        key: The field name, for the message.

    Returns:
        The string, or ``None``.

    Raises:
        RecordFormatError: If it is neither.
    """

    if value is None or isinstance(value, str):
        return value
    raise RecordFormatError(
        f"Field {key!r} is a string or null, got {type(value).__name__}"
    )


def _enum(value: object, enum_cls: type[_EnumT], key: str) -> _EnumT:
    """Read a closed-vocabulary token.

    Args:
        value: The raw token.
        enum_cls: The vocabulary it must belong to.
        key: The field name, for the message.

    Returns:
        The member.

    Raises:
        RecordFormatError: If the token is not one of the vocabulary's.
    """

    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    raise RecordFormatError(
        f"Unknown token for field {key!r}: {value!r}. Must be one of "
        f"{[m.value for m in enum_cls]}."
    )


def _mapping(value: object, key: str) -> Mapping[str, object]:
    """Check a value is a JSON object.

    Args:
        value: The raw value.
        key: The field name, for the message.

    Returns:
        It, as a mapping.

    Raises:
        RecordFormatError: If it is not a mapping.
    """

    if not isinstance(value, Mapping):
        raise RecordFormatError(
            f"Field {key!r} is an object, got {type(value).__name__}"
        )
    return value


def _side_ints(value: object, key: str) -> dict[TeamSide, int]:
    """Read a side-keyed mapping of whole numbers.

    Args:
        value: The raw mapping.
        key: The field name, for the message.

    Returns:
        The mapping, keyed by :class:`TeamSide`.

    Raises:
        RecordFormatError: If it is not a mapping, a key is not a side
            token, or a value is not a whole number.
    """

    raw = _mapping(value, key)
    result: dict[TeamSide, int] = {}
    for token, amount in raw.items():
        if type(amount) is not int:
            raise RecordFormatError(
                f"Field {key!r} holds whole numbers, got "
                f"{type(amount).__name__} for {token!r}"
            )
        result[parse_side(token)] = amount
    return result


def _opt_side_ints(value: object, key: str) -> dict[TeamSide, int] | None:
    """Read a side-keyed mapping of whole numbers that may be ``null``.

    Args:
        value: The raw mapping, or ``None``.
        key: The field name, for the message.

    Returns:
        The mapping, or ``None``.

    Raises:
        RecordFormatError: If it is neither ``null`` nor a well-formed
            side-keyed mapping.
    """

    if value is None:
        return None
    return _side_ints(value, key)


def _decode_header(reader: _Reader) -> Header:
    """Build a :class:`Header`, checking the format version first."""

    header = Header(
        format=_check_format(reader.take("format")),
        source=_enum(reader.take("source"), RecordSource, "source"),
        generator=_str(reader, "generator"),
        game_id=_str(reader, "game_id"),
        created_at=parse_timestamp(reader.take("created_at")),
    )
    reader.done()
    return header


def _decode_game_started(reader: _Reader) -> GameStarted:
    """Build a :class:`GameStarted`."""

    raw_ruleset = _mapping(reader.take("ruleset"), "ruleset")
    if "preset" not in raw_ruleset or "config" not in raw_ruleset:
        raise RecordFormatError(
            "Field 'ruleset' names a preset and a config, got "
            f"{sorted(raw_ruleset)}"
        )
    ruleset = Ruleset(
        preset=_opt_str(raw_ruleset["preset"], "ruleset.preset") or "",
        config=parse_ruleset(raw_ruleset["config"]),
    )

    seats: dict[Position, Seat] = {}
    for token, raw_seat in _mapping(reader.take("seats"), "seats").items():
        entry = _mapping(raw_seat, "seat")
        missing = {"id", "name", "account", "kind", "level"} - set(entry)
        if missing:
            raise RecordFormatError(
                f"A seat is missing fields {sorted(missing)}"
            )
        seats[parse_position(token)] = Seat(
            id=_opt_str(entry["id"], "id"),
            name=_opt_str(entry["name"], "name") or "",
            account=_opt_str(entry["account"], "account"),
            kind=_enum(entry["kind"], SeatKind, "kind"),
            level=_opt_str(entry["level"], "level"),
        )

    raw_join = reader.take("observed_from")
    if raw_join is None:
        observed_from = None
    else:
        join = _mapping(raw_join, "observed_from")
        missing = {"round", "phase", "totals"} - set(join)
        if missing:
            raise RecordFormatError(
                f"Field 'observed_from' is missing {sorted(missing)}"
            )
        if type(join["round"]) is not int:
            raise RecordFormatError(
                f"Field 'observed_from.round' is a whole number, got "
                f"{type(join['round']).__name__}"
            )
        observed_from = ObservedFrom(
            round=join["round"],
            phase=_enum(join["phase"], JoinPhase, "observed_from.phase"),
            totals=_side_ints(join["totals"], "observed_from.totals"),
        )

    started = GameStarted(
        ruleset=ruleset,
        seats=seats,
        observed_from=observed_from,
        ts=parse_timestamp(reader.take("ts")),
    )
    reader.done()
    return started


def _decode_round_dealt(reader: _Reader) -> RoundDealt:
    """Build a :class:`RoundDealt`."""

    number = _int(reader, "round")
    dealer = parse_position(reader.take("dealer"))
    hands: dict[Position, tuple[Card, ...]] = {}
    for token, raw_hand in _mapping(reader.take("hands"), "hands").items():
        if not isinstance(raw_hand, list):
            raise RecordFormatError(
                f"A hand is a list of card tokens, got "
                f"{type(raw_hand).__name__}"
            )
        hands[parse_position(token)] = tuple(parse_card(c) for c in raw_hand)
    dealt = RoundDealt(
        round=number,
        dealer=dealer,
        hands=hands,
        hands_derivation=_enum(
            reader.take("hands_derivation"), HandsDerivation, "hands_derivation"
        ),
        ts=parse_timestamp(reader.take("ts")),
    )
    reader.done()
    return dealt


def _decode_bid(reader: _Reader) -> BidMade:
    """Build a :class:`BidMade`."""

    number = _int(reader, "round")
    seq = _int(reader, "seq")
    position = parse_position(reader.take("position"))
    made = BidMade(
        round=number,
        seq=seq,
        position=position,
        bid=parse_bid(reader.take("bid"), position),
        think_ms=_opt_int(reader, "think_ms"),
        ts=parse_timestamp(reader.take("ts")),
    )
    reader.done()
    return made


def _decode_card_played(reader: _Reader) -> CardPlayed:
    """Build a :class:`CardPlayed`."""

    played = CardPlayed(
        round=_int(reader, "round"),
        trick=_int(reader, "trick"),
        position=parse_position(reader.take("position")),
        card=parse_card(reader.take("card")),
        derived=_bool(reader, "derived"),
        think_ms=_opt_int(reader, "think_ms"),
        ts=parse_timestamp(reader.take("ts")),
    )
    reader.done()
    return played


def _decode_belote(reader: _Reader) -> BeloteHeld:
    """Build a :class:`BeloteHeld`."""

    number = _int(reader, "round")
    position = parse_position(reader.take("position"))
    raw_cards = reader.take("cards")
    if not isinstance(raw_cards, list):
        raise RecordFormatError(
            f"Field 'cards' is a list of card tokens, got "
            f"{type(raw_cards).__name__}"
        )
    held = BeloteHeld(
        round=number,
        position=position,
        cards=tuple(parse_card(c) for c in raw_cards),
        announced=_opt_bool(reader, "announced"),
        ts=parse_timestamp(reader.take("ts")),
    )
    reader.done()
    return held


def _decode_round_scored(reader: _Reader) -> RoundScored:
    """Build a :class:`RoundScored`."""

    number = _int(reader, "round")
    outcome = _enum(reader.take("outcome"), RoundOutcome, "outcome")
    raw_declarer = reader.take("declarer")
    declarer = None if raw_declarer is None else parse_position(raw_declarer)

    raw_contract = reader.take("contract")
    if raw_contract is None:
        contract = None
    else:
        terms = _mapping(raw_contract, "contract")
        missing = {"value", "suit", "multiplier"} - set(terms)
        if missing:
            raise RecordFormatError(
                f"Field 'contract' is missing {sorted(missing)}"
            )
        if type(terms["multiplier"]) is not int:
            raise RecordFormatError(
                f"Field 'contract.multiplier' is a whole number, got "
                f"{type(terms['multiplier']).__name__}"
            )
        contract = ContractTerms(
            value=parse_contract_value(terms["value"]),
            suit=parse_contract_suit(terms["suit"]),
            multiplier=terms["multiplier"],
        )

    marked: dict[TeamSide, SideMark] = {}
    for token, raw_mark in _mapping(reader.take("marked"), "marked").items():
        mark = _mapping(raw_mark, "marked")
        missing = {"made", "announced"} - set(mark)
        if missing:
            raise RecordFormatError(
                f"Field 'marked' is missing {sorted(missing)} for {token!r}"
            )
        for part in ("made", "announced"):
            if type(mark[part]) is not int:
                raise RecordFormatError(
                    f"Field 'marked.{part}' is a whole number, got "
                    f"{type(mark[part]).__name__}"
                )
        marked[parse_side(token)] = SideMark(
            made=mark["made"], announced=mark["announced"]
        )

    raw_last = reader.take("last_trick")
    scored = RoundScored(
        round=number,
        outcome=outcome,
        declarer=declarer,
        contract=contract,
        taken=_side_ints(reader.take("taken"), "taken"),
        belote=_side_ints(reader.take("belote"), "belote"),
        announcements=_side_ints(reader.take("announcements"), "announcements"),
        carried_over=_opt_side_ints(reader.take("carried_over"), "carried_over"),
        marked=marked,
        totals=_opt_side_ints(reader.take("totals"), "totals"),
        last_trick=None if raw_last is None else parse_side(raw_last),
        slam=_enum(reader.take("slam"), SlamOutcome, "slam"),
        source=_enum(reader.take("source"), ScoreSource, "source"),
        ts=parse_timestamp(reader.take("ts")),
    )
    reader.done()
    return scored


def _decode_game_ended(reader: _Reader) -> GameEnded:
    """Build a :class:`GameEnded`.

    ``ts`` is the one timestamp in the format that may be ``null``: a
    game we walked away from has no wire instant for its end.
    """

    totals = _opt_side_ints(reader.take("totals"), "totals")
    raw_winner = reader.take("winner")
    reason = _enum(reader.take("reason"), EndReason, "reason")
    raw_ts = reader.take("ts")
    ended = GameEnded(
        totals=totals,
        winner=None if raw_winner is None else parse_side(raw_winner),
        reason=reason,
        ts=None if raw_ts is None else parse_timestamp(raw_ts),
    )
    reader.done()
    return ended


_DECODERS = MappingProxyType(
    {
        "header": _decode_header,
        "game_started": _decode_game_started,
        "round_dealt": _decode_round_dealt,
        "bid": _decode_bid,
        "card_played": _decode_card_played,
        "belote": _decode_belote,
        "round_scored": _decode_round_scored,
        "game_ended": _decode_game_ended,
    }
)


def _check_format(value: object) -> str:
    """Accept only a format string this build implements.

    Args:
        value: The header's raw ``format`` field.

    Returns:
        ``value`` unchanged.

    Raises:
        RecordFormatError: If it is not a ``family/major`` string with a
            numeric major.
        UnsupportedFormatError: If the family is not this one, or the
            major is not in :data:`READABLE_MAJORS`.
    """

    if not isinstance(value, str) or value.count("/") != 1:
        raise RecordFormatError(
            f"Malformed format: {value!r}. Expected {FORMAT!r}."
        )
    family, _, major = value.partition("/")
    if not major.isdigit():
        raise RecordFormatError(
            f"Malformed format: {value!r}. The major version must be a "
            f"number, e.g. {FORMAT!r}."
        )
    if family != FORMAT_FAMILY:
        raise UnsupportedFormatError(
            f"Unknown record format {value!r}. This build reads "
            f"{FORMAT_FAMILY!r} records."
        )
    if int(major) not in READABLE_MAJORS:
        readable = ", ".join(str(m) for m in sorted(READABLE_MAJORS))
        raise UnsupportedFormatError(
            f"Record format major {major} is not readable by this build, "
            f"which reads majors {readable}."
        )
    return value


def decode(line: str) -> GameEvent:
    """Read one JSON line back into its event.

    Args:
        line: One line of a record, without its newline.

    Returns:
        The event it spells.

    Raises:
        RecordFormatError: If the line is not a JSON object, names no
            event or an unknown one, omits a field the event needs,
            carries a field this build does not know, or holds a token
            that does not parse.
        UnsupportedFormatError: If a header names a format major this
            build does not implement.
    """

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        raise RecordFormatError(f"Line is not JSON: {line[:60]!r}") from None
    if not isinstance(payload, dict):
        raise RecordFormatError(
            f"A record line is a JSON object, got {type(payload).__name__}"
        )
    fields = dict(payload)
    name = fields.pop("event", None)
    if name is None:
        raise RecordFormatError(
            f"A record line names its event, got fields {sorted(fields)}"
        )
    if name not in _DECODERS:
        raise RecordFormatError(
            f"Unknown event: {name!r}. Must be one of {sorted(_EVENT_TYPES)}."
        )
    return _DECODERS[name](_Reader(fields, name))
