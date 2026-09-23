"""The last stage: frames in, a ``contrai-data`` record out.

Two rules belong here and nowhere else, and both are about what the parser
refuses to do.

**A round is complete at twenty-eight transmitted plays plus the forced
eighth trick.** Anything less and the round is dropped rather than recorded
short: a record with seven tricks looks like a game that was abandoned, and
the difference between "abandoned" and "we stopped watching" is not something
a later reader can recover.

**The round in progress when the spectator joined is skipped.** The join
snapshot describes the last *completed* round, so the current round's deal
never arrives and its hands cannot be recovered. Reconstructing them from the
cards that happen to get played would produce four hands that are consistent
with the tricks and wrong about everything not yet played — which no check
downstream could catch.

The same instinct governs the score. A round with no score row emits no
``round_scored`` at all, and a game with no score read ends with null totals.
The record's schema allows both absences precisely so that a producer never
has to invent a number, and an invented total is exactly the fault the
verifier cannot see.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from contrai_core import (
    PRESETS,
    Card,
    ContractBid,
    ContractSuit,
    ObservedPlay,
    Position,
    Rank,
    RuleConfig,
    SlamLevel,
    TeamSide,
    TrickRecord,
)
from contrai_data import (
    FORMAT,
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

from ..exceptions import ParseError
from ..profile import Profile
from ..wire import WireEvent
from .deal import DECK_SIZE, deal_hands, final_trick, resolve_dealer
from .live import LiveRound, bid_events, collect_rounds, play_events
from .snapshot import ScoreRow, Snapshot, read_snapshot
from .translate import Translator

#: How many plays reach the wire, and how many a whole round holds.
OBSERVED_PLAYS = 28
TRICKS_PER_ROUND = 8

#: What a Belote is made of, in every regime this parser sees.
BELOTE_RANKS = (Rank.KING, Rank.QUEEN)

#: The Belote bonus, which is how a score row says one was announced.
BELOTE_POINTS = 20


@dataclass(frozen=True, slots=True)
class SessionResult:
    """A parsed session: the record, and what could not be parsed."""

    events: tuple[GameEvent, ...]
    notes: tuple[str, ...]
    """Everything the parser declined to do, in the order it declined."""

    skipped_rounds: tuple[int, ...]


def split_visits(
    events: Iterable[WireEvent], profile: Profile
) -> tuple[tuple[WireEvent, ...], ...]:
    """One group of events per table visit, in arrival order.

    A session's raw log holds every table the session looked at — two to
    seven of them in the logs measured on 2026-09-16 — while
    :func:`parse_session` assembles exactly one game out of whatever it is
    handed. Giving it a whole log therefore merges tables: rounds whose
    plays belong to another table are dropped as undealable, the record
    takes whichever game id came first, and every snapshot's seat map is
    folded into one. The live recorder never meets this, because it buffers
    one table at a time and resets at every seat. This is that same cut,
    made afterwards, which is what lets a parser fix reach games already
    watched.

    A join snapshot naming a table other than the one in progress opens a
    visit; a snapshot naming the same table again — the mirrored socket's
    copy, or a boundary re-read — stays in it. Everything else belongs to
    the visit in progress, so a game-over flag lands with the game it ended.
    Frames arriving before the first snapshot have no table to belong to and
    are dropped.

    Args:
        events: The session's wire events, in **arrival** order. Not
            ``order_events``' output: that files unclocked events after
            clocked ones, which would collect every snapshot at the end and
            lose the very sequence this reads.
        profile: The loaded profile, for the join-snapshot name and the
            table-id path.

    Returns:
        One tuple of events per visit, in the order the visits happened.
    """

    translator = Translator(profile)
    join = profile.wire.events.join_snapshot
    visits: list[list[WireEvent]] = []
    current: str | None = None
    for event in events:
        if event.kind == join:
            # Read through the profile's own path rather than the whole
            # snapshot: a table this profile cannot parse still has to be
            # separated from its neighbours, not raised over.
            table = translator.field(event.data, "table_id")
            if not visits or table != current:
                visits.append([])
                current = table
        elif not visits:
            continue
        visits[-1].append(event)

    # A visit is where a table's frames *arrive*, which is not quite where
    # they belong: a table's last events can still be in flight when the hop
    # lands, and during a fast sweep the rendered table runs a hop ahead of
    # the stream. An in-game event says which game it is part of, so it is
    # filed by that rather than by when it turned up — otherwise one game's
    # rounds appear inside another's visit, colliding with its round numbers
    # and costing both. Ordering is restored per visit by ``order_events``.
    home: dict[str, int] = {}
    for index, visit in enumerate(visits):
        for event in visit:
            if event.key is not None:
                home.setdefault(event.key.game, index)
    filed: list[list[WireEvent]] = [[] for _ in visits]
    for index, visit in enumerate(visits):
        for event in visit:
            target = index if event.key is None else home[event.key.game]
            filed[target].append(event)
    return tuple(tuple(visit) for visit in filed)


def parse_session(
    events: Iterable[WireEvent],
    profile: Profile,
    *,
    game_id: str | None = None,
    generator: str = "contrai-scraper",
    now: datetime | None = None,
    end_reason: EndReason | None = None,
) -> SessionResult:
    """Assemble one observed game from a session's events.

    Args:
        events: The session's wire events, already ordered.
        profile: The loaded profile.
        game_id: The record's identity; defaults to ``obs-`` plus the wire's
            own game id, which is what lets a re-observed game be recognised
            rather than duplicated.
        generator: The producer string for the header.
        now: The instant to stamp the header with; defaults to now, in UTC.
        end_reason: How the game finished, when the caller knows better than
            the wire does. A watchdog that gives up on a silent table and a
            process stopped by a signal are both invisible to the stream, and
            neither is what the wire's own flags would suggest.
            Unconditional: a caller that passes one is asserting it.

    Returns:
        The record and the parser's notes.

    Raises:
        ParseError: If the session carries no join snapshot. Without one there
            is no seat map, and a record whose seats were guessed is worse
            than no record at all, or when a mid-game join's running totals
            cannot be placed on a side.
    """

    translator = Translator(profile)
    ordered = tuple(events)
    snapshots = _snapshots(ordered, translator)
    if not snapshots:
        raise ParseError(
            "The session carries no join snapshot, so no seat map is known"
        )

    opening = snapshots[0]
    seat_of_player = dict(opening.seats)
    for snapshot in snapshots:
        seat_of_player.update(snapshot.seats)

    rounds = collect_rounds(ordered, translator)
    wire_game = _wire_game_id(ordered)
    scores = _scores_by_round(snapshots, rounds)
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    ts = stamp

    rules = PRESETS[profile.rules.preset]
    record: list[GameEvent] = [
        Header(
            format=FORMAT,
            source=RecordSource.OBSERVED,
            generator=generator,
            game_id=game_id or f"obs-{wire_game}",
            created_at=stamp,
        ),
        GameStarted(
            ruleset=Ruleset(preset=profile.rules.preset, config=rules),
            seats=_seats(opening, seat_of_player),
            observed_from=_observed_from(opening),
            ts=ts,
        ),
    ]

    notes: list[str] = []
    skipped: list[int] = []
    for number in sorted(rounds):
        round_ = rounds[number]
        produced = _round(
            round_, translator, seat_of_player, rules, scores.get(number), ts, notes
        )
        if produced is None:
            skipped.append(number)
            continue
        record += produced

    record.append(_game_ended(ordered, translator, snapshots, ts, end_reason))
    return SessionResult(tuple(record), tuple(notes), tuple(skipped))


def _snapshots(
    events: Sequence[WireEvent], translator: Translator
) -> tuple[Snapshot, ...]:
    """Every join snapshot the session carried, in arrival order."""

    name = translator.profile.wire.events.join_snapshot
    return tuple(
        read_snapshot(event.data, translator, at=event.received_ms)
        for event in events
        if event.kind == name
    )


def _wire_game_id(events: Sequence[WireEvent]) -> str:
    """The site's own id for the game, off the first key that carries one."""

    for event in events:
        if event.key is not None and event.key.game:
            return event.key.game
    return "unknown"


