"""A flat event stream, folded back into rounds.

The record stores what *happened* and nothing that follows from it. The
contract, the completed tricks, who won each of them and whether a round
was passed out are all recomputed here, through ``contrai-core``'s own
:class:`~contrai_core.Auction` and :class:`~contrai_core.TrickRecord`,
because a derived fact stored twice is a fact that can disagree with
itself — and a record disagreeing with its own derivations is a corpus
nobody can trust.

Two ordering rules matter, and they are not the same rule.

**Rounds keep file order.** Round numbers are the source's deal count, so
they are monotonic but need not start at one and need not be contiguous:
a spectator joining a game in progress records from the next deal, and
the round they walked in on is skipped entirely. Nothing here does
arithmetic on a round number.

**Events attach to a round by number.** The observed state snapshot
describes the last *completed* round, so a score read while round N+1 is
being played carries round N — and therefore arrives in the file after
round N+1's deal. Attaching by "the round currently being built" would
file it under the wrong round, with nothing in the file looking wrong.

The projection checks structure, never legality. Whether a card could
lawfully be played is ``PlayState``'s question, and whether a whole
observed round obeys the rules is the verifier's; this layer only asks
whether the events can be folded into rounds at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from contrai_core import (
    Auction,
    Bid,
    Card,
    ContractSuit,
    ObservedContract,
    ObservedPlay,
    Position,
    RuleConfig,
    TrickRecord,
)

from .events import (
    BeloteHeld,
    BidMade,
    CardPlayed,
    GameEnded,
    GameEvent,
    GameStarted,
    HandsDerivation,
    Header,
    ObservedFrom,
    RoundDealt,
    RoundOutcome,
    RoundScored,
    Seat,
)
from .exceptions import RecordFormatError
from .store import read_events


@dataclass(frozen=True, slots=True)
class RoundRecord:
    """One round of a record, with its derived facts recomputed.

    Attributes:
        number: The source's deal count for this round.
        dealer: The seat that dealt.
        hands: Each seat's eight cards, as dealt.
        hands_derivation: How those hands were arrived at.
        auction: The round's bids, in order, seated on bare positions.
        contract: The contract the auction settled on, or ``None`` when
            the round was passed out. Derived, never stored.
        tricks: The completed tricks, in play order.
        derived_tricks: One flag per completed trick — whether that trick
            was reconstructed rather than observed.
        current_trick: The trailing partial trick when a round was
            abandoned mid-trick; empty otherwise.
        belotes: The King-and-Queen pairs reported in this round.
        score: The round's score line, or ``None`` when no source covered
            it.
        outcome: How the round resolved. ``None`` for a contracted round
            with no score — made or failed is a scoring question.
        complete: Whether the round's structure finished: the auction
            closed, and either it was passed out with no cards played or
            all eight tricks were played out. A score is **not** part of
            this.
    """

    number: int
    dealer: Position
    hands: Mapping[Position, tuple[Card, ...]]
    hands_derivation: HandsDerivation
    auction: tuple[Bid[Position], ...]
    contract: ObservedContract | None
    tricks: tuple[TrickRecord[ObservedPlay], ...]
    derived_tricks: tuple[bool, ...]
    current_trick: tuple[ObservedPlay, ...]
    belotes: tuple[BeloteHeld, ...]
    score: RoundScored | None
    outcome: RoundOutcome | None
    complete: bool

    @property
    def trump_suit(self) -> ContractSuit | None:
        """The round's trump, or ``None`` when it was passed out."""

        return self.contract.suit if self.contract else None

    @property
    def trick_winners(self) -> tuple[Position, ...]:
        """The seat that won each completed trick, in play order.

        The winner rule is core's — the same
        :meth:`~contrai_core.TrickRecord.winner` the engine's live play
        path calls — so a replayed record and a played game can never
        disagree about who took a trick. A no-trump or contract-less
        round passes ``None`` through to ``rules_for``, which degrades
        every trump branch to the plain follow-suit rule.

        Returns:
            One seat per completed trick; empty for a passed-out round.
        """

        return tuple(
            trick.winner(self.trump_suit).position for trick in self.tricks
        )


@dataclass(frozen=True, slots=True)
class GameRecord:
    """A whole record, folded into rounds.

    Attributes:
        header: The record's header line.
        preset: The table preset named by ``game_started``.
        ruleset: The rules the game was played under.
        seats: Each seat's occupant.
        observed_from: Where a mid-game join landed, or ``None``.
        rounds: The rounds, in file order.
        ended: The closing event, or ``None`` when the file stops short.
        truncated: Whether the file's last line was a torn write.
        complete: Whether the record is whole: it ends, it was not
            truncated, and every round finished.
    """

    header: Header
    preset: str
    ruleset: RuleConfig
    seats: Mapping[Position, Seat]
    observed_from: ObservedFrom | None
    rounds: tuple[RoundRecord, ...]
    ended: GameEnded | None
    truncated: bool
    complete: bool


