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
    rules_for,
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
from ..profile import Profile, WireSection
from ..wire import WireEvent
from .deal import DECK_SIZE, deal_hands, final_trick, resolve_dealer
from .live import (
    DRAW_ROUND,
    LiveRound,
    bid_events,
    collect_rounds,
    is_draw,
    passed_out,
    play_events,
)
from .sheet import SheetLine, read_sheet
from .snapshot import ScoreRow, Snapshot, read_snapshot
from .translate import Translator

#: How many plays reach the wire, and how many a whole round holds.
OBSERVED_PLAYS = 28
TRICKS_PER_ROUND = 8

#: What a Belote is made of, in every regime this parser sees.
BELOTE_RANKS = (Rank.KING, Rank.QUEEN)

#: The Belote bonus, which is how a score row says one was announced.
BELOTE_POINTS = 20

#: The last-trick bonus, which a score row folds into the taker's card points.
LAST_TRICK_BONUS = 10


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
    passed = _passed_out_rounds(rounds, translator)
    sheet = read_sheet(snapshots, seen=frozenset(rounds), passed=passed)
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    ts = stamp

    rules = PRESETS[profile.rules.preset]
    joined = _observed_from(opening) or _joined_in_round_one(rounds)
    record: list[GameEvent] = [
        Header(
            format=FORMAT,
            source=RecordSource.OBSERVED,
            generator=generator,
            game_id=game_id or observed_game_id(wire_game),
            created_at=stamp,
        ),
        GameStarted(
            ruleset=Ruleset(preset=profile.rules.preset, config=rules),
            seats=_seats(opening, seat_of_player),
            observed_from=joined,
            ts=ts,
        ),
    ]

    notes: list[str] = [*_draw_notes(ordered, profile.wire), *sheet.notes]
    skipped: list[int] = []
    last = max(rounds, default=0)
    for number in sorted(rounds):
        round_ = rounds[number]
        produced = _round(
            round_, translator, seat_of_player, rules, sheet.lines.get(number),
            sheet.standings.get(number), ts, notes,
            passed=number in passed,
            cut_short=number == last and _cut_short(round_, passed, snapshots),
        )
        if produced is None:
            skipped.append(number)
            continue
        record += produced

    record.append(
        _game_ended(ordered, translator, snapshots, ts, end_reason, last)
    )
    return SessionResult(tuple(record), tuple(notes), tuple(skipped))


def round_count(events: Sequence[GameEvent]) -> int:
    """How many rounds a record holds.

    A record with none is not a game worth writing: it is a table seated
    too late to see a deal, and both ``contrai-scrape parse`` and the live
    recorder leave it out on this one count.

    Args:
        events: A record's events.

    Returns:
        The number of rounds dealt in it.
    """

    return sum(1 for event in events if isinstance(event, RoundDealt))


def _draw_notes(events: Sequence[WireEvent], wire: WireSection) -> list[str]:
    """What leaving the pre-game draw out left out that was not the draw as named.

    The draw itself goes unremarked: a game caught from its first card always
    carries one, and a note on every such record would bury the notes that
    matter. What is said is anything else the exclusion swept up — an event at
    round 0 under another verb, or the draw's verb somewhere else — because
    that is the site saying something new, and a stage that quietly drops what
    it does not recognise is how a change goes unseen.

    Args:
        events: The session's events.
        wire: The profile's wire section, for the draw's verb.

    Returns:
        Zero, one or two notes.
    """

    draw = [
        event.key for event in events
        if event.key is not None and is_draw(event.key, wire.draw_verb)
    ]
    if not draw:
        return []
    if wire.draw_verb is None:
        return [
            f"round 0: {len(draw)} event(s) left out as the pre-game draw, which "
            "belongs to no hand — the profile names no draw verb to confirm it by"
        ]
    notes = []
    unnamed = sum(1 for key in draw if key.verb != wire.draw_verb)
    if unnamed:
        notes.append(
            f"round 0: {unnamed} event(s) under a verb other than the draw's were "
            "left out with it"
        )
    misplaced = sum(1 for key in draw if key.round != DRAW_ROUND)
    if misplaced:
        notes.append(
            f"{misplaced} event(s) under the draw's verb were addressed outside "
            "round 0, and left out"
        )
    return notes


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