def _seats(
    snapshot: Snapshot, seat_of_player: Mapping[str, Position]
) -> dict[Position, Seat]:
    """The four occupants, keyed by seat.

    The account field is the stable identity — the same value the per-seat
    panel shows — so it fills both ``id`` and ``account`` rather than the
    session-local handle, which changes between observations of the same
    player.
    """

    seats: dict[Position, Seat] = {}
    for handle, position in seat_of_player.items():
        player = snapshot.players.get(handle)
        account = None if player is None else player.account
        seats[position] = Seat(
            id=account,
            name=(player.name if player is not None else None) or handle,
            account=account,
            kind=SeatKind.OBSERVED,
            level=None if player is None else player.level,
        )
    return seats


def _observed_from(snapshot: Snapshot) -> ObservedFrom | None:
    """Where a mid-game join landed, or ``None`` when the game was seen whole.

    A snapshot that already knows a completed round means rounds happened
    before the session began; the round being watched is the one after it.
    """

    if snapshot.round_index is None:
        return None
    if snapshot.totals is None:
        # A join records the score it landed on, and the record has no way to
        # say "unknown" there. Refusing as a ParseError keeps this a per-log
        # report for `parse` and a hop for the recorder, rather than a crash.
        raise ParseError(
            "The join snapshot's running totals cannot be placed on a side, "
            "so where the session joined cannot be recorded"
        )
    return ObservedFrom(
        round=snapshot.round_index + 1,
        phase=JoinPhase.PLAY,
        totals=dict(snapshot.totals),
    )