def _contract_of(auction: Auction) -> ObservedContract | None:
    """The contract an auction settled on, named by seat.

    Core's :meth:`~contrai_core.Auction.contract` builds a live
    :class:`~contrai_core.Contract`, which reads the winning bidder's
    ``team`` — and a record's auction holds bids seated on bare
    :class:`~contrai_core.Position` values, with no team to read. The
    seat-named :class:`~contrai_core.ObservedContract` carries the same
    terms and is what a :class:`~contrai_core.PlayObservation` already
    uses, so the auction's *precedence* rules stay core's and only the
    final assembly happens here.

    Args:
        auction: The round's auction, over sealed bids.

    Returns:
        The contract, or ``None`` if the round was passed out.
    """

    winning = auction.last_contract_bid
    if winning is None:
        return None
    return ObservedContract(
        declarer=winning.player,
        value=winning.value,
        suit=winning.suit,
        doubled_by=auction.double_player,
        redoubled_by=auction.redouble_player,
    )


def _group_tricks(
    number: int, plays: list[CardPlayed]
) -> tuple[
    tuple[TrickRecord[ObservedPlay], ...], tuple[bool, ...], tuple[ObservedPlay, ...]
]:
    """Cut a round's plays into tricks, in file order.

    A new group starts each time the ``trick`` field changes. Every group
    but the last must hold four plays; the last holds four (a completed
    trick) or fewer (the round was abandoned mid-trick).

    Args:
        number: The round number, for messages.
        plays: The round's ``card_played`` events, in file order.

    Returns:
        The completed tricks, one ``derived`` flag per completed trick,
        and the trailing partial trick.

    Raises:
        RecordFormatError: If a trick number repeats after being left, if
            a group that is not the last holds other than four plays, or
            if a trick's ``derived`` flags disagree.
    """

    groups: list[tuple[int, list[CardPlayed]]] = []
    seen: set[int] = set()
    for played in plays:
        if groups and groups[-1][0] == played.trick:
            groups[-1][1].append(played)
            continue
        if played.trick in seen:
            raise RecordFormatError(
                f"Round {number}: trick {played.trick} resumes after trick "
                f"{groups[-1][0]}"
            )
        seen.add(played.trick)
        groups.append((played.trick, [played]))

    tricks: list[TrickRecord[ObservedPlay]] = []
    derived: list[bool] = []
    current: tuple[ObservedPlay, ...] = ()
    for index, (trick_number, group) in enumerate(groups):
        last = index == len(groups) - 1
        if len(group) != 4:
            if not last:
                raise RecordFormatError(
                    f"Round {number} trick {trick_number} has four plays, got "
                    f"{len(group)}"
                )
            current = tuple(
                ObservedPlay(played.position, played.card) for played in group
            )
            continue
        flags = {played.derived for played in group}
        if len(flags) != 1:
            raise RecordFormatError(
                f"Round {number} trick {trick_number}: derived flags disagree "
                f"— a trick is reconstructed whole or observed whole"
            )
        tricks.append(
            TrickRecord(
                ObservedPlay(played.position, played.card) for played in group
            )
        )
        derived.append(flags.pop())
    return tuple(tricks), tuple(derived), current