def observed_game_id(wire_game: str) -> str:
    """The record's game id for a game the site calls ``wire_game``.

    Args:
        wire_game: The site's own id, as its in-game events and its join
            snapshot's round block carry it.

    Returns:
        ``obs-`` plus that id: the stem of the game's record file.
    """

    return f"obs-{wire_game}"


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


def _joined_in_round_one(rounds: Mapping[int, LiveRound]) -> ObservedFrom | None:
    """Round 1 as the join point, when the session walked in on it.

    A join snapshot that knows no completed round cannot tell a session seated
    before the first deal from one seated just after it — a chase that lost
    the race to the deal. Only the wire can: the second never receives round
    1's deal, so the round is skipped, and a record that still claimed the
    game seen whole would be wrong about its own first round.

    The phase is read off the auction: a session that saw any of round 1's
    bids sat down while it was under way; one that saw none arrived once the
    cards were being played. Nothing is scored before round 1, so the running
    score it landed on is nil.

    Args:
        rounds: The session's rounds, by number.

    Returns:
        Round 1 as the join point, or ``None`` when its deal was received or
        the session saw nothing of it.
    """

    first = rounds.get(1)
    if first is None or len(first.deal_stock) == DECK_SIZE:
        return None
    return ObservedFrom(
        round=1,
        phase=JoinPhase.BIDDING if first.bids else JoinPhase.PLAY,
        totals=dict.fromkeys(TeamSide, 0),
    )


def _passed_out_rounds(
    rounds: Mapping[int, LiveRound], translator: Translator
) -> frozenset[int]:
    """The numbers of every round the wire shows was passed out.

    Args:
        rounds: The session's rounds, by number.
        translator: The vocabulary layer, for how a pass is spelled.

    Returns:
        The passed-out round numbers — see
        :func:`~contrai_scraper.parse.live.passed_out`.
    """

    tokens = translator.profile.wire.tokens
    last = max(rounds, default=0)
    return frozenset(
        number for number, round_ in rounds.items()
        if passed_out(round_, tokens, superseded=number < last)
    )


def _round(
    round_: LiveRound,
    translator: Translator,
    seat_of_player: Mapping[str, Position],
    rules: RuleConfig,
    line: SheetLine | None,
    standing: Mapping[TeamSide, int] | None,
    ts: str,
    notes: list[str],
    *,
    passed: bool = False,
    cut_short: bool = False,
) -> list[GameEvent] | None:
    """Everything one round contributes to the record, or ``None`` if skipped.

    Args:
        round_: The round as the wire described it.
        translator: The vocabulary layer.
        seat_of_player: Which seat each player handle sits in.
        rules: The table ruleset.
        line: The round's row on the score sheet, if one was placed.
        standing: The totals standing through the round, if it was passed
            out and they were read.
        ts: The timestamp to stamp every event with.
        notes: Where a skipped round says why.
        passed: Whether the wire shows the round passed out.
        cut_short: Whether the record ends before the round did: the last
            round of the visit, not passed out, short of its plays, and
            covered by no score read.

    Returns:
        The round's events, or ``None`` when it was skipped.
    """

    number = round_.number
    if len(round_.deal_stock) != DECK_SIZE:
        notes.append(
            f"round {number}: no deal was transmitted, so the round was "
            "already under way when the session joined — skipped"
        )
        return None

    stock = [translator.card(token) for token in round_.deal_stock]
    if passed and len(round_.bids) == len(Position):
        return _passed_out_round(
            round_, stock, translator, seat_of_player, rules, standing, ts, notes
        )
    if cut_short:
        notes.append(
            f"round {number}: the record ends before the round did — skipped"
        )
        return None

    plays = play_events(round_, translator, seat_of_player, ts=ts)
    if not plays:
        # Nothing to resolve a dealer from. A passed-out round with its whole
        # auction was recorded above; this is one seen only in part.
        notes.append(
            f"round {number}: no card of the round was observed — skipped"
        )
        return None
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
    events += _belotes(number, hands, contract.suit, line, ts)
    scored = _round_scored(number, contract, line, [*plays, *last], ts)
    if scored is not None:
        events.append(scored)
    return events