def _scores_by_round(
    snapshots: Sequence[Snapshot], rounds: Mapping[int, LiveRound]
) -> dict[int, tuple[ScoreRow, Mapping[TeamSide, int] | None]]:
    """Map each snapshot's score rows onto the rounds they describe.

    A snapshot's ``round_index`` names the round its **last** row scored;
    earlier rows walk backwards over the rounds before it. The walk is only
    ever applied to the rows one snapshot carried, so a round that was never
    covered by a snapshot simply has no entry — which is what makes "no score
    row" expressible rather than guessed at.
    """

    scores: dict[int, tuple[ScoreRow, Mapping[TeamSide, int] | None]] = {}
    for snapshot in snapshots:
        if snapshot.round_index is None or not snapshot.score_rows:
            continue
        number = snapshot.round_index
        for row in reversed(snapshot.score_rows):
            if number < 1:
                break
            # Only the newest row's totals are the totals *after* that round;
            # an older row's running total is not in the payload at all.
            totals = snapshot.totals if number == snapshot.round_index else None
            scores.setdefault(number, (row, totals))
            number -= 1
    return scores


def _round(
    round_: LiveRound,
    translator: Translator,
    seat_of_player: Mapping[str, Position],
    rules: RuleConfig,
    score: tuple[ScoreRow, Mapping[TeamSide, int] | None] | None,
    ts: str,
    notes: list[str],
) -> list[GameEvent] | None:
    """Everything one round contributes to the record, or ``None`` if skipped."""

    number = round_.number
    if len(round_.deal_stock) != DECK_SIZE:
        notes.append(
            f"round {number}: no deal was transmitted, so the round was "
            "already under way when the session joined — skipped"
        )
        return None

    stock = [translator.card(token) for token in round_.deal_stock]
    plays = play_events(round_, translator, seat_of_player, ts=ts)
    played_by_seat: dict[Position, set[Card]] = {}
    for play in plays:
        played_by_seat.setdefault(play.position, set()).add(play.card)

    dealer = resolve_dealer(stock, played_by_seat, translator.rotation)
    if dealer is None:
        notes.append(
            f"round {number}: no deal rotation fits the observed plays, so "
            "the dealer is unknown — skipped"
        )
        return None

    # The first packet goes to the seat after the dealer in the table's own
    # direction — the same seat that speaks first.
    hands = deal_hands(stock, dealer.next_in(rules.turn_direction), translator.rotation)
    try:
        bids = bid_events(
            round_, translator, seat_of_player, dealer=dealer, rules=rules, ts=ts
        )
    except ParseError as error:
        # A round-level refusal, like an unresolvable dealer: one auction the
        # parser cannot account for costs that round, not the whole game.
        notes.append(f"round {number}: {error} — skipped")
        return None
    contract = _contract(bids)

    if contract is None:
        notes.append(
            f"round {number}: the auction reached no contract, so the round "
            "cannot be played out — skipped"
        )
        return None

    try:
        last = _final_trick(plays, hands, contract.suit, translator, ts, number)
    except ParseError as error:
        notes.append(f"round {number}: {error} — skipped")
        return None

    events: list[GameEvent] = [
        RoundDealt(
            round=number,
            dealer=dealer,
            hands=hands,
            hands_derivation=HandsDerivation.DEALT_FROM_DECK,
            ts=ts,
        ),
        *bids,
        *plays,
        *last,
    ]
    events += _belotes(number, hands, contract.suit, score, ts)
    scored = _round_scored(number, contract, bids, score, [*plays, *last], ts)
    if scored is not None:
        events.append(scored)
    return events


