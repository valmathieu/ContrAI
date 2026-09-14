"""Reading the table description the socket opens with.

When a spectator joins, the server sends one large payload describing the
table as it stands: who is sitting where, which side each of them is on, the
running totals and a per-round score breakdown. It is the only place the wire
carries that breakdown at all — the live events say what was *played*, never
what it was *worth* — so it is the scraper's only score source.

Two things about it shape everything downstream.

**It describes the last *completed* round.** Not the one in progress. The
round being watched when the spectator arrives is therefore unobservable from
its start, and the parser skips it rather than reconstructing a partial
auction. This is why an observed game's hands are always ``dealt_from_deck``
and never taken from a snapshot.

**Its team labels are per-game.** Which label is the North-South side depends
on who sat down where, so every label is resolved through the seat that holds
it. A parser that assumed the first label was always North-South would be
right about half the time and wrong silently the rest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from contrai_core import ContractSuit, Position, SlamLevel, TeamSide

from ..exceptions import ParseError
from .translate import Translator


@dataclass(frozen=True, slots=True)
class PlayerInfo:
    """One seated player, as the snapshot describes them."""

    id: str
    name: str | None
    account: str | None
    level: str | None
    """Nullable per snapshot: the same seat may report one and then none."""

    kind: str | None
    team_letter: str | None
    position: Position


@dataclass(frozen=True, slots=True)
class RowContract:
    """The contract a score row was played under."""

    value: int | SlamLevel | None
    suit: ContractSuit | None
    multiplier: int


@dataclass(frozen=True, slots=True)
class ScoreRow:
    """One completed round's score, as the scoreboard states it."""

    made: bool
    contract: RowContract
    taken: Mapping[TeamSide, int]
    """Card points per side, **as stated**.

    A side that took all eight tricks has this recorded as 250 rather than
    162. The row is stored the way the wire said it and never reconciled by
    arithmetic against the deck's 162 — the sweep is a fact about the round,
    not a discrepancy to correct.
    """

    belote: Mapping[TeamSide, int]
    marked: Mapping[TeamSide, tuple[int, int]]
    """Made points and announced points, per side."""

    marked_belote: Mapping[TeamSide, int]
    """Belote as the score sheet credited it, per side.

    A component of its own, beside the made and announced points: one
    scoreboard column is the three added together, which is what a panel
    read has to be compared against. Not the same question as
    :attr:`belote`, which is what a side *held*.
    """


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything one join snapshot said."""

    table_id: str | None
    is_tournament: bool | None
    round_index: int | None
    """The last **completed** round's number, not the one in progress."""

    seats: Mapping[str, Position]
    players: Mapping[str, PlayerInfo]
    score_rows: tuple[ScoreRow, ...]
    totals: Mapping[TeamSide, int] | None
    at: int | None


def read_snapshot(
    payload: Any, translator: Translator, *, at: int | None = None
) -> Snapshot:
    """Read one join snapshot.

    Args:
        payload: The snapshot's data block.
        translator: The vocabulary layer.
        at: The server clock the snapshot arrived on.

    Returns:
        The snapshot.

    Raises:
        ParseError: If a seat names a placement the profile does not map.
    """

    seats = _seats(payload, translator)
    players = _players(payload, translator, seats)
    seat_of_letter = {
        player.team_letter: player.position
        for player in players.values()
        if player.team_letter is not None
    }

    round_state = _round_state(payload, translator)
    round_index = None
    rows: tuple[ScoreRow, ...] = ()
    totals = None
    if round_state is not None:
        round_index = translator.field(round_state, "round_index")
        raw_rows = translator.field(round_state, "scores") or ()
        rows = tuple(
            _score_row(row, translator, seat_of_letter)
            for row in raw_rows
            if isinstance(row, Mapping)
        )
        totals = _totals(round_state, translator, seat_of_letter)

    return Snapshot(
        table_id=translator.field(payload, "table_id"),
        is_tournament=translator.field(payload, "is_tournament"),
        round_index=round_index,
        seats=seats,
        players=players,
        score_rows=rows,
        totals=totals,
        at=at,
    )


def _seats(payload: Any, translator: Translator) -> dict[str, Position]:
    """Map each player id to the seat it holds.

    A seat block without an id is skipped: an empty chair has a placement and
    nobody in it, and defaulting one would put a phantom at the table.
    """

    seats: dict[str, Position] = {}
    for seat in translator.field(payload, "seats") or ():
        if not isinstance(seat, Mapping):
            continue
        identity = translator.field(seat, "seat_id")
        placement = translator.field(seat, "seat_placement")
        if identity is None or placement is None:
            continue
        seats[identity] = translator.position(placement)
    return seats