def _cut_short(
    round_: LiveRound, passed: frozenset[int], snapshots: Sequence[Snapshot]
) -> bool:
    """Whether the record stops before a round it began did.

    Meant for the last round of a visit: one that was not passed out, is
    short of its plays, and that no score read reached. It was still being
    played when the watching stopped, which is not the same fault as a
    round whose frames went missing mid-game.

    Args:
        round_: The round as the wire described it.
        passed: The passed-out round numbers.
        snapshots: Every snapshot of the session.

    Returns:
        Whether the round was cut off by the end of the record.
    """

    number = round_.number
    return (
        number not in passed
        and len(round_.plays) < OBSERVED_PLAYS
        and not any(
            snapshot.round_index is not None and snapshot.round_index >= number
            for snapshot in snapshots
        )
    )


def _passed_out_round(
    round_: LiveRound,
    stock: Sequence[Card],
    translator: Translator,
    seat_of_player: Mapping[str, Position],
    rules: RuleConfig,
    standing: Mapping[TeamSide, int] | None,
    ts: str,
    notes: list[str],
) -> list[GameEvent] | None:
    """A round every seat passed: its deal, its four passes and its score line.

    Four seats looked at known hands and none bid, which is bidding data like
    any other. What a played round takes from its plays — the dealer — comes
    from the auction instead: nobody is forced to pass an empty auction, so
    the first transmitted bid is the first speaker's, and the dealer sits
    just before it.

    Args:
        round_: The round as the wire described it, four passes and no play.
        stock: The deck in its pre-deal order.
        translator: The vocabulary layer.
        seat_of_player: Which seat each player handle sits in.
        rules: The table ruleset.
        standing: The totals standing through the round, if read.
        ts: The timestamp to stamp every event with.
        notes: Where a skipped round says why.

    Returns:
        The round's events, or ``None`` when the auction cannot be placed.
    """

    number = round_.number
    handle, _ = round_.bids[min(round_.bids)]
    speaker = seat_of_player.get(handle)
    if speaker is None:
        notes.append(
            f"round {number}: every seat passed, but the first to speak is "
            "seated nowhere, so the dealer is unknown — skipped"
        )
        return None
    rotation = translator.rotation
    dealer = rotation[(rotation.index(speaker) - 1) % len(rotation)]
    try:
        bids = bid_events(
            round_, translator, seat_of_player, dealer=dealer, rules=rules, ts=ts
        )
    except ParseError as error:
        notes.append(f"round {number}: {error} — skipped")
        return None
    return [
        RoundDealt(
            round=number,
            dealer=dealer,
            hands=deal_hands(stock, speaker, rotation),
            hands_derivation=HandsDerivation.DEALT_FROM_DECK,
            ts=ts,
        ),
        *bids,
        _passed_out_scored(number, standing, ts),
    ]