def _contract(bids: Sequence[BidMade]) -> ContractBid | None:
    """The auction's winning bid, which is its last contract bid."""

    for made in reversed(bids):
        if isinstance(made.bid, ContractBid):
            return made.bid
    return None


def _final_trick(
    plays: Sequence[CardPlayed],
    hands: Mapping[Position, tuple[Card, ...]],
    trump: ContractSuit,
    translator: Translator,
    ts: str,
    number: int,
) -> list[CardPlayed]:
    """Rebuild the trick the wire never sent.

    Its leader is the winner of the seventh trick, decided by core's own rule
    — the same one the record's projection will re-derive — so the two cannot
    disagree about who led.
    """

    counts = Counter(play.trick for play in plays)
    short = [
        number_
        for number_ in range(1, TRICKS_PER_ROUND)
        if counts.get(number_) != len(Position)
    ]
    if short:
        raise ParseError(
            f"{len(plays)} of {OBSERVED_PLAYS} plays were observed — "
            f"trick(s) {', '.join(str(n) for n in short)} never completed, so "
            "the forced last trick cannot be rebuilt"
        )
    seventh = [play for play in plays if play.trick == TRICKS_PER_ROUND - 1]
    leader = (
        TrickRecord(ObservedPlay(play.position, play.card) for play in seventh)
        .winner(trump)
        .position
    )
    played = {play.card for play in plays}
    return [
        CardPlayed(
            round=number,
            trick=TRICKS_PER_ROUND,
            position=seat,
            card=card,
            derived=True,
            think_ms=None,
            ts=ts,
        )
        for seat, card in final_trick(hands, played, leader, translator.rotation)
    ]


def _belotes(
    number: int,
    hands: Mapping[Position, tuple[Card, ...]],
    trump: ContractSuit,
    score: tuple[ScoreRow, Mapping[TeamSide, int] | None] | None,
    ts: str,
) -> list[BeloteHeld]:
    """Which seats held the trump king and queen.

    Possession is a fact about the deal and is always recorded. Whether it was
    *announced* is not on the wire at all, so it is left unknown unless a
    score row credits the bonus to that seat's side.
    """

    credited = set()
    if score is not None:
        credited = {
            side for side, points in score[0].belote.items()
            if points >= BELOTE_POINTS
        }
    held: list[BeloteHeld] = []
    for seat, cards in hands.items():
        pair = tuple(
            card for card in cards
            if card.suit == trump and card.rank in BELOTE_RANKS
        )
        if len(pair) != len(BELOTE_RANKS):
            continue
        held.append(
            BeloteHeld(
                round=number,
                position=seat,
                cards=pair,
                announced=True if seat.team_side in credited else None,
                ts=ts,
            )
        )
    return held


def _round_scored(
    number: int,
    contract: ContractBid,
    bids: Sequence[BidMade],
    score: tuple[ScoreRow, Mapping[TeamSide, int] | None] | None,
    plays: Sequence[CardPlayed],
    ts: str,
) -> RoundScored | None:
    """One round's score, or ``None`` when the wire never scored it."""

    if score is None:
        return None
    row, totals = score
    return RoundScored(
        round=number,
        outcome=RoundOutcome.MADE if row.made else RoundOutcome.FAILED,
        declarer=contract.player,
        contract=ContractTerms(
            value=row.contract.value if row.contract.value is not None
            else contract.value,
            suit=row.contract.suit or contract.suit,
            multiplier=row.contract.multiplier,
        ),
        taken=dict(row.taken),
        belote=dict(row.belote),
        # Neither is on the wire. Zero is not a guess here: the observed
        # tables play no announcements, and a carried-over mark would appear
        # in the row's own marked figures.
        announcements=dict.fromkeys(TeamSide, 0),
        carried_over=dict.fromkeys(TeamSide, 0),
        marked={
            side: SideMark(made=made, announced=announced)
            for side, (made, announced) in row.marked.items()
        },
        totals=None if totals is None else dict(totals),
        # The last trick's bonus is folded into the row's card points rather
        # than stated, so which side took it is not recoverable.
        last_trick=None,
        slam=_slam(contract, plays),
        source=ScoreSource.SNAPSHOT,
        ts=ts,
    )


