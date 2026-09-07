"""Keyed wire events, grouped by round and turned into record events.

This is where the two attributions live that decide whether a record is
usable at all, and both are cases where the wrong reading produces a game
that still looks perfectly legal.

**A double's payload names the player being doubled, not the doubler.** It
repeats the bid under attack — declarer, value and trump — and adds a field
saying who attacked it. Reading the owner field as the actor credits the
double to the declarer's own side, which core would refuse on a replay only
because a side cannot double itself; read it as the *key's* actor and the
attribution is right. The same payload also repeats on every later bid, so a
double is emitted once and only once.

**The key's fourth field is the index within the trick, not the seat.** Four
plays per trick numbered 0-3 look exactly like four seats numbered 0-3, and
the mistake is invisible until a trick winner comes out wrong. The seat comes
from the player handle, through the snapshot's seat map.

One smaller rule, from the corpus: the wire's per-bid sequence numbers **have
gaps**, and ``contrai-data``'s projection refuses any sequence that is not
exactly ``1..n``, so the record renumbers them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from contrai_core import (
    Bid,
    ContractBid,
    DoubleBid,
    PassBid,
    Position,
    RedoubleBid,
)
from contrai_data import BidMade, CardPlayed

from ..exceptions import ParseError
from ..lzstring import decompress_from_base64
from ..wire import DEAL_VERB, WireEvent
from .translate import Translator


@dataclass(slots=True)
class LiveRound:
    """Everything the socket said about one round, still in wire terms."""

    number: int
    deal_stock: tuple[str, ...] = ()
    """The deck's pre-deal order, as card tokens."""

    bids: dict[int, tuple[str, object]] = field(default_factory=dict)
    """Wire sequence number to ``(actor handle, payload)``."""

    plays: dict[tuple[int, int], tuple[str, str]] = field(default_factory=dict)
    """``(trick, index within trick)`` to ``(actor handle, card token)``."""

    think: dict[tuple[int, int], int] = field(default_factory=dict)
    """Think time per play, where the event carried metadata."""

    first_ms: int | None = None
    """The server clock of the round's earliest event."""


def collect_rounds(
    events: Iterable[WireEvent], translator: Translator
) -> dict[int, LiveRound]:
    """Group keyed events by the round they belong to.

    Args:
        events: The events, already ordered.
        translator: The vocabulary layer.

    Returns:
        Round number to what the wire said about it. Events that carry no
        game key — lifecycle notices, counters — are ignored here; the
        session assembler reads those itself.
    """

    wire = translator.profile.wire
    rounds: dict[int, LiveRound] = {}
    for event in events:
        key = event.key
        if key is None or key.round is None:
            continue
        round_ = rounds.setdefault(key.round, LiveRound(number=key.round))
        if event.received_ms is not None and (
            round_.first_ms is None or event.received_ms < round_.first_ms
        ):
            round_.first_ms = event.received_ms

        if key.verb == DEAL_VERB:
            round_.deal_stock = _deal_stock(event, translator)
        elif key.verb == wire.play_verb:
            if key.trick is not None and key.position is not None and key.player:
                round_.plays[(key.trick, key.position)] = (key.player, event.data)
                think = translator.field(event.metadata, "think_ms")
                if isinstance(think, int):
                    round_.think[(key.trick, key.position)] = think
        elif key.verb.startswith(wire.bid_verb_prefix) and key.player:
            round_.bids[_bid_sequence(key.verb, wire.bid_verb_prefix, key.position)] = (
                key.player,
                event.data,
            )
    return rounds


def bid_events(
    round_: LiveRound,
    translator: Translator,
    seat_of_player: Mapping[str, Position],
    *,
    ts: str,
) -> tuple[BidMade, ...]:
    """Turn one round's auction into record events.

    Args:
        round_: The round as the wire described it.
        translator: The vocabulary layer.
        seat_of_player: Which seat each player handle sits in.
        ts: The timestamp to stamp every event with.

    Returns:
        The auction, renumbered ``1..n``.

    Raises:
        ParseError: If a payload names a value or trump the profile does not
            describe.
    """

    events: list[BidMade] = []
    seen_double = seen_redouble = False
    for wire_seq in sorted(round_.bids):
        handle, payload = round_.bids[wire_seq]
        actor = seat_of_player.get(handle)
        if actor is None:
            # A handle nobody was seen sitting in. Skipping is the only safe
            # move: a bid attributed to a guessed seat changes whose auction
            # it was.
            continue

        bid = _bid(payload, actor, translator, seen_double, seen_redouble)
        if isinstance(bid, RedoubleBid):
            seen_redouble = True
        elif isinstance(bid, DoubleBid):
            seen_double = True

        events.append(
            BidMade(
                round=round_.number,
                # Gapless, because the projection refuses anything else — and
                # the wire's own numbering does skip values.
                seq=len(events) + 1,
                position=actor,
                bid=bid,
                think_ms=None,
                ts=ts,
            )
        )
    return tuple(events)