def _passed_out_scored(
    number: int, standing: Mapping[TeamSide, int] | None, ts: str
) -> RoundScored:
    """The score line of a passed-out round, which the site's sheet never writes.

    Nothing is marked, and nothing is paid out either: under the held rule
    a dispute's pot goes to the next *contract's* winner, and there was no
    contract. The totals therefore stand where they stood before the round,
    when those are known.

    Args:
        number: The round.
        standing: The totals standing through the round, if read.
        ts: The timestamp to stamp it with.

    Returns:
        An ``all_pass`` line.
    """

    nothing = dict.fromkeys(TeamSide, 0)
    return RoundScored(
        round=number,
        outcome=RoundOutcome.ALL_PASS,
        declarer=None,
        contract=None,
        taken=dict(nothing),
        belote=dict(nothing),
        announcements=dict(nothing),
        carried_over=dict(nothing),
        marked={side: SideMark(made=0, announced=0) for side in TeamSide},
        totals=None if standing is None else dict(standing),
        last_trick=None,
        slam=SlamOutcome.NONE,
        source=ScoreSource.SNAPSHOT,
        ts=ts,
    )


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
    line: SheetLine | None,
    ts: str,
) -> list[BeloteHeld]:
    """Which seats held the trump king and queen.

    Possession is a fact about the deal and is always recorded. Whether it was
    *announced* is not on the wire at all, so it is left unknown unless a
    score row credits the bonus to that seat's side.
    """

    credited = set()
    if line is not None:
        credited = {
            side for side, points in line.row.belote.items()
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


def _held(row: ScoreRow, contract: ContractBid) -> bool:
    """Whether the row is a held dispute (§7.5).

    The site flags it made, yet marks the declaring side nothing: its
    points went into a pot for the next contract. A made contract on these
    tables always marks at least its value as announced points, so a made
    row with the declarer at 0 / 0 can mean nothing else.
    """

    return row.made and tuple(row.marked[contract.player.team_side]) == (0, 0)


def _round_scored(
    number: int,
    contract: ContractBid,
    line: SheetLine | None,
    plays: Sequence[CardPlayed],
    ts: str,
) -> RoundScored | None:
    """One round's score, or ``None`` when the wire never scored it."""

    if line is None:
        return None
    row, totals = line.row, line.totals
    return RoundScored(
        round=number,
        outcome=(
            RoundOutcome.HELD if _held(row, contract)
            else RoundOutcome.MADE if row.made
            else RoundOutcome.FAILED
        ),
        declarer=contract.player,
        contract=ContractTerms(
            value=row.contract.value if row.contract.value is not None
            else contract.value,
            suit=row.contract.suit or contract.suit,
            multiplier=row.contract.multiplier,
        ),
        taken=dict(row.taken),
        belote=dict(row.belote),
        # Not on the wire: the observed tables play no announcements.
        announcements=dict.fromkeys(TeamSide, 0),
        # A held dispute's pot is paid into the winner's running total and
        # stated nowhere in the row: the sheet reads it off the totals.
        carried_over=(
            None if line.carried_over is None else dict(line.carried_over)
        ),
        marked={
            side: SideMark(made=made, announced=announced)
            for side, (made, announced) in row.marked.items()
        },
        totals=None if totals is None else dict(totals),
        last_trick=_last_trick(plays, contract.suit, row.taken),
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

    Args:
        plays: Every play of the round, the rebuilt last trick included.
        trump: The contract's trump.

    Returns:
        The sweeping side, or ``None`` when the round was shared or when the
        plays do not make eight whole tricks.
    """

    won = _tricks_won(plays, trump)
    if won is None:
        return None
    sides = {side for side, _ in won}
    return sides.pop() if len(sides) == 1 else None


def _last_trick(
    plays: Sequence[CardPlayed],
    trump: ContractSuit,
    taken: Mapping[TeamSide, int],
) -> TeamSide | None:
    """Which side took the last trick, as the row's card points bear out.

    The eighth trick is rebuilt rather than seen, and the site names no
    side for it — but its card points fold the ten-point bonus into the
    taker's pile. So the side core's winner rule hands the last trick is
    written down only when the row's card points are exactly the tricks'
    piles with the bonus on that side: the claim is then the site's, not
    the parser's alone. A sweep is the exception, because the site writes
    the sweeper's flat substitute in place of its pile; a side that took
    all eight tricks took the last. Measured over the V5 corpus: 3858
    rounds agree, 338 are sweeps, none disagree.

    Args:
        plays: Every play of the round, the rebuilt last trick included.
        trump: The contract's trump.
        taken: The row's card points by side.

    Returns:
        The side that took the last trick, or ``None`` when the plays do not
        make eight whole tricks or the row's card points say otherwise.
    """

    won = _tricks_won(plays, trump)
    if won is None:
        return None
    last = won[-1][0]
    if all(side is last for side, _ in won):
        return last
    points = rules_for(trump).points
    piles = dict.fromkeys(TeamSide, 0)
    for side, trick in won:
        piles[side] += sum(points(play.card) for play in trick)
    piles[last] += LAST_TRICK_BONUS
    stated = {side: taken.get(side, 0) for side in TeamSide}
    return last if stated == piles else None


def _tricks_won(
    plays: Sequence[CardPlayed], trump: ContractSuit
) -> list[tuple[TeamSide, list[CardPlayed]]] | None:
    """Each trick in order, with the side that won it.

    The winner rule is core's — the same :meth:`~contrai_core.TrickRecord.winner`
    that :func:`_final_trick` leads with and that the record's projection
    re-derives — so the readings of one round cannot disagree.

    Args:
        plays: Every play of the round, the rebuilt last trick included.
        trump: The contract's trump.

    Returns:
        ``(winning side, plays)`` per trick, first to eighth, or ``None``
        when the plays do not make eight whole tricks.
    """

    by_trick: dict[int, list[CardPlayed]] = {}
    for play in plays:
        by_trick.setdefault(play.trick, []).append(play)
    if len(by_trick) != TRICKS_PER_ROUND:
        return None
    won = []
    for _, trick in sorted(by_trick.items()):
        if len(trick) != len(Position):
            return None
        side = (
            TrickRecord(ObservedPlay(play.position, play.card) for play in trick)
            .winner(trump)
            .position.team_side
        )
        won.append((side, trick))
    return won


def _game_ended(
    events: Sequence[WireEvent],
    translator: Translator,
    snapshots: Sequence[Snapshot],
    ts: str,
    override: EndReason | None,
    last_round: int,
) -> GameEnded:
    """How the game finished, and with what totals.

    The totals come from the newest score read and are ``None`` when there was
    none — a game whose last rounds were never scored has no total, and the
    schema says so rather than the parser adding up what it saw.

    The table's game-over flag, seen anywhere in the game's updates, decides
    the reason over everything else: a game that finished and was then left
    finished, whatever the spectator was told afterwards and whatever the
    caller guessed. Otherwise an ``override`` stands — the caller saw
    something the stream cannot carry — and then the table's word that the
    spectator left, else ``interrupted``.

    A finished game whose final read never came gets ``None`` totals rather
    than those of an earlier read: they would miss the last round, and a
    winner derived from them could be the wrong side.

    Args:
        events: The session's events.
        translator: The vocabulary layer.
        snapshots: Every snapshot of the session, in arrival order.
        ts: The timestamp to stamp it with.
        override: The caller's end reason, if it has one.
        last_round: The newest round the wire showed.

    Returns:
        The record's last line.
    """

    newest = next(
        (snapshot for snapshot in reversed(snapshots) if snapshot.totals), None
    )
    totals = None if newest is None else dict(newest.totals)
    name = translator.profile.wire.events.table_update
    updates = [event.data for event in events if event.kind == name]
    if any(translator.field(update, "ended") for update in updates):
        if newest is not None and (newest.round_index or 0) < last_round:
            totals = None
        return GameEnded(
            totals=totals, winner=None, reason=EndReason.TARGET_REACHED, ts=ts
        )
    if override is not None:
        reason = override
    elif any(translator.field(update, "left") for update in updates):
        reason = EndReason.OBSERVER_LEFT
    else:
        reason = EndReason.INTERRUPTED
    return GameEnded(totals=totals, winner=None, reason=reason, ts=ts)
