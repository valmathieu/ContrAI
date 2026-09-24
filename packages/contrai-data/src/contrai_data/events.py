"""The game-record event vocabulary — one frozen dataclass per event.

A record is a flat, append-only sequence of these values: a
:class:`Header`, then :class:`GameStarted`, then one :class:`RoundDealt`
per deal carrying the :class:`BidMade` / :class:`CardPlayed` /
:class:`BeloteHeld` / :class:`RoundScored` events that belong to it, and
finally :class:`GameEnded`.

Events hold **domain values, not tokens**: a seat is a
:class:`~contrai_core.Position`, a card is a :class:`~contrai_core.Card`,
a bid is a core ``Bid[Position]``, a ruleset is a real
:class:`~contrai_core.RuleConfig`. Turning those into the record's ASCII
tokens is :mod:`contrai_data.tokens`' job and turning an event into a
JSON line is :mod:`contrai_data.codec`'s. Keeping the three apart is what
lets the format's spelling change without touching what an event *is*.

Nothing here derives anything. Contract establishment, trick winners and
the all-pass redeal are recomputed by :mod:`contrai_data.projection` from
core, because a derived fact stored twice is a fact that can disagree
with itself.

What each event *does* check is the invariant only it can see — four
seats, thirty-two distinct cards, a bid filed under the seat that made
it, a belote that is really a King and Queen of one suit, an outcome that
agrees with whether a contract exists. That is the line between a
mis-parsed capture that raises and one that merely looks plausible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from contrai_core import (
    Bid,
    Card,
    ContractSuit,
    Position,
    Rank,
    RuleConfig,
    SlamLevel,
    TeamSide,
)

from .exceptions import RecordFormatError


# ----------------------------------------------------------------------
# The eight closed vocabularies
# ----------------------------------------------------------------------


class RecordSource(Enum):
    """Who produced the record.

    Members:
        ENGINE: The engine played the game itself.
        OBSERVED: The scraper watched someone else's table.
    """

    ENGINE = "engine"
    OBSERVED = "observed"

    def __str__(self) -> str:
        return self.value


class SeatKind(Enum):
    """What kind of player occupied a seat.

    Members:
        HUMAN: A person at the keyboard.
        AI: One of the engine's strategies.
        OBSERVED: Somebody at the table we watched, whose nature we
            cannot know.
    """

    HUMAN = "human"
    AI = "ai"
    OBSERVED = "observed"

    def __str__(self) -> str:
        return self.value


class JoinPhase(Enum):
    """The phase a mid-game join landed in.

    Members:
        BIDDING: The auction was under way.
        PLAY: Cards were already being played.
    """

    BIDDING = "bidding"
    PLAY = "play"

    def __str__(self) -> str:
        return self.value


class HandsDerivation(Enum):
    """How the hands on a ``round_dealt`` event were arrived at.

    Members:
        SELF_PLAY: The engine dealt them and knows them outright.
        OBSERVED: A spectator feed reported all four hands.
        DEALT_FROM_DECK: Reconstructed from a recorded deck and cut.
    """

    SELF_PLAY = "self_play"
    OBSERVED = "observed"
    DEALT_FROM_DECK = "dealt_from_deck"

    def __str__(self) -> str:
        return self.value


class RoundOutcome(Enum):
    """How a round resolved.

    Members:
        MADE: The declaring side met its contract.
        FAILED: It did not.
        ALL_PASS: Nobody bid, so the round was passed out.
        DISPUTED: Two score sources disagreed about the round.
        HELD: The contract tied and its points went into a pot for the
            next contract to be won (§7.5). Not DISPUTED, which says two
            score sources disagreed about a round.
    """

    MADE = "made"
    FAILED = "failed"
    ALL_PASS = "all_pass"
    DISPUTED = "disputed"
    HELD = "held"

    def __str__(self) -> str:
        return self.value


class SlamOutcome(Enum):
    """Whether the round swept the tricks, and whether that was called.

    Members:
        NONE: Not a sweep.
        SLAM: An announced Slam, made.
        SOLO_SLAM: An announced Solo Slam, made.
        UNANNOUNCED: All eight tricks taken without having called it.
    """

    NONE = "none"
    SLAM = "slam"
    SOLO_SLAM = "solo_slam"
    UNANNOUNCED = "unannounced"

    def __str__(self) -> str:
        return self.value


class ScoreSource(Enum):
    """Where a round's score line was read from.

    Members:
        ENGINE: Computed by the engine's own scoring.
        SNAPSHOT: Read off an observed table's state snapshot.
        PANEL: Read off an observed table's score panel.
    """

    ENGINE = "engine"
    SNAPSHOT = "snapshot"
    PANEL = "panel"

    def __str__(self) -> str:
        return self.value


class EndReason(Enum):
    """Why the record stops.

    Members:
        TARGET_REACHED: A side crossed the target score.
        ABANDONED: The table broke up before a winner.
        OBSERVER_LEFT: We stopped watching; the game may well have gone on.
        INTERRUPTED: The producing process was stopped.
    """

    TARGET_REACHED = "target_reached"
    ABANDONED = "abandoned"
    OBSERVER_LEFT = "observer_left"
    INTERRUPTED = "interrupted"

    def __str__(self) -> str:
        return self.value


# ----------------------------------------------------------------------
# Shared invariant
# ----------------------------------------------------------------------


def _require_both_sides(mapping: Mapping[TeamSide, object], field: str) -> None:
    """Reject a per-side component that does not name both sides.

    Every side-keyed field in the format is total: callers index it
    (``taken[TeamSide.NS]``) rather than reaching for ``.get``, so a
    half-filled mapping is a producer bug that must surface here.

    Args:
        mapping: The per-side component to check.
        field: The field name, for the message.

    Raises:
        RecordFormatError: If either side is missing.
    """

    if set(mapping) != set(TeamSide):
        raise RecordFormatError(
            f"{field} must name both sides, got {sorted(str(s) for s in mapping)}"
        )


# ----------------------------------------------------------------------
# Value objects
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Seat:
    """Who sat in one seat.

    Attributes:
        id: The table's own identifier for the occupant, or ``None``.
        name: The display name, or the strategy name for an AI seat.
        account: The account identifier behind the seat, or ``None``.
        kind: Whether the seat held a person, a strategy, or somebody at
            a table we merely watched.
        level: The table's skill grading for the occupant, or ``None``.
    """

    id: str | None
    name: str
    account: str | None
    kind: SeatKind
    level: str | None


@dataclass(frozen=True, slots=True)
class ObservedFrom:
    """Where a mid-game join landed.

    Present only when the record does not start at the first deal. A
    spectator sitting down mid-game cannot rescue the round already in
    progress — the wire's state snapshot describes the last *completed*
    round — so recording starts at the next deal and this says what was
    missed.

    Attributes:
        round: The deal number in progress when we joined.
        phase: Whether that round was bidding or playing.
        totals: The running score at the moment of joining.
    """

    round: int
    phase: JoinPhase
    totals: Mapping[TeamSide, int]

    def __post_init__(self) -> None:
        """Reject a one-sided running total.

        Raises:
            RecordFormatError: If ``totals`` names only one side.
        """

        _require_both_sides(self.totals, "observed_from.totals")


@dataclass(frozen=True, slots=True)
class Ruleset:
    """The table's rules, by name and in full.

    Attributes:
        preset: The preset the table was set up from — ``classic`` for
            the engine's default, ``tournament`` for an observed table.
        config: The knob-by-knob ruleset the game was actually played
            under. Authoritative; ``preset`` is a label.
    """

    preset: str
    config: RuleConfig


@dataclass(frozen=True, slots=True)
class SideMark:
    """What one side wrote on the score sheet for a round.

    Attributes:
        made: Points marked for tricks actually taken.
        announced: Points marked for the contract announced.
    """

    made: int
    announced: int


@dataclass(frozen=True, slots=True)
class ContractTerms:
    """The contract as the score line states it.

    The score sheet's own words, not a derivation: what the declaring
    side committed to and at what multiplier. The *derived* contract —
    read back off the auction — is
    :class:`~contrai_core.ObservedContract`, built by
    :mod:`contrai_data.projection`; the two never stand in for each
    other.

    Attributes:
        value: The contracted amount, or a
            :class:`~contrai_core.SlamLevel`.
        suit: The trump the contract named.
        multiplier: 1, 2 or 4 — plain, doubled, redoubled.
    """

    value: int | SlamLevel
    suit: ContractSuit
    multiplier: int


# ----------------------------------------------------------------------
# The eight events
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Header:
    """The record's first line: what this file is and who wrote it.

    Attributes:
        format: ``family/major``, e.g. ``contrai-record/2``. The major
            version is what a loader refuses when it cannot read it.
        source: Whether the game was played or watched.
        generator: The producing program and its version.
        game_id: The record's identity, and its file name's stem.
        created_at: When the file was opened, in UTC.
    """

    format: str
    source: RecordSource
    generator: str
    game_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GameStarted:
    """The table: its rules, its four seats, and where we joined.

    Attributes:
        ruleset: The rules the game is played under.
        seats: Every seat's occupant, keyed by position.
        observed_from: Where a mid-game join landed, or ``None`` when the
            record starts at the first deal.
        ts: When the game started, in UTC.
    """

    ruleset: Ruleset
    seats: Mapping[Position, Seat]
    observed_from: ObservedFrom | None
    ts: str

    def __post_init__(self) -> None:
        """Reject a seating that is not the four seats of a table.

        Raises:
            RecordFormatError: If ``seats`` does not name all four positions.
        """

        if set(self.seats) != set(Position):
            raise RecordFormatError(
                f"A game seats four seats, got {sorted(str(s) for s in self.seats)}"
            )


@dataclass(frozen=True, slots=True)
class RoundDealt:
    """A deal: the round's number, its dealer and all four hands.

    Attributes:
        round: The source's deal count. Monotonic, but it need not start
            at one and need not be contiguous — a spectator joining mid-
            game skips the round it walked in on.
        dealer: The seat that dealt.
        hands: Each seat's eight cards.
        hands_derivation: How the hands were arrived at.
        ts: When the deal happened, in UTC.
    """

    round: int
    dealer: Position
    hands: Mapping[Position, tuple[Card, ...]]
    hands_derivation: HandsDerivation
    ts: str

    def __post_init__(self) -> None:
        """Reject a deal that is not 4 x 8 distinct cards.

        The check that catches a mis-decoded deal blob: a parser that repeats
        or drops a packet fails here rather than three steps later in a
        legality check, where the symptom no longer names the cause.

        Raises:
            RecordFormatError: If a seat is missing, a hand is not eight
                cards, or the four hands do not hold 32 distinct cards.
        """

        if set(self.hands) != set(Position):
            raise RecordFormatError(
                f"A deal fills four seats, got {sorted(str(s) for s in self.hands)}"
            )
        for position, hand in self.hands.items():
            if len(hand) != 8:
                raise RecordFormatError(
                    f"A hand holds eight cards, {position} holds {len(hand)}"
                )
        dealt = [card for hand in self.hands.values() for card in hand]
        if len(set(dealt)) != 32:
            raise RecordFormatError(
                f"A deal holds 32 distinct cards, got {len(set(dealt))}"
            )


@dataclass(frozen=True, slots=True)
class BidMade:
    """One bid, in the auction's own order.

    Attributes:
        round: The round the auction belongs to.
        seq: The bid's 1-based position in that auction, gapless.
        position: The seat that bid.
        bid: The bid itself, seated on a bare
            :class:`~contrai_core.Position`.
        think_ms: How long the seat took, or ``None`` when unknown.
        ts: When the bid was made, in UTC.
    """

    round: int
    seq: int
    position: Position
    bid: Bid[Position]
    think_ms: int | None
    ts: str

    def __post_init__(self) -> None:
        """Reject a bid filed under a seat other than its bidder's.

        ``position`` and ``bid.player`` are the same fact written twice —
        the format spells both out, so the one place they can disagree is
        here.

        Raises:
            RecordFormatError: If ``seq`` is not 1-based, or the bid's own
                seat is not ``position``.
        """

        if self.seq < 1:
            raise RecordFormatError(f"Bid seq is 1-based, got {self.seq}")
        if self.bid.player is not self.position:
            raise RecordFormatError(
                f"Bid filed under seat {self.position} was made by "
                f"{self.bid.player}"
            )


@dataclass(frozen=True, slots=True)
class CardPlayed:
    """One card, in the round's own play order.

    Attributes:
        round: The round the card was played in.
        trick: Which of the eight tricks, 1-based.
        position: The seat that played it.
        card: The card.
        derived: Whether the play was reconstructed rather than seen —
            the last trick is often deducible from what is left rather
            than reported.
        think_ms: How long the seat took, or ``None`` when unknown.
        ts: When the card was played, in UTC.
    """

    round: int
    trick: int
    position: Position
    card: Card
    derived: bool
    think_ms: int | None
    ts: str

    def __post_init__(self) -> None:
        """Reject a trick number off the eight-trick ladder.

        Raises:
            RecordFormatError: If ``trick`` is not between 1 and 8.
        """

        if not 1 <= self.trick <= 8:
            raise RecordFormatError(
                f"A round has eight tricks, numbered 1 to 8; got {self.trick}"
            )


@dataclass(frozen=True, slots=True)
class BeloteHeld:
    """A King and Queen of one suit, held by one seat.

    Possession and declaration are separate facts: a seat can hold the
    pair and never call it, which is why ``announced`` is nullable rather
    than ``False`` by default.

    Attributes:
        round: The round the pair was held in.
        position: The seat holding it.
        cards: The King and the Queen, in that order.
        announced: Whether it was declared, or ``None`` when the record
            only establishes possession.
        ts: When the pair was observed, in UTC.
    """

    round: int
    position: Position
    cards: tuple[Card, ...]
    announced: bool | None
    ts: str

    def __post_init__(self) -> None:
        """Reject a pair that is not a belote.

        A belote is the King and the Queen of one suit — under all trump a
        seat can hold several, but each is its own event.

        Raises:
            RecordFormatError: If ``cards`` is not the King and the Queen of
                a single suit.
        """

        ranks = {card.rank for card in self.cards}
        suits = {card.suit for card in self.cards}
        if len(self.cards) != 2 or ranks != {Rank.KING, Rank.QUEEN} or len(suits) != 1:
            raise RecordFormatError(
                "A belote is the King and the Queen of one suit, got "
                f"{[str(card) for card in self.cards]}"
            )


@dataclass(frozen=True, slots=True)
class RoundScored:
    """The round's score line, as its source stated it.

    Absent entirely when no score source covered the round — a game we
    joined mid-play, or left before the scoreboard updated, simply has
    rounds whose score is unknown. A zero-filled score line would be a
    claim; no line at all is the truth.

    Attributes:
        round: The round being scored.
        outcome: How it resolved.
        declarer: The seat that owned the contract, or ``None`` when the
            round was passed out.
        contract: The contract's terms, or ``None`` when passed out.
        taken: Card points each side took.
        belote: Belote points each side marked.
        announcements: Announcement points each side marked.
        carried_over: Points each side was paid from earlier rounds — a
            held dispute's pot — or ``None`` when the source could not say.
        marked: What each side wrote on the sheet, split made / announced.
        totals: The running score after the round, or ``None`` when the
            source did not report one.
        last_trick: The side that took the last trick, or ``None``.
        slam: Whether the round swept, and whether that was called.
        source: Where the line was read from.
        ts: When the round was scored, in UTC.
    """

    round: int
    outcome: RoundOutcome
    declarer: Position | None
    contract: ContractTerms | None
    taken: Mapping[TeamSide, int]
    belote: Mapping[TeamSide, int]
    announcements: Mapping[TeamSide, int]
    carried_over: Mapping[TeamSide, int] | None
    marked: Mapping[TeamSide, SideMark]
    totals: Mapping[TeamSide, int] | None
    last_trick: TeamSide | None
    slam: SlamOutcome
    source: ScoreSource
    ts: str

    def __post_init__(self) -> None:
        """Reject a score line that contradicts itself.

        Two agreements are checked. A contract and a declarer stand or fall
        together — an anonymous contract is unrepresentable, mirroring
        :class:`~contrai_core.Contract`'s own refusal of an anonymous
        double. And ``all_pass`` is exactly the outcome with no contract:
        any other outcome needs one, and a round with one was not passed
        out.

        Raises:
            RecordFormatError: If the declarer and the contract disagree, if
                the outcome and the contract disagree, or if a per-side
                component does not name both sides.
        """

        if (self.declarer is None) != (self.contract is None):
            raise RecordFormatError(
                "A contract and its declarer stand or fall together, got "
                f"declarer={self.declarer}, contract={self.contract}"
            )
        if (self.outcome is RoundOutcome.ALL_PASS) != (self.contract is None):
            raise RecordFormatError(
                f"Outcome all_pass is exactly the round with no contract, got "
                f"outcome={self.outcome}, contract={self.contract}"
            )
        for field, mapping in (
            ("taken", self.taken),
            ("belote", self.belote),
            ("announcements", self.announcements),
            ("marked", self.marked),
        ):
            _require_both_sides(mapping, field)
        if self.totals is not None:
            _require_both_sides(self.totals, "totals")
        if self.carried_over is not None:
            _require_both_sides(self.carried_over, "carried_over")


@dataclass(frozen=True, slots=True)
class GameEnded:
    """The record's last line: why it stops, and where the score stood.

    Both ``totals`` and ``ts`` are nullable, and for the same reason: a
    game we walked away from reports neither a final score nor an end
    instant, because the wire never sent one. A ``null`` says "unknown"
    and cannot be mistaken for a measurement.

    Attributes:
        totals: The final running score, or ``None`` when unreported.
        winner: The side that won, or ``None`` when the game had none or
            we did not see it.
        reason: Why the record stops.
        ts: When the game ended, in UTC, or ``None``.
    """

    totals: Mapping[TeamSide, int] | None
    winner: TeamSide | None
    reason: EndReason
    ts: str | None

    def __post_init__(self) -> None:
        """Reject a one-sided total.

        Raises:
            RecordFormatError: If ``totals`` names only one side.
        """

        if self.totals is not None:
            _require_both_sides(self.totals, "totals")


#: Every event a record can hold. The codec dispatches on it and the
#: projection matches over it, so a new event kind is a change both must
#: see.
GameEvent = (
    Header
    | GameStarted
    | RoundDealt
    | BidMade
    | CardPlayed
    | BeloteHeld
    | RoundScored
    | GameEnded
)