def play_events(
    round_: LiveRound,
    translator: Translator,
    seat_of_player: Mapping[str, Position],
    *,
    ts: str,
) -> tuple[CardPlayed, ...]:
    """Turn one round's observed plays into record events.

    The eighth trick is **not** here: it never reaches the wire, and the
    session assembler adds it once the hands are known.

    Args:
        round_: The round as the wire described it.
        translator: The vocabulary layer.
        seat_of_player: Which seat each player handle sits in.
        ts: The timestamp to stamp every event with.

    Returns:
        The plays, in trick then within-trick order.

    Raises:
        ParseError: If a payload names a card the profile does not describe.
    """

    events: list[CardPlayed] = []
    for trick, index in sorted(round_.plays):
        handle, token = round_.plays[(trick, index)]
        actor = seat_of_player.get(handle)
        if actor is None:
            continue
        events.append(
            CardPlayed(
                round=round_.number,
                trick=trick,
                position=actor,
                card=translator.card(token),
                derived=False,
                think_ms=round_.think.get((trick, index)),
                ts=ts,
            )
        )
    return tuple(events)


def _bid_sequence(verb: str, prefix: str, fallback: int | None) -> int:
    """The wire's own number for a bid, off the verb.

    The verb carries it, and the key's ordering slot repeats it; the verb
    wins, because a site that stops repeating it there would otherwise
    collapse a whole auction onto one entry.
    """

    tail = verb[len(prefix) :]
    try:
        return int(tail)
    except ValueError:
        return fallback if fallback is not None else 0


def _bid(
    payload: object,
    actor: Position,
    translator: Translator,
    seen_double: bool,
    seen_redouble: bool,
) -> Bid[Position]:
    """Read one bid payload, attributing it to the key's actor."""

    tokens = translator.profile.wire.tokens
    if payload is None and tokens.pass_is_null:
        return PassBid(player=actor)
    if not isinstance(payload, Mapping):
        # Anything else is a shape the profile does not describe. Defaulting
        # it to a pass would be the worst of both worlds: the auction stays
        # legal, so nothing downstream notices, and the round is wrong.
        raise ParseError(
            f"A bid by {actor.value} arrived as {type(payload).__name__}, "
            "which the profile does not describe"
        )

    # Order matters: a redouble's payload still carries the doubler, so
    # checking for the double first would read every redouble as a double.
    if translator.field(payload, "redoubler") and not seen_redouble:
        return RedoubleBid(player=actor)
    if translator.field(payload, "doubler") and not seen_double:
        return DoubleBid(player=actor)

    suit_token = translator.field(payload, "bid_suit")
    if suit_token is None:
        raise ParseError(f"A contract bid by {actor.value} names no trump")
    return ContractBid(
        player=actor,
        value=translator.contract_value(translator.field(payload, "bid_value")),
        suit=translator.contract_suit(suit_token),
    )


def _deal_stock(event: WireEvent, translator: Translator) -> tuple[str, ...]:
    """Decompress a deal payload into the deck's pre-deal order.

    The payload is a compressed JSON document rather than a list, because it
    is the largest thing the socket sends and the client unpacks it itself.
    An undecodable one reads as no deal at all: the round then has no hands,
    and the assembler skips it rather than dealing a guess.
    """

    if not isinstance(event.data, str):
        return ()
    plain = decompress_from_base64(event.data)
    if not plain:
        return ()
    try:
        document = json.loads(plain)
    except json.JSONDecodeError:
        return ()
    order = translator.field(document, "deck_order")
    if not isinstance(order, Sequence) or isinstance(order, str):
        return ()
    return tuple(str(token) for token in order)
