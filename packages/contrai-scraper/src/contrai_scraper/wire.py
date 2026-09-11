"""Turns socket frames into a de-duplicated stream of game events.

Three properties of the traffic shape everything here.

It is **double-wrapped**: the frame is JSON whose ``data`` is itself a JSON
*string*, so the payload is parsed twice. It is **mirrored**: the page keeps a
second connection that repeats every event, so half of what arrives is a
duplicate. And it is **interleaved with a keepalive that is not JSON at all**,
so a parser that assumes every frame is a document raises roughly once a
second on a busy socket.

Nothing in this module knows what the site is called. The envelope's
discriminator, the keepalive's text and the shape of the composite key all
come from the profile's ``[wire]`` section; what is spelled out here is only
the *structure* — that there is an envelope, that events carry a key, that a
key addresses a round, a trick and a seat.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .profile import WireSection

#: The verb a deal is given. Deal keys carry no verb of their own — they are
#: recognised by their shape — so the parser supplies a logical one rather
#: than leaving downstream code to test the key's length again.
DEAL_VERB = "deal"

#: The key fields read as integers. A key whose round, trick or seat is not a
#: number is not a game key, which is a normal thing for a frame to be.
_NUMERIC_FIELDS = ("round", "trick", "position")

#: The key fields that identify *which* deal, as opposed to the slots a deal
#: key fills with zeros.
_IDENTIFYING_FIELDS = ("game", "round")


@dataclass(frozen=True, slots=True)
class EventKey:
    """The composite key an in-game event is addressed by."""

    game: str
    round: int | None
    trick: int | None
    position: int | None
    verb: str
    player: str | None


@dataclass(frozen=True, slots=True)
class WireEvent:
    """One game event, unwrapped from its envelope."""

    kind: str
    """The inner event name: a composite key, or a lifecycle event's name."""

    key: EventKey | None
    """The parsed key, or ``None`` when ``kind`` is not a composite key."""

    data: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)
    received_ms: int | None = None
    """The server's clock, absent on events that carry no metadata block."""

    frame_id: str | None = None
    socket: int = 0


def dig(payload: Any, path: str) -> Any:
    """Walk a dotted path into a nested payload.

    Args:
        payload: The mapping to walk.
        path: Dot-separated segments, as a profile spells a field.

    Returns:
        The value at the path, or ``None`` if any segment is missing. A
        missing field is normal — the same event carries different blocks at
        different points in a game — so this is not an error.
    """

    current = payload
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
        if current is None:
            return None
    return current


def parse_key(kind: str, wire: WireSection) -> EventKey | None:
    """Read an event name as a composite key.

    Args:
        kind: The inner event name.
        wire: The profile's wire section, which owns the field order.

    Returns:
        The parsed key, or ``None`` when the name is not one — a lifecycle
        event, or a key whose numeric slots do not hold numbers. Both are
        ordinary traffic, so neither raises.
    """

    parts = kind.split(",")
    names = wire.key_fields
    if len(parts) == wire.deal_key_arity:
        fields = dict(zip(names[: wire.deal_key_arity], parts, strict=True))
        # A deal is addressed by its round alone, and the remaining slots of
        # the short key are filled with zeros rather than left out. Anything
        # else with this many parts is not a deal.
        padding = [
            name
            for name in names[: wire.deal_key_arity]
            if name not in _IDENTIFYING_FIELDS
        ]
        if any(fields[name] != "0" for name in padding):
            return None
        fields["verb"] = DEAL_VERB
    elif len(parts) == len(names):
        fields = dict(zip(names, parts, strict=True))
    else:
        return None

    numbers: dict[str, int | None] = {}
    for name in _NUMERIC_FIELDS:
        raw = fields.get(name)
        if raw is None:
            numbers[name] = None
            continue
        try:
            numbers[name] = int(raw)
        except ValueError:
            return None

    return EventKey(
        game=fields["game"],
        round=numbers["round"],
        trick=numbers["trick"],
        position=numbers["position"],
        verb=fields["verb"],
        player=fields.get("player"),
    )