@dataclass(slots=True)
class _RoundBuilder:
    """Accumulates one round's events, then builds its record."""

    dealt: RoundDealt
    bids: list[BidMade] = field(default_factory=list)
    plays: list[CardPlayed] = field(default_factory=list)
    belotes: list[BeloteHeld] = field(default_factory=list)
    score: RoundScored | None = None

    def add(self, event: GameEvent) -> None:
        """File one round-scoped event.

        Args:
            event: A bid, a play, a belote or a score.

        Raises:
            RecordFormatError: If the round is scored twice, or the event
                is not one a round can hold.
        """

        match event:
            case BidMade():
                self.bids.append(event)
            case CardPlayed():
                self.plays.append(event)
            case BeloteHeld():
                self.belotes.append(event)
            case RoundScored():
                if self.score is not None:
                    raise RecordFormatError(
                        f"Round {self.dealt.round} is scored twice"
                    )
                self.score = event
            case _:
                # Unreachable through :func:`project`, which narrows before
                # it calls here. Spelled out anyway, so a ninth event added
                # to the union announces itself instead of being dropped.
                raise RecordFormatError(
                    f"Round {self.dealt.round} cannot hold a "
                    f"{type(event).__name__}"
                )

    def build(self, rules: RuleConfig) -> RoundRecord:
        """Fold the accumulated events into a :class:`RoundRecord`.

        Args:
            rules: The table ruleset, which the auction runs under.

        Returns:
            The round.

        Raises:
            RecordFormatError: If the bid sequence is not gapless, or the
                plays do not group into tricks.
        """

        # §4.2 promises ``seq`` is 1-based and gapless *in the record* —
        # the wire's own numbering has gaps, and closing them is the
        # parser's job. Checking it here is what makes that promise
        # mean something to a reader.
        sequence = [bid.seq for bid in self.bids]
        if sequence != list(range(1, len(sequence) + 1)):
            raise RecordFormatError(
                f"Round {self.dealt.round}: bid seq must be 1-based and "
                f"gapless, got {sequence}"
            )
        auction_bids = tuple(bid.bid for bid in self.bids)
        auction = Auction(bids=auction_bids, rules=rules)
        contract = _contract_of(auction)
        tricks, derived, current = _group_tricks(self.dealt.round, self.plays)

        if contract is None:
            # A passed-out round is finished the moment four passes land;
            # there are no cards to wait for.
            complete = auction.is_terminal() and not self.plays
        else:
            complete = auction.is_terminal() and len(tricks) == 8 and not current

        if self.score is not None:
            outcome: RoundOutcome | None = self.score.outcome
        elif contract is None and auction.is_terminal():
            # The one outcome derivable without a score. Made or failed is
            # a scoring question and scoring lives in the engine, so a
            # contracted round with no score line has an unknown outcome
            # rather than a guessed one.
            outcome = RoundOutcome.ALL_PASS
        else:
            outcome = None

        return RoundRecord(
            number=self.dealt.round,
            dealer=self.dealt.dealer,
            hands=self.dealt.hands,
            hands_derivation=self.dealt.hands_derivation,
            auction=auction_bids,
            contract=contract,
            tricks=tricks,
            derived_tricks=derived,
            current_trick=current,
            belotes=tuple(self.belotes),
            score=self.score,
            outcome=outcome,
            complete=complete,
        )


def project(
    events: Iterable[GameEvent], *, truncated: bool = False
) -> GameRecord:
    """Fold a flat event stream into a :class:`GameRecord`.

    Args:
        events: The record's events, in file order.
        truncated: Whether the file they came from was cut short. A
            truncated record is never ``complete``.

    Returns:
        The game.

    Raises:
        RecordFormatError: If the stream does not start with a header and
            a ``game_started``, carries a second of either, deals a round
            number twice, names a round that was never dealt, or holds
            events after ``game_ended``.
    """

    stream = iter(events)
    header = next(stream, None)
    if not isinstance(header, Header):
        raise RecordFormatError(
            f"A record starts with a header, got "
            f"{type(header).__name__ if header is not None else 'nothing'}"
        )
    started = next(stream, None)
    if not isinstance(started, GameStarted):
        raise RecordFormatError(
            f"A record's second event is game_started, got "
            f"{type(started).__name__ if started is not None else 'nothing'}"
        )

    builders: dict[int, _RoundBuilder] = {}
    order: list[_RoundBuilder] = []
    ended: GameEnded | None = None

    for event in stream:
        if ended is not None:
            raise RecordFormatError(
                f"Events after game_ended: {type(event).__name__}"
            )
        match event:
            case Header() | GameStarted():
                raise RecordFormatError(
                    "A record carries one header and one game_started"
                )
            case RoundDealt():
                if event.round in builders:
                    raise RecordFormatError(f"Round {event.round} is dealt twice")
                builder = _RoundBuilder(dealt=event)
                builders[event.round] = builder
                order.append(builder)
            case GameEnded():
                ended = event
            case _:
                # Attached by number, never to "the round being built": an
                # observed score describes the last *completed* round and
                # so can arrive after the next deal.
                target = builders.get(event.round)
                if target is None:
                    raise RecordFormatError(
                        f"Event names round {event.round}, which was never dealt"
                    )
                target.add(event)

    rounds = tuple(builder.build(started.ruleset.config) for builder in order)
    complete = (
        ended is not None
        and not truncated
        and all(round_.complete for round_ in rounds)
    )
    return GameRecord(
        header=header,
        preset=started.ruleset.preset,
        ruleset=started.ruleset.config,
        seats=started.seats,
        observed_from=started.observed_from,
        rounds=rounds,
        ended=ended,
        truncated=truncated,
        complete=complete,
    )


def load_game(path: Path | str) -> GameRecord:
    """Read a record file and fold it into a :class:`GameRecord`.

    Args:
        path: The record file.

    Returns:
        The game, with the file's truncation flag carried through.

    Raises:
        FileNotFoundError: If the file does not exist.
        RecordFormatError: If a line or the stream as a whole is
            malformed.
        UnsupportedFormatError: If the header names an unreadable format
            major.
    """

    result = read_events(path)
    return project(result.events, truncated=result.truncated)
