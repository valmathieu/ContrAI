"""Shared fixtures for the ``contrai-data`` suite.

Two things live here. An autouse redirect of ``CONTRAI_HOME``, because
:func:`contrai_data.records_root` resolves to the real ``~/.contrai``
otherwise and a test suite must never write there (CLAUDE.md §10). And
the hand-written three-round game the codec and the projection are both
exercised against — a made round, an all-pass round and a failed round,
spelled out rather than generated so the expected projection can be read
off the fixture by eye.

This directory deliberately has **no** ``__init__.py``, unlike
``contrai-core``'s and ``contrai-engine``'s test directories. ``pytest``
registers each ``conftest.py`` as a plugin keyed by its module name, and
inside a package that name is derived from the package — so a second
``tests`` package carrying a ``conftest.py`` registers as ``tests.conftest``
a second time and a whole-workspace ``uv run pytest`` aborts collection
with "Plugin already registered under a different name". Core's ``tests``
is a package *and* has a ``conftest.py``; engine's is a package with none.
Leaving this one a plain directory is what lets all three suites be
collected in one run.
"""

from __future__ import annotations

import pytest
from contrai_core import (
    Card,
    ContractBid,
    PassBid,
    Position,
    Rank,
    RuleConfig,
    Suit,
    TeamSide,
)

