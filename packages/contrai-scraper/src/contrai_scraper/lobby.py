"""The lobby's socket, read for the one thing a fleet waits on: a game starting.

The lobby screen is driven by one event per row, and the tournament's row is a
slot that fills with four players, starts, and recycles for the next four. Its
events are the only place a game's players can be seen *before* its first
card, which is what makes a game catchable from round 1 at all. The page shows
the same thing, but it shows a complete row for about a second and a half, so
reading the page every few seconds catches half the games; the socket carries
every one of them.

Three readings are wrong in ways that produce a well-formed chase for the wrong
game, and each is ruled out here rather than downstream.

**An event's seats are the whole seat map, never a change to it.** Nothing in
the stream ever vacates a seat, so adding events up leaves a ghost wherever a
player changed chairs: measured against the page, the accumulated reading
agreed 4.7% of the time and reported 104 complete rosters where the page showed
6 — and it *looks* as though it is working.

**A roster is the one the row held when its game started.** Seats change all
the time before a start — 74 changes in a measured half hour, swaps included —
and the row recycles right after, so a roster read early names somebody who
left and one read late names the next game's players. The row raises a flag of
its own in the event that carries the fourth seat's arrival, milliseconds after
it; that event, with its four seats, is the roster.

**Accounts are compared as the site spells them.** Six digits, zero-padded,
kept as strings — an account turned into a number would lose its leading zero,
and a match on anything else (the per-visit handle, the pseudonym) has already
been measured to fail.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from contrai_core import Position

from .exceptions import ProfileError
from .parse.translate import Translator
from .profile import Profile
from .wire import WireEvent

#: Seats at a table: a roster names exactly this many accounts.
SEATS: Final[int] = len(Position)

#: How many hex digits of a roster's digest a log line carries.
_DIGEST_LENGTH: Final[int] = 10


@dataclass(frozen=True, slots=True)
class LobbyRoster:
    """A tournament game's four players, as the lobby announced them starting."""

    seats: Mapping[str, str]
    """The row's placement token to the account sitting there."""

    at: float
    """When the announcing frame arrived, on the frame source's own clock."""

    received_ms: int | None
    """The server's clock on the announcing event, when it carried one."""

    @property
    def accounts(self) -> frozenset[str]:
        """The four accounts, which is what a table is matched on."""

        return frozenset(self.seats.values())

    @property
    def digest(self) -> str:
        """A short, stable name for this roster, for lines that must not carry it.

        Accounts are personal data and a health line is the thing most likely
        to leave the machine, so a roster is named by a digest of its sorted
        accounts: the same four players give the same name on every worker's
        line, and the name gives none of them away.
        """

        joined = "\n".join(sorted(self.accounts)).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()[:_DIGEST_LENGTH]


def lobby_seats(data: Any, translator: Translator) -> dict[str, str] | None:
    """One lobby event's seat map: placement to account, taken seats only.

    Args:
        data: The event's payload.
        translator: The vocabulary layer, for the lobby's field paths.

    Returns:
        The seats that hold an account, or ``None`` when the payload carries
        no seat map at all. A seat whose account is not a non-empty string
        is not counted: a number there would already have lost its leading
        zero, and comparing it would be comparing the wrong thing.
    """

    blocks = translator.field(data, "lobby_seats")
    if not isinstance(blocks, Mapping):
        return None
    seats: dict[str, str] = {}
    for placement, block in blocks.items():
        account = translator.field(block, "lobby_seat_account")
        if isinstance(account, str) and account:
            seats[placement] = account
    return seats


class LobbyWatcher:
    """Reads lobby events and announces each tournament game as it starts."""

    __slots__ = ("_translator", "_kind", "_hash", "_announced", "states")

    def __init__(self, profile: Profile, table_hash: str) -> None:
        """Watch one row.

        Args:
            profile: The loaded profile, which must describe the lobby's
                event and its fields.
            table_hash: The tournament row's hash, read off the page once —
                the socket never says which row is the tournament's.

        Raises:
            ProfileError: If the profile cannot read the lobby's socket.
        """

        if not profile.wire.has_lobby:
            raise ProfileError(
                "the profile cannot read the lobby's socket; it needs "
                "[wire.events].lobby_table and the [wire.fields] lobby paths"
            )
        self._translator = Translator(profile)
        self._kind = profile.wire.events.lobby_table
        self._hash = table_hash
        self._announced: frozenset[str] | None = None
        self.states = 0
        """How many of the tournament row's states have been read."""

    def read(self, event: WireEvent, *, at: float) -> LobbyRoster | None:
        """Take one event; say whether it started a tournament game.

        Args:
            event: An event off the socket, of any kind.
            at: When its frame arrived, on the frame source's clock.

        Returns:
            The starting roster, or ``None`` — which is the answer for every
            event but one per game.
        """

        if event.kind != self._kind:
            return None
        data = event.data
        if self._translator.field(data, "lobby_hash") != self._hash:
            return None
        self.states += 1
        # The whole map, every time: never merged into what came before.
        seats = lobby_seats(data, self._translator) or {}
        if len(seats) < SEATS:
            # The row filling, or recycling for the next four: whatever
            # starts after this is a new game, even with the same players.
            self._announced = None
            return None
        accounts = frozenset(seats.values())
        if (
            not self._translator.field(data, "lobby_full")
            or len(accounts) != SEATS
            or accounts == self._announced
        ):
            return None
        self._announced = accounts
        return LobbyRoster(seats=seats, at=at, received_ms=event.received_ms)