def _players(
    payload: Any, translator: Translator, seats: Mapping[str, Position]
) -> dict[str, PlayerInfo]:
    """Read the player blocks, keyed by placement."""

    players: dict[str, PlayerInfo] = {}
    blocks = translator.field(payload, "players") or {}
    if not isinstance(blocks, Mapping):
        return players
    for placement, block in blocks.items():
        if not isinstance(block, Mapping):
            continue
        identity = translator.field(block, "player_id")
        if identity is None:
            # No id, no player. A block that is merely incomplete would be
            # worth defaulting; one with nothing to key on would collide with
            # the next such block and merge two seats into one.
            continue
        players[identity] = PlayerInfo(
            id=identity,
            name=translator.field(block, "player_name"),
            account=translator.field(block, "player_account"),
            level=translator.field(block, "player_level"),
            kind=translator.field(block, "player_kind"),
            team_letter=translator.field(block, "team"),
            position=seats.get(identity) or translator.position(placement),
        )
    return players


def _round_state(payload: Any, translator: Translator) -> Mapping[str, Any] | None:
    """Find the per-round container, which is keyed by prefix plus game id.

    There is no name to look up — the key carries the game's own id — so the
    only stable handle is the prefix the profile names.
    """

    state = translator.field(payload, "state")
    if not isinstance(state, Mapping):
        return None
    prefix = translator.profile.wire.round_state_prefix
    for key, value in state.items():
        if key.startswith(prefix) and isinstance(value, Mapping):
            return value
    return None


def _score_row(
    row: Mapping[str, Any],
    translator: Translator,
    seat_of_letter: Mapping[str, Position],
) -> ScoreRow:
    """Read one row of the per-round breakdown."""

    tokens = translator.profile.wire.tokens
    suit_token = translator.field(row, "row_suit")
    value_token = translator.field(row, "row_value")
    taken: dict[TeamSide, int] = {}
    belote: dict[TeamSide, int] = {}
    marked: dict[TeamSide, tuple[int, int]] = {}
    marked_belote: dict[TeamSide, int] = {}
    for letter in tokens.team_letters:
        side = translator.side(letter, seat_of_letter)
        block = row.get(letter) or {}
        taken[side] = translator.field(block, "side_taken") or 0
        belote[side] = translator.field(block, "side_belote") or 0
        marked[side] = (
            translator.field(block, "side_marked_made") or 0,
            translator.field(block, "side_marked_announced") or 0,
        )
        marked_belote[side] = translator.field(block, "side_marked_belote") or 0
    return ScoreRow(
        made=_made(row, translator),
        contract=RowContract(
            value=None if value_token is None
            else translator.contract_value(value_token),
            suit=None if suit_token is None else translator.contract_suit(suit_token),
            multiplier=translator.field(row, "row_multiplier") or 1,
        ),
        taken=taken,
        belote=belote,
        marked=marked,
        marked_belote=marked_belote,
    )


def _made(row: Mapping[str, Any], translator: Translator) -> bool:
    """Whether the declaring side made its contract.

    The row names the declaring side and the side that won the round, and
    comparing the two is the reading that has held on every row observed. The
    status token alone does not: a contract made by taking every trick
    carries a status of its own, which one "made" token would read as a
    failure. The status stays as the fallback for a row that does not name
    both sides.
    """

    declarer = translator.field(row, "row_declarer")
    winner = translator.field(row, "row_winner")
    if declarer is not None and winner is not None:
        return winner == declarer
    return (
        translator.field(row, "row_status")
        == translator.profile.wire.tokens.score_made
    )


def _totals(
    round_state: Mapping[str, Any],
    translator: Translator,
    seat_of_letter: Mapping[str, Position],
) -> dict[TeamSide, int] | None:
    """Read the running totals, keyed by side rather than by team label.

    Only the profile's own team letters are read. The block the totals live
    in may hold other things beside them — at the observed tables the
    per-round rows sit in it — and treating every key as a label would turn
    one of those into a team no seat holds, and lose both totals.

    Returns ``None`` rather than a partial mapping when a total is missing or
    its label cannot be placed: a total attributed to the wrong side is worse
    than no total. The record's own schema allows the absence — except at a
    mid-game join, where ``ObservedFrom`` needs both sides and the parser
    refuses it there instead.
    """

    raw = translator.field(round_state, "totals")
    if not isinstance(raw, Mapping):
        return None
    totals: dict[TeamSide, int] = {}
    for letter in translator.profile.wire.tokens.team_letters:
        value = raw.get(letter)
        if not isinstance(value, int):
            return None
        try:
            totals[translator.side(letter, seat_of_letter)] = value
        except ParseError:
            return None
    return totals