def _slam(
    contract: ContractBid, plays: Sequence[CardPlayed]
) -> SlamOutcome:
    """Which Slam, if any, the round was.

    A bid Slam outranks a swept one, as the engine's own recorder has it: the
    declarer announced it, so that is what the round *was*, whatever the sweep
    looked like afterwards.

    An unannounced sweep is a fact about the tricks rather than about the bid,
    and it is read here rather than left to the record's projection.
    ``SlamOutcome.UNANNOUNCED`` means "all eight tricks taken without having
    called it", so writing ``NONE`` for a swept round states something the
    round's own plays contradict — and the verifier, which replays them, says
    so. A doubled sweep is still a sweep: what the double changes is what the
    round is *worth*, which the §9.6 scoring knobs decide, not whether the
    eight tricks were taken. The multiplier is therefore not read here.

    Args:
        contract: The auction's winning bid.
        plays: Every play of the round, the rebuilt last trick included.

    Returns:
        The matching :class:`~contrai_data.SlamOutcome`.
    """

    if contract.value is SlamLevel.SLAM:
        return SlamOutcome.SLAM
    if contract.value is SlamLevel.SOLO_SLAM:
        return SlamOutcome.SOLO_SLAM
    if _swept_by(plays, contract.suit) is contract.player.team_side:
        return SlamOutcome.UNANNOUNCED
    return SlamOutcome.NONE


def _swept_by(
    plays: Sequence[CardPlayed], trump: ContractSuit
) -> TeamSide | None:
    """Which side took all eight tricks, or ``None`` when neither did.

    The winner rule is core's — the same :meth:`~contrai_core.TrickRecord.winner`
    that :func:`_final_trick` leads with and that the record's projection
    re-derives — so the three readings of one round cannot disagree.

    Args:
        plays: Every play of the round, the rebuilt last trick included.
        trump: The contract's trump.

    Returns:
        The sweeping side, or ``None`` when the round was shared or when the
        plays do not make eight whole tricks.
    """

    by_trick: dict[int, list[CardPlayed]] = {}
    for play in plays:
        by_trick.setdefault(play.trick, []).append(play)
    if len(by_trick) != TRICKS_PER_ROUND:
        return None
    swept: TeamSide | None = None
    for _, trick in sorted(by_trick.items()):
        if len(trick) != len(Position):
            return None
        side = (
            TrickRecord(ObservedPlay(play.position, play.card) for play in trick)
            .winner(trump)
            .position.team_side
        )
        if swept is not None and side is not swept:
            return None
        swept = side
    return swept


def _game_ended(
    events: Sequence[WireEvent],
    translator: Translator,
    snapshots: Sequence[Snapshot],
    ts: str,
    override: EndReason | None,
) -> GameEnded:
    """How the game finished, and with what totals.

    The totals come from the newest score read and are ``None`` when there was
    none — a game whose last rounds were never scored has no total, and the
    schema says so rather than the parser adding up what it saw.

    An ``override`` short-circuits the whole reading: the caller saw something
    the stream cannot carry, and the flags left on the wire would describe a
    different ending.
    """

    totals = next(
        (dict(snapshot.totals) for snapshot in reversed(snapshots)
         if snapshot.totals),
        None,
    )
    if override is not None:
        return GameEnded(totals=totals, winner=None, reason=override, ts=ts)

    # The table's own game-over flag wins over the observer leaving: a game
    # that finished and was then left finished.
    reason = EndReason.INTERRUPTED
    name = translator.profile.wire.events.table_update
    for event in events:
        if event.kind != name:
            continue
        if translator.field(event.data, "ended"):
            reason = EndReason.TARGET_REACHED
        elif translator.field(event.data, "left"):
            reason = EndReason.OBSERVER_LEFT
    return GameEnded(totals=totals, winner=None, reason=reason, ts=ts)
