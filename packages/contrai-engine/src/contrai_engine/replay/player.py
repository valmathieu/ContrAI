"""The seat that plays from a record instead of from a strategy.

:class:`RecordedPlayer` is a real :class:`~contrai_engine.model.player.Player`
— the engine cannot tell it from an AI — whose two decision hooks read
the round's recorded actions instead of deciding anything.

**Actions are addressed by position in the round, never by a per-seat
queue.** That is the one design decision in this module worth spelling
out, because the obvious alternative is wrong. The engine auto-applies a
bid whenever a seat has exactly one legal action, *without consulting the
seat* (see ``Round.manage_bidding``), and a record holds those forced
passes like any other bid. A seat popping its own queue would therefore
fall one behind the moment it was skipped — and a seat can be skipped and
then consulted again in the same auction: the partner of a Slam bidder
has only Pass until an opponent doubles, and gains the redouble the
moment one does.

Indexing off ``len(auction.bids)`` sidesteps all of it. The auto-applied
bids advance that counter exactly as consulted ones do, so the script and
the engine stay in step by construction, and the seat check below turns
any disagreement into an error rather than a silent swap.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from contrai_core.bid import Bid
from contrai_core.position import Position

from ..model.player.base import Player
from ..model.player.rationale import BidDecision, CardDecision, Rationale
from .exceptions import ScriptExhaustedError, SeatMismatchError

if TYPE_CHECKING:
    from contrai_core.auction import Auction
    from contrai_core.card import Card
    from contrai_core.play import ObservedPlay, PlayObservation
    from contrai_data import RoundRecord


#: What a replayed decision says when asked why. A record carries the
#: action, never the reasoning behind it — the seat that made it may not
#: even have been an engine — so the rationale names the source instead of
#: inventing a rule that fired.
_REPLAY_RULE = "recorded action"


@dataclass(frozen=True, slots=True)
class RoundScript:
    """One round's recorded actions, addressed by position in the round.

    Attributes:
        number: The round number the script came from, for messages.
        bids: The round's bids in auction order, seated on bare
            :class:`~contrai_core.Position` values.
        plays: The round's cards in play order, flattened across tricks —
            trick boundaries are a consequence of the play order, not an
            input to it, so the replay does not need them here.
    """

    number: int
    bids: tuple[Bid[Position], ...] = ()
    plays: tuple["ObservedPlay", ...] = field(default=())

    @classmethod
    def of(cls, round_: "RoundRecord") -> "RoundScript":
        """Script the actions of one recorded round.

        The trailing partial trick is included: a round abandoned
        mid-trick still played the cards it played, and refusing to hand
        them over would turn a short round into a wrong one.

        Args:
            round_: The round to script.

        Returns:
            Its bids and its cards, each in the order they happened.
        """

        # A ``TrickRecord`` *is* its four plays — it subclasses tuple — so
        # extending from it directly is the whole flattening.
        plays: list["ObservedPlay"] = []
        for trick in round_.tricks:
            plays.extend(trick)
        plays.extend(round_.current_trick)
        return cls(number=round_.number, bids=round_.auction, plays=tuple(plays))

    def bid_at(self, index: int, seat: Position) -> Bid[Position]:
        """The recorded bid at ``index``, checked against ``seat``.

        Args:
            index: How many bids the replayed auction already holds —
                which is the 0-based position of the bid now due.
            seat: The seat the engine is consulting.

        Returns:
            The recorded bid, still seated on its :class:`Position`.

        Raises:
            ScriptExhaustedError: If the auction ran past the record.
            SeatMismatchError: If the record's bid at ``index`` belongs
                to another seat.
        """

        if index >= len(self.bids):
            raise ScriptExhaustedError(
                f"Round {self.number}: {seat} was asked for bid "
                f"{index + 1}, but the record holds {len(self.bids)}"
            )
        bid = self.bids[index]
        if bid.player is not seat:
            raise SeatMismatchError(
                f"Round {self.number}: bid {index + 1} is {bid.player}'s in "
                f"the record, but the engine consulted {seat}"
            )
        return bid

    def play_at(self, index: int, seat: Position) -> "Card":
        """The recorded card at ``index``, checked against ``seat``.

        Args:
            index: How many cards the replayed round already played —
                the 0-based position of the play now due.
            seat: The seat the engine is consulting.

        Returns:
            The card that seat played.

        Raises:
            ScriptExhaustedError: If the round ran past the record.
            SeatMismatchError: If the record's play at ``index`` belongs
                to another seat.
        """

        if index >= len(self.plays):
            raise ScriptExhaustedError(
                f"Round {self.number}: {seat} was asked for play "
                f"{index + 1}, but the record holds {len(self.plays)}"
            )
        play = self.plays[index]
        if play.position is not seat:
            raise SeatMismatchError(
                f"Round {self.number}: play {index + 1} is {play.position}'s "
                f"in the record, but the engine consulted {seat}"
            )
        return play.card


class RecordedPlayer(Player):
    """A seat whose every decision was made once already, and written down.

    The engine drives it exactly as it drives an ``AiPlayer``: it asks
    for a bid, it asks for a card, and it validates both through
    :meth:`~contrai_core.Auction.apply` and
    :meth:`~contrai_core.PlayState.apply`. Nothing here checks legality —
    handing the recorded action to the real rules and letting them object
    *is* the verification.

    Attributes:
        script: The round currently being replayed, or ``None`` between
            rounds. The controller re-points it before each round rather
            than rebuilding the seats, so the four players — and the
            teams the game wired onto them — survive the whole game.
    """

    def __init__(self, name: str, position: Position) -> None:
        """Seat a recorded player.

        Args:
            name: The occupant's name, as the record gives it.
            position: The seat, which is also the key its recorded
                actions are addressed by. Required, unlike
                :class:`~contrai_core.BasePlayer`'s optional seat: a seat
                with no position has no actions to look up.
        """

        super().__init__(name, position)
        self.script: RoundScript | None = None

    def _due(self) -> RoundScript:
        """The script for the round in progress.

        Returns:
            The current script.

        Raises:
            ScriptExhaustedError: If the engine asked for an action
                outside any round — a controller that forgot to point
                the seat at a script.
        """

        if self.script is None:
            raise ScriptExhaustedError(
                f"{self.position} was consulted with no round scripted"
            )
        return self.script

    def choose_bid(self, auction: "Auction") -> Optional[BidDecision]:
        """Hand back the bid the record has for this point in the auction.

        The bid is re-seated from the record's bare
        :class:`~contrai_core.Position` onto this player, because
        :meth:`~contrai_core.Auction.apply` and everything downstream of
        it — the contract, the doubling side, the scorer — read a live
        player off the bid.

        Args:
            auction: The auction so far. Its length is the address of the
                bid now due.

        Returns:
            The recorded bid, re-seated, with a rationale naming the
            record as its source.

        Raises:
            ScriptExhaustedError: If the auction ran past the record.
            SeatMismatchError: If that bid was another seat's.
        """

        recorded = self._due().bid_at(len(auction.bids), self.position)
        return BidDecision(
            bid=dataclasses.replace(recorded, player=self),
            rationale=Rationale(
                rule=_REPLAY_RULE,
                detail=f"{self.position} bid {recorded} in the record",
            ),
        )

    def choose_card(
        self, observation: "PlayObservation"
    ) -> Optional[CardDecision]:
        """Hand back the card the record has for this point in the round.

        Args:
            observation: This seat's view of the play phase. The cards
                already played — four per completed trick plus the trick
                in progress — are the address of the play now due.

        Returns:
            The recorded card, with a rationale naming the record as its
            source. The card object is the record's own; the play state
            matches by value, so identity is not load-bearing here.

        Raises:
            ScriptExhaustedError: If the round ran past the record.
            SeatMismatchError: If that play was another seat's.
        """

        played = 4 * len(observation.completed_tricks) + len(
            observation.current_trick
        )
        card = self._due().play_at(played, self.position)
        return CardDecision(
            card=card,
            rationale=Rationale(
                rule=_REPLAY_RULE,
                detail=f"{self.position} played {card} in the record",
            ),
        )