from contrai_data import (
    FORMAT,
    BidMade,
    CardPlayed,
    ContractTerms,
    EndReason,
    GameEnded,
    GameEvent,
    GameStarted,
    HandsDerivation,
    Header,
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

TS = "2026-09-10T18:18:15Z"

#: Seats in the order the deal below hands suits out: North holds the
#: spades, West the hearts, South the diamonds, East the clubs. A
#: segregated deal is not a plausible shuffle, and that is the point —
#: every trick then has exactly one card of each suit, so who wins each
#: trick is decided by the contract alone and can be read off by eye.
_SUIT_OF = dict(zip(Position, Suit, strict=True))


@pytest.fixture(autouse=True)
def contrai_home(tmp_path, monkeypatch):
    """Point ``CONTRAI_HOME`` at a scratch directory for every test.

    ``records_root()`` reads it, and a suite that resolved to the real
    ``~/.contrai`` would write records into the developer's own corpus.

    Returns:
        The scratch home directory.
    """

    home = tmp_path / "contrai-home"
    home.mkdir()
    monkeypatch.setenv("CONTRAI_HOME", str(home))
    return home


def deal() -> dict[Position, tuple[Card, ...]]:
    """The 32-card deck split one suit per seat, ranks in enum order."""

    return {
        position: tuple(Card(suit, rank) for rank in Rank)
        for position, suit in _SUIT_OF.items()
    }


@pytest.fixture
def dealt_hands() -> dict[Position, tuple[Card, ...]]:
    """The fixture game's deal, for a test that has to forge another round.

    Exposed as a fixture rather than imported from this module: pytest runs
    under ``--import-mode=importlib`` (root ``pyproject.toml``) precisely
    because three packages ship a top-level ``tests`` package, so a test
    module importing its own ``conftest`` by name is not a safe move.

    Returns:
        Seat to its eight cards.
    """

    return deal()


def _plays(number: int) -> list[CardPlayed]:
    """Eight tricks, each seat playing its ``k``-th card in seat order.

    Deliberately **not** a legal line of play: following suit is
    impossible when every seat holds one suit. The projection is a
    grouping-and-derivation layer and checks no legality — that is
    ``PlayState``'s job in core, and the verifier's in the replay step —
    so the fixture buys predictability instead. North leads every trick,
    which makes the trick winner a pure function of the trump.
    """

    hands = deal()
    return [
        CardPlayed(
            round=number,
            trick=trick,
            position=position,
            card=hands[position][trick - 1],
            derived=(trick == 8),
            think_ms=None,
            ts=TS,
        )
        for trick in range(1, 9)
        for position in Position
    ]


@pytest.fixture
def three_round_game() -> list[GameEvent]:
    """A made round, an all-pass round and a failed round.

    Round 1 — North declares 80 spades and holds every spade, so North
    trumps all eight tricks and the contract is made, with the declaring
    side sweeping (an unannounced Slam).

    Round 2 — four passes, no cards played.

    Round 3 — North declares 80 hearts but West holds every heart, so
    West trumps all eight tricks and the declarer takes nothing: failed.

    Returns:
        The events in file order, header first.
    """

    events: list[GameEvent] = [
        Header(
            format=FORMAT,
            source=RecordSource.ENGINE,
            generator="contrai-engine 0.4.0",
            game_id="engine-20260910T181815Z-a1b2c3",
            created_at=TS,
        ),
        GameStarted(
            ruleset=Ruleset(preset="classic", config=RuleConfig()),
            seats={
                position: Seat(
                    id=None,
                    name="ai:expert",
                    account=None,
                    kind=SeatKind.AI,
                    level=None,
                )
                for position in Position
            },
            observed_from=None,
            ts=TS,
        ),
    ]

    # --- Round 1: made, North declares 80 spades ---
    events.append(
        RoundDealt(
            round=1,
            dealer=Position.EAST,
            hands=deal(),
            hands_derivation=HandsDerivation.SELF_PLAY,
            ts=TS,
        )
    )
    auction_1 = [
        ContractBid(player=Position.NORTH, value=80, suit=Suit.SPADES),
        PassBid(player=Position.WEST),
        PassBid(player=Position.SOUTH),
        PassBid(player=Position.EAST),
    ]
    events += [
        BidMade(
            round=1, seq=seq, position=bid.player, bid=bid, think_ms=None, ts=TS
        )
        for seq, bid in enumerate(auction_1, start=1)
    ]
    events += _plays(1)
    events.append(
        RoundScored(
            round=1,
            outcome=RoundOutcome.MADE,
            declarer=Position.NORTH,
            contract=ContractTerms(value=80, suit=Suit.SPADES, multiplier=1),
            taken={TeamSide.NS: 162, TeamSide.EW: 0},
            belote={TeamSide.NS: 0, TeamSide.EW: 0},
            announcements={TeamSide.NS: 0, TeamSide.EW: 0},
            carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
            marked={
                TeamSide.NS: SideMark(made=162, announced=80),
                TeamSide.EW: SideMark(made=0, announced=0),
            },
            totals={TeamSide.NS: 242, TeamSide.EW: 0},
            last_trick=TeamSide.NS,
            slam=SlamOutcome.UNANNOUNCED,
            source=ScoreSource.ENGINE,
            ts=TS,
        )
    )

    # --- Round 2: passed out ---
    events.append(
        RoundDealt(
            round=2,
            dealer=Position.NORTH,
            hands=deal(),
            hands_derivation=HandsDerivation.SELF_PLAY,
            ts=TS,
        )
    )
    events += [
        BidMade(
            round=2,
            seq=seq,
            position=position,
            bid=PassBid(player=position),
            think_ms=None,
            ts=TS,
        )
        for seq, position in enumerate(Position, start=1)
    ]
    events.append(
        RoundScored(
            round=2,
            outcome=RoundOutcome.ALL_PASS,
            declarer=None,
            contract=None,
            taken={TeamSide.NS: 0, TeamSide.EW: 0},
            belote={TeamSide.NS: 0, TeamSide.EW: 0},
            announcements={TeamSide.NS: 0, TeamSide.EW: 0},
            carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
            marked={
                TeamSide.NS: SideMark(made=0, announced=0),
                TeamSide.EW: SideMark(made=0, announced=0),
            },
            totals={TeamSide.NS: 242, TeamSide.EW: 0},
            last_trick=None,
            slam=SlamOutcome.NONE,
            source=ScoreSource.ENGINE,
            ts=TS,
        )
    )

    # --- Round 3: failed, North declares 80 hearts into West's hand ---
    events.append(
        RoundDealt(
            round=3,
            dealer=Position.WEST,
            hands=deal(),
            hands_derivation=HandsDerivation.SELF_PLAY,
            ts=TS,
        )
    )
    auction_3 = [
        ContractBid(player=Position.NORTH, value=80, suit=Suit.HEARTS),
        PassBid(player=Position.WEST),
        PassBid(player=Position.SOUTH),
        PassBid(player=Position.EAST),
    ]
    events += [
        BidMade(
            round=3, seq=seq, position=bid.player, bid=bid, think_ms=None, ts=TS
        )
        for seq, bid in enumerate(auction_3, start=1)
    ]
    events += _plays(3)
    events.append(
        RoundScored(
            round=3,
            outcome=RoundOutcome.FAILED,
            declarer=Position.NORTH,
            contract=ContractTerms(value=80, suit=Suit.HEARTS, multiplier=1),
            taken={TeamSide.NS: 0, TeamSide.EW: 162},
            belote={TeamSide.NS: 0, TeamSide.EW: 0},
            announcements={TeamSide.NS: 0, TeamSide.EW: 0},
            carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
            marked={
                TeamSide.NS: SideMark(made=0, announced=0),
                TeamSide.EW: SideMark(made=162, announced=80),
            },
            totals={TeamSide.NS: 242, TeamSide.EW: 242},
            last_trick=TeamSide.EW,
            slam=SlamOutcome.NONE,
            source=ScoreSource.ENGINE,
            ts=TS,
        )
    )

    events.append(
        GameEnded(
            totals={TeamSide.NS: 242, TeamSide.EW: 242},
            winner=None,
            reason=EndReason.INTERRUPTED,
            ts=TS,
        )
    )
    return events