def unwrap(text: str, *, socket: int, wire: WireSection) -> WireEvent | None:
    """Unwrap one frame into a game event.

    Args:
        text: The frame's raw text.
        socket: Which connection it arrived on, kept for the raw log.
        wire: The profile's wire section.

    Returns:
        The event, or ``None`` for a keepalive, a non-JSON frame, or a frame
        of some other kind. None of those is an error: they are the majority
        of what a socket carries.
    """

    if text == wire.keepalive_frame:
        return None
    outer = _loads(text)
    if not isinstance(outer, Mapping):
        return None
    if outer.get("event") != wire.game_envelope_kind:
        return None

    inner = _inner(outer)
    if inner is None:
        return None
    kind = inner.get("event")
    if not isinstance(kind, str):
        return None

    metadata = inner.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    received = dig(metadata, wire.fields["received_ms"])
    frame_id = outer.get("id")

    return WireEvent(
        kind=kind,
        key=parse_key(kind, wire),
        data=inner.get("data"),
        metadata=metadata,
        received_ms=received if isinstance(received, int) else None,
        frame_id=None if frame_id is None else str(frame_id),
        socket=socket,
    )


def duplicate_key(text: str) -> str | None:
    """The identity a frame is de-duplicated on.

    Public, and taking raw text rather than a :class:`WireEvent`, because the
    raw log de-duplicates through exactly this function: two implementations
    of "the same frame" would drift, and the drift would show up as a record
    that is missing a card or holds it twice.

    Args:
        text: The frame's raw text.

    Returns:
        The frame's own id when it has one, else a canonical rendering of the
        inner event, or ``None`` when the text is not a frame at all.
    """

    outer = _loads(text)
    if not isinstance(outer, Mapping):
        return None
    frame_id = outer.get("id")
    if frame_id is not None:
        return str(frame_id)
    inner = _inner(outer)
    if inner is None:
        return None
    return json.dumps(
        [inner.get("event"), inner.get("data")], sort_keys=True, ensure_ascii=False
    )


def order_events(events: Iterable[WireEvent]) -> tuple[WireEvent, ...]:
    """Sort events by the server's clock, keeping arrival order otherwise.

    Args:
        events: The events, in arrival order.

    Returns:
        The same events, ordered. Events with no clock sort *after* every
        clocked one and keep their arrival order among themselves — the
        alternative, treating a missing clock as zero, files every lifecycle
        event at the start of the game.
    """

    indexed = list(enumerate(events))
    indexed.sort(
        key=lambda pair: (
            pair[1].received_ms is None,
            pair[1].received_ms or 0,
            pair[0],
        )
    )
    return tuple(event for _, event in indexed)


class WireStream:
    """Ingests frames, dropping keepalives and the mirrored socket's copies.

    The counters are not decoration: a run whose ``deduped`` count is not
    roughly half its ``received`` count is a run where one of the two
    connections dropped, and that is the difference between a complete game
    and a game missing a trick.
    """

    __slots__ = ("_wire", "_seen", "received", "deduped", "skipped")

    def __init__(self, wire: WireSection) -> None:
        self._wire = wire
        self._seen: set[str] = set()
        self.received = 0
        """Frames that were game events, before de-duplication."""

        self.deduped = 0
        """Events dropped because the other connection already carried them."""

        self.skipped = 0
        """Frames that were not game events at all — keepalives and the rest."""

    def ingest(self, text: str, socket: int) -> WireEvent | None:
        """Take one frame.

        Args:
            text: The frame's raw text.
            socket: Which connection it arrived on.

        Returns:
            The event, or ``None`` when the frame was not one or was a copy
            of one already seen.
        """

        event = unwrap(text, socket=socket, wire=self._wire)
        if event is None:
            self.skipped += 1
            return None
        self.received += 1
        identity = duplicate_key(text)
        if identity in self._seen:
            self.deduped += 1
            return None
        self._seen.add(identity)
        return event


def _loads(text: str) -> Any:
    """Parse JSON, treating "not JSON" as a value rather than an error."""

    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _inner(outer: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Unwrap the second envelope, whose payload is JSON inside a string."""

    payload = outer.get("data")
    if isinstance(payload, str):
        payload = _loads(payload)
    return payload if isinstance(payload, Mapping) else None
