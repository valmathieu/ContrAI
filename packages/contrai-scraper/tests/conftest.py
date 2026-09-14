"""Fixtures for the scraper suite.

This directory deliberately has **no** ``__init__.py``, unlike
``contrai-core``'s and ``contrai-engine``'s test directories: ``pytest``
registers each ``conftest.py`` as a plugin keyed by its module name, and a
second ``tests`` *package* carrying one aborts a whole-workspace run with
"Plugin already registered under a different name". Leaving this a plain
directory is what lets all four suites be collected in one run.

Helper data is exposed as fixtures rather than imported by name, because the
root ``pyproject.toml`` runs pytest with ``--import-mode=importlib`` and
``from conftest import ...`` is not reliable under it.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from contrai_core import (
    PRESETS,
    Card,
    ContractBid,
    DoubleBid,
    ObservedPlay,
    PassBid,
    Position,
    Rank,
    RedoubleBid,
    SlamLevel,
    Suit,
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

from contrai_scraper import compress_to_base64


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """An output root for records and raw logs, outside the real corpus.

    Nothing in this suite may write to the machine's records root: it holds
    personal data until roadmap step A.

    Returns:
        A freshly created scratch directory.
    """

    root = tmp_path / "scraper-root"
    root.mkdir()
    return root


#: The fixture profile, in a vocabulary that exists nowhere but this suite.
#:
#: Spec §7.5: no tracked file may carry a string the target site uses, so the
#: tests invent their own site. Seats are ``top/right/bottom/left``, ranks are
#: the digits ``2..9``, suits are ``w/x/y/z``, teams are ``X``/``Y`` and the
#: verbs are ``card`` and ``bid:``. Every assertion in the suite reads against
#: this vocabulary, which is what keeps the code free of the real one.
#:
#: The seat map is **literal**: ``right`` is East and ``left`` is West, where
#: they sit on screen. Walking ``seat_rotation`` through it yields
#: N → E → S → W, which is clockwise — the direction the ``tournament`` preset
#: named under ``[rules]`` plays. A map with the two side seats crossed would
#: walk anticlockwise and fail the translator's rotation check.
PROFILE_TEXT = """
[site]
url = "https://example.invalid/lobby"
locale = "xx"

[account]
email = "watcher@example.invalid"
verification_code = "0000"

[browser]
headless = true
slow_mo_ms = 0
screenshot_on_error = false

[selectors]
dismiss_tutorial = "#no-thanks"
login_start = ["#by-email", "#by-email-icon"]
login_email = "#email"
login_continue = ["#go", "#go-icon"]
code_input = "#code"
code_submit = "#submit"
mode_online = "#online"
mode_observe = "#observe"
variant = "#variant"
pledge_dialog = "#pledge"
pledge_accept = "#pledge-ok"
tournament_marker = "#table-kind"
tournament_marker_text = "cup"
next_table = "#next"
rail_show = "#rail-in:visible"
options_button = "#options"
options_row = ".option-row"
options_id_element = ".option-name"
options_state_element = ".option-switch"
options_id_attr = "data-option"
options_on_class = "on"
panel_close = "#close"
scoreboard_button = "#scoreboard"
scoreboard_row = ".score-row"
scoreboard_cell = ".score-cell"
seat_element = "#seat-{seat}"
player_panel = ".player-panel"
player_id_title = ".player-panel .title"
player_id_prefix = "no. "

[wire]
socket_url_pattern = "^wss://example\\\\.invalid/sock/\\\\d+$"
game_envelope_kind = "payload"
keepalive_frame = "tick"
key_fields = ["game", "round", "trick", "position", "verb", "player"]
play_verb = "card"
bid_verb_prefix = "bid:"
deal_key_arity = 4
round_state_prefix = "round."
resume_action = "resume"
resume_room_prefix = "room-"
resume_param = "lastSeen"

[wire.events]
join_snapshot = "joinTable"
table_update = "updateTable"
counters = "counters"

# Dotted paths, each resolved relative to the payload it is looked up in:
# the table/state names walk from a snapshot's root, the player names from one
# player block, the row names from one score row, and think_ms / received_ms
# from an event's metadata block. The set of *names* is fixed and spans both
# scraper halves — the browser half reads `spectators`, `observable_tables`,
# `hands`, `tricks` and `auction` — so the local profile is written once. An
# unknown name raises; a missing one raises.
[wire.fields]
table = "table"
table_id = "table.id"
is_tournament = "table.cup"
seats = "table.seats"
seat_id = "id"
seat_placement = "spot"
state = "state"
players = "state.people"
player_id = "id"
player_name = "label"
player_account = "acct"
player_level = "grade"
player_kind = "sort"
team = "side.team"
round_index = "n"
dealer = "giver"
trump = "suit"
deck_order = "beforeDeal"
hands = "hands"
tricks = "tricks"
auction = "bids"
scores = "score.rows"
totals = "score.by_team"
row_status = "deal.status"
row_value = "deal.level"
row_suit = "deal.suit"
row_multiplier = "deal.coeff"
row_declarer = "deal.taker"
row_winner = "deal.winner"
side_taken = "done.points"
side_belote = "done.belotes"
side_marked_made = "marks.points"
side_marked_announced = "marks.bid"
bid_owner = "who"
bid_suit = "colour"
bid_value = "level"
doubler = "twice"
redoubler = "fourfold"
think_ms = "ms.v"
turn_limit_ms = "ms.m"
received_ms = "at"
game_id = "game"
ended = "over"
left = "gone"
spectators = "watchers"
observable_tables = "tables"

[wire.tokens]
seats = { top = "N", right = "E", bottom = "S", left = "W" }
seat_rotation = ["top", "right", "bottom", "left"]
ranks = { "2" = "7", "3" = "8", "4" = "9", "5" = "10", "6" = "J", "7" = "Q", "8" = "K", "9" = "A" }
suits = { w = "S", x = "H", y = "D", z = "C" }
suit_words = { wood = "S", water = "H", wind = "D", wool = "C" }
team_letters = ["X", "Y"]
bid_slam = "BIG"
bid_solo_slam = "BIGGER"
pass_is_null = true
score_made = "ok"

[rules]
preset = "tournament"

[rules.options]
opt_alpha = true
opt_beta = false

[recorder]
hop_after_rows = 8
stale_after_s = 180
health_interval_s = 60
snapshot_timeout_s = 30

[schedule]
timezone = "Europe/Paris"
active = ["00:00-24:00"]
finish_current_game = true
max_overrun_minutes = 30
idle_poll_minutes = 5

[egress]
home_ip = "198.51.100.1"
expected_country = "XX"
probe_url = "https://probe.invalid/ip.json"
probe_ip_field = "addr"
probe_country_field = "land"

[output]
# Both roots name the same directory: game_path appends games/ and raw_path
# appends raw/.
root = "./out"
raw_root = "./out"
raw_retention_days = 30

[privacy]
pseudonym_salt = "unused-in-4a"
"""


@pytest.fixture
def profile_text() -> str:
    """The fixture profile as TOML text, for mutation in refusal tests."""

    return PROFILE_TEXT


@pytest.fixture
def profile_path(tmp_path: Path, profile_text: str) -> Path:
    """The fixture profile written to disk.

    Never named ``profile.toml``: the root ``.gitignore`` ignores that name at
    any depth, so a file by that name inside the repo would be invisible.

    Returns:
        The path the profile was written to.
    """

    path = tmp_path / "fixture-profile.toml"
    path.write_text(profile_text, encoding="utf-8")
    return path


@pytest.fixture
def profile(profile_path: Path):
    """The parsed fixture profile.

    Returns:
        The :class:`~contrai_scraper.Profile` the fixture document describes.
    """

    from contrai_scraper import load_profile

    return load_profile(profile_path)


# ---------------------------------------------------------------------------
# Payload builders — shared by the snapshot, live and session suites
# ---------------------------------------------------------------------------
#
# One set of shapes, used by every test that needs a frame. Three modules each
# inventing their own is the fastest route to a suite that passes per module
# and fails end to end, so a shape that is missing here is added here.
#
# Every key below comes from the ``[wire.fields]`` block of the fixture
# profile above. Change one and you change both — which is the point: the
# builders are a second reader of the same document, so a path that no longer
# resolves shows up as a failing test rather than as a silently empty field.


def envelope(kind: str, event: str, data, *, frame_id: str = "1", metadata=None) -> str:
    """Builds a frame the way the invented vocabulary spells it."""

    inner = {"event": event, "data": data}
    if metadata is not None:
        inner["metadata"] = metadata
    return json.dumps({"id": frame_id, "event": kind, "data": json.dumps(inner)})


def snapshot_payload(*, table_id="t1", round_index=2, rows=(), totals=(40, 60)) -> dict:
    """A join snapshot in the invented vocabulary.

    The round block is keyed by ``round_state_prefix`` + the game id, which is
    why the reader finds it by prefix and never by name.
    """

    return {
        "table": {
            "id": table_id,
            "cup": True,
            "seats": [
                {"id": "p1", "spot": "top"}, {"id": "p2", "spot": "right"},
                {"id": "p3", "spot": "bottom"}, {"id": "p4", "spot": "left"},
            ],
        },
        "state": {
            "people": {
                "top":    {"id": "p1", "label": "One",   "acct": "1001",
                           "grade": "7", "sort": "human", "side": {"team": "X"}},
                "right":  {"id": "p2", "label": "Two",   "acct": "1002",
                           "grade": None, "sort": "human", "side": {"team": "Y"}},
                "bottom": {"id": "p3", "label": "Three", "acct": "1003",
                           "grade": "3", "sort": "human", "side": {"team": "X"}},
                "left":   {"id": "p4", "label": "Four",  "acct": "1004",
                           "grade": "5", "sort": "human", "side": {"team": "Y"}},
            },
            "round.g1": {
                "n": round_index,
                "giver": "top",
                "suit": "wood",
                "score": {"rows": list(rows),
                          "by_team": {"X": totals[0], "Y": totals[1]}},
            },
        },
    }


def score_row(*, made=True, value=80, suit="wood", multiplier=1, declarer="X",
              taken=(90, 72), belote=(0, 0), marked=((80, 0), (0, 0))) -> dict:
    """One row of the per-round breakdown, keyed by team letter.

    The row names the declaring side and the side that won the round; the
    winner is the declarer when ``made`` and the other letter otherwise.
    """

    defence = "Y" if declarer == "X" else "X"
    return {
        "deal": {"status": "ok" if made else "down", "level": value,
                 "suit": suit, "coeff": multiplier,
                 "taker": declarer, "winner": declarer if made else defence},
        "X": {"done": {"points": taken[0], "belotes": belote[0]},
              "marks": {"points": marked[0][0], "bid": marked[0][1]}},
        "Y": {"done": {"points": taken[1], "belotes": belote[1]},
              "marks": {"points": marked[1][0], "bid": marked[1][1]}},
    }


def deal_frame(game="g1", round_=1, cards=()) -> str:
    """The four-field key whose payload is the compressed pre-deal order."""

    blob = compress_to_base64(json.dumps({"beforeDeal": list(cards)}))
    # A frame id of its own, like every other builder: two rounds whose deals
    # shared one would be de-duplicated down to a single deal, and the second
    # round would then look like one joined mid-play.
    return envelope("payload", f"{game},{round_},0,0", blob,
                    frame_id=f"d{round_}")


def bid_frame(game="g1", round_=1, seq=1, actor="p1", payload=None, at=None) -> str:
    """A bid. ``payload=None`` is a pass (``pass_is_null``)."""

    return envelope("payload", f"{game},{round_},0,{seq},bid:{seq},{actor}", payload,
                    frame_id=f"b{round_}-{seq}",
                    metadata={"at": at} if at else None)


def play_frame(game="g1", round_=1, trick=1, index=0, actor="p1",
               card="2w", think=None, at=None) -> str:
    """A card play. The fourth key field is the index within the trick."""

    metadata = {}
    if think is not None:
        metadata["ms"] = {"v": think, "m": 8000}
    if at is not None:
        metadata["at"] = at
    return envelope("payload", f"{game},{round_},{trick},{index},card,{actor}", card,
                    frame_id=f"p{round_}-{trick}-{index}",
                    metadata=metadata or None)


@pytest.fixture
def builders():
    """The payload builders, as one namespace.

    Exposed as a fixture rather than imported: pytest runs under
    ``--import-mode=importlib`` (root ``pyproject.toml``), so a test module
    importing its own ``conftest`` by name is not a safe move.

    Returns:
        A namespace carrying every builder above.
    """

    return SimpleNamespace(
        envelope=envelope,
        snapshot_payload=snapshot_payload,
        score_row=score_row,
        deal_frame=deal_frame,
        bid_frame=bid_frame,
        play_frame=play_frame,
    )



# ---------------------------------------------------------------------------
# A whole observed game, and the frames a session would have produced for it
# ---------------------------------------------------------------------------
#
# The round trip is this branch's real gate: a hand-built record becomes
# frames, the frames go through the actual stream and parser, and what comes
# back must be the record again. Everything the wire cannot carry — timestamps,
# the generator string — is dropped before the comparison; everything else has
# to survive.
#
# The source game is built from ``contrai_data`` events rather than from an
# engine autoplay run on purpose: CI installs only this package's own
# dependencies, so importing the engine here would fail there.

TS = "2026-09-11T18:18:15Z"

#: The direction the fixture profile's preset plays — clockwise.
DIRECTION = PRESETS["tournament"].turn_direction

#: The four seats in the site's own rotation — top, right, bottom, left —
#: which is core's seats walked in ``DIRECTION``.
ROTATION = (Position.NORTH, Position.EAST, Position.SOUTH, Position.WEST)

#: Player handles, in the same order. The fixture profile's seat map places
#: ``top`` at North and ``right`` at East, where they sit on screen.
HANDLES = ("p1", "p2", "p3", "p4")

#: Handle to seat, and back.
SEAT_OF_HANDLE = dict(zip(HANDLES, ROTATION, strict=True))
HANDLE_OF_SEAT = {seat: handle for handle, seat in SEAT_OF_HANDLE.items()}

#: Card to the invented vocabulary's spelling of it.
_RANK_TOKEN = dict(zip(Rank, "23456789", strict=True))
_SUIT_TOKEN = dict(zip(Suit, "wxyz", strict=True))


def card_glyph(card: Card) -> str:
    """One card, as the invented vocabulary spells it."""

    return f"{_RANK_TOKEN[card.rank]}{_SUIT_TOKEN[card.suit]}"


def _deck(offset: int = 0) -> list[Card]:
    """A deck whose order mixes the suits, rotated by ``offset``.

    Four seats each holding one whole suit would let more than one of the four
    candidate deals explain the plays, and the dealer would then resolve to
    nothing at all — the parser refuses to guess.
    """

    cards = [Card(suit, rank) for suit in Suit for rank in Rank]
    mixed = [cards[(index * 7) % 32] for index in range(32)]
    return mixed[offset:] + mixed[:offset]


def _deal(stock, dealer):
    """Deal a stock 3-2-3 from the seat after the dealer.

    Spelled out here rather than imported from the package, so the fixture is
    an independent statement of the layout and not a re-run of the code under
    test.
    """

    start = ROTATION.index(dealer.next_in(DIRECTION))
    order = [ROTATION[(start + step) % 4] for step in range(4)]
    hands = {seat: [] for seat in order}
    cursor = 0
    for packet in (3, 2, 3):
        for seat in order:
            hands[seat].extend(stock[cursor : cursor + packet])
            cursor += packet
    return {seat: tuple(cards) for seat, cards in hands.items()}


def stock_for(hands, dealer):
    """The pre-deal deck order that deals ``hands`` — the deal, inverted.

    The wire sends the stock, never the hands, so a synthesizer has to undo
    the 3-2-3 layout: walk the packets in rotation from the seat after the
    dealer, taking each seat's next cards in turn.
    """

    start = ROTATION.index(dealer.next_in(DIRECTION))
    order = [ROTATION[(start + step) % 4] for step in range(4)]
    cursor = dict.fromkeys(order, 0)
    stock = []
    for packet in (3, 2, 3):
        for seat in order:
            stock.extend(hands[seat][cursor[seat] : cursor[seat] + packet])
            cursor[seat] += packet
    return stock


def _tricks(hands, leader, trump):
    """Play every hand out, each seat playing its first remaining card.

    Not a *clever* line of play — following suit is not attempted — but a
    consistent one: each trick's winner leads the next, decided by core's own
    rule, which is what the site does and what the record's projection will
    re-derive.
    """

    remaining = {seat: list(cards) for seat, cards in hands.items()}
    tricks = []
    for _ in range(8):
        order = [leader]
        while len(order) < 4:
            order.append(order[-1].next_in(DIRECTION))
        trick = [(seat, remaining[seat].pop(0)) for seat in order]
        tricks.append(trick)
        leader = (
            TrickRecord(ObservedPlay(seat, card) for seat, card in trick)
            .winner(trump)
            .position
        )
    return tricks


def _belote_pairs(hands, trump):
    """Which seats hold the trump king and queen — a fact about the deal.

    Possession is always recordable; whether it was *announced* is not on the
    wire, so the record leaves that unknown.
    """

    for seat, cards in hands.items():
        pair = tuple(
            card for card in cards
            if card.suit == trump and card.rank in (Rank.KING, Rank.QUEEN)
        )
        if len(pair) == 2:
            yield seat, pair


def _after(declarer):
    """The three seats that are not the declarer, in rotation after it."""

    seat = declarer
    for _ in range(3):
        seat = seat.next_in(DIRECTION)
        yield seat


def round_events(number, dealer, declarer, value, suit, made, totals, *,
                 auction=None, multiplier=1):
    """One complete round of a record: deal, auction, play, score.

    ``auction`` replaces the default auction — the declarer's bid and three
    passes — with bids of its own; ``multiplier`` is the contract's, 2 when
    doubled and 4 when redoubled.
    """

    hands = _deal(_deck(number * 5), dealer)
    events = [
        RoundDealt(
            round=number,
            dealer=dealer,
            hands=hands,
            hands_derivation=HandsDerivation.DEALT_FROM_DECK,
            ts=TS,
        )
    ]
    if auction is None:
        auction = [
            ContractBid(player=declarer, value=value, suit=suit),
            *(PassBid(player=seat) for seat in _after(declarer)),
        ]
    events += [
        BidMade(round=number, seq=seq, position=bid.player, bid=bid,
                think_ms=None, ts=TS)
        for seq, bid in enumerate(auction, start=1)
    ]
    for index, trick in enumerate(_tricks(hands, dealer.next_in(DIRECTION), suit), start=1):
        events += [
            CardPlayed(round=number, trick=index, position=seat, card=card,
                       derived=index == 8, think_ms=None, ts=TS)
            for seat, card in trick
        ]
    events += [
        BeloteHeld(round=number, position=seat, cards=pair, announced=None, ts=TS)
        for seat, pair in _belote_pairs(hands, suit)
    ]
    events.append(
        RoundScored(
            round=number,
            outcome=RoundOutcome.MADE if made else RoundOutcome.FAILED,
            declarer=declarer,
            contract=ContractTerms(value=value, suit=suit, multiplier=multiplier),
            taken={TeamSide.NS: 90, TeamSide.EW: 72},
            belote={TeamSide.NS: 0, TeamSide.EW: 0},
            announcements={TeamSide.NS: 0, TeamSide.EW: 0},
            carried_over={TeamSide.NS: 0, TeamSide.EW: 0},
            marked={
                TeamSide.NS: SideMark(
                    made=90 if made else 0,
                    announced=marked_points(value) if made else 0),
                TeamSide.EW: SideMark(
                    made=0 if made else 162,
                    announced=0 if made else marked_points(value)),
            },
            totals=dict(totals),
            last_trick=None,
            slam=SlamOutcome.NONE,
            source=ScoreSource.SNAPSHOT,
            ts=TS,
        )
    )
    return events


def game_events(*rounds, reason=EndReason.OBSERVER_LEFT):
    """A whole record: header, table, the given rounds, and an ending."""

    events = [
        Header(
            format=FORMAT,
            source=RecordSource.OBSERVED,
            generator="contrai-scraper",
            game_id="obs-g1",
            created_at=TS,
        ),
        GameStarted(
            ruleset=Ruleset(preset="tournament", config=PRESETS["tournament"]),
            seats={
                seat: Seat(
                    id=f"100{index + 1}",
                    name=name,
                    account=f"100{index + 1}",
                    kind=SeatKind.OBSERVED,
                    level=level,
                )
                for index, (seat, name, level) in enumerate(
                    zip(ROTATION, ("One", "Two", "Three", "Four"),
                        ("7", None, "3", "5"), strict=True)
                )
            },
            observed_from=None,
            ts=TS,
        ),
    ]
    last_totals = None
    for round_ in rounds:
        events += round_
        for event in round_:
            if isinstance(event, RoundScored):
                last_totals = event.totals
    events.append(
        GameEnded(totals=last_totals, winner=None, reason=reason, ts=TS)
    )
    return tuple(events)


@pytest.fixture
def source_game():
    """A complete two-round observed game, as ``contrai_data`` events.

    Returns:
        The events in file order, header first.
    """

    # Each round is dealt by the seat before its declarer, so the declarer
    # speaks first and the auction is its bid and three passes.
    return game_events(
        round_events(1, Position.SOUTH, Position.WEST, 80, Suit.SPADES, True,
                     {TeamSide.NS: 0, TeamSide.EW: 170}),
        round_events(2, Position.EAST, Position.SOUTH, 110, Suit.HEARTS, True,
                     {TeamSide.NS: 200, TeamSide.EW: 170}),
    )


def _snapshot_of(seen_rounds, totals, round_index):
    """A join snapshot carrying the rounds scored so far."""

    payload = snapshot_payload(round_index=round_index, rows=seen_rounds,
                               totals=totals)
    if round_index is None:
        del payload["state"]["round.g1"]
    return payload


def synthesize_frames(events, *, game="g1", keepalive_every=5, sockets=(0, 1), silent=frozenset()):
    """The raw frame texts a session would have produced for a record.

    Everything the parser has to undo is done here: the deal goes out as a
    compressed pre-deal stock rather than as hands, the eighth trick is left
    out because it is forced, every frame is mirrored on the second socket,
    and a keepalive that is not JSON is dropped in every few frames.

    Args:
        events: A record's events, header first.
        game: The wire's own id for the game.
        keepalive_every: How often to interleave a keepalive.
        sockets: Which connections carry the traffic.
        silent: ``(round, seq)`` pairs naming forced passes the table would
            not send: their numbers are spent and no frame goes out.

    Returns:
        ``(text, socket)`` pairs, in the order they would have arrived.
    """

    by_round: dict[int, list] = {}
    scored: list = []
    ended = None
    for event in events:
        match event:
            case RoundDealt() | BidMade() | CardPlayed():
                by_round.setdefault(event.round, []).append(event)
            case RoundScored():
                by_round.setdefault(event.round, []).append(event)
                scored.append(event)
            case GameEnded():
                ended = event

    texts: list[str] = []
    rows: list = []
    # The session opens on a snapshot that knows of no completed round: this
    # game is watched from its first deal.
    texts.append(envelope("payload", "joinTable", _snapshot_of((), (0, 0), None),
                          frame_id="s0"))

    for number in sorted(by_round):
        round_events_ = by_round[number]
        seq = 0
        for event in round_events_:
            match event:
                case RoundDealt():
                    texts.append(deal_frame(
                        game=game, round_=number,
                        cards=[card_glyph(card)
                               for card in stock_for(event.hands, event.dealer)]))
                case BidMade():
                    seq += 1
                    if (number, seq) in silent:
                        # A forced pass: the table spends the number and
                        # sends nothing.
                        continue
                    texts.append(bid_frame(
                        game=game, round_=number, seq=seq,
                        actor=HANDLE_OF_SEAT[event.position],
                        payload=_bid_payload(event, round_events_)))
                case CardPlayed() if not event.derived:
                    texts.append(play_frame(
                        game=game, round_=number, trick=event.trick,
                        index=_index_in_trick(round_events_, event),
                        actor=HANDLE_OF_SEAT[event.position],
                        card=card_glyph(event.card)))
                case RoundScored():
                    rows.append(_wire_row(event))
                    texts.append(envelope(
                        "payload", "joinTable",
                        _snapshot_of(tuple(rows),
                                     (event.totals[TeamSide.NS],
                                      event.totals[TeamSide.EW]),
                                     number),
                        frame_id=f"s{number}"))

    if ended is not None:
        # The table says one of two things, and they are not the same: the
        # game finished at the table, or the spectator stopped watching a game
        # that had not.
        flag = ("over" if ended.reason is EndReason.TARGET_REACHED
                else "gone")
        texts.append(envelope("payload", "updateTable", {flag: 1},
                              frame_id="end"))

    paired: list[tuple[str, int]] = []
    for index, text in enumerate(texts):
        if keepalive_every and index and index % keepalive_every == 0:
            paired.append(("tick", sockets[0]))
        for socket in sockets:
            paired.append((text, socket))
    return paired


def _index_in_trick(round_events_, played):
    """Where in its trick a play fell, which is what the key's fourth slot is."""

    trick = [
        event for event in round_events_
        if isinstance(event, CardPlayed) and event.trick == played.trick
    ]
    return trick.index(played)


def marked_points(value):
    """What a contract is worth as a mark: a slam has a base value of its own."""

    return value.base_value if isinstance(value, SlamLevel) else value


def value_token(value):
    """A contract value as the wire spells it: a number, or a slam token."""

    return {SlamLevel.SLAM: "BIG", SlamLevel.SOLO_SLAM: "BIGGER"}.get(value, value)


def _bid_payload(made, round_events_):
    """The payload the wire would have sent for one bid.

    A double and a redouble repeat the contract under attack and add who
    attacked it; a later payload keeps those fields.
    """

    bid = made.bid
    if isinstance(bid, PassBid):
        return None
    auction = [event for event in round_events_ if isinstance(event, BidMade)]
    contract = next(
        event.bid for event in auction if isinstance(event.bid, ContractBid)
    )
    payload = {
        "who": HANDLE_OF_SEAT[contract.player],
        "colour": _SUIT_TOKEN[contract.suit],
        "level": value_token(contract.value),
    }
    for event in auction[: auction.index(made) + 1]:
        if isinstance(event.bid, DoubleBid):
            payload["twice"] = HANDLE_OF_SEAT[event.position]
        elif isinstance(event.bid, RedoubleBid):
            payload["fourfold"] = HANDLE_OF_SEAT[event.position]
    return payload


def _wire_row(scored):
    """The score row the wire would have sent for one ``RoundScored``."""

    return score_row(
        made=scored.outcome is RoundOutcome.MADE,
        value=value_token(scored.contract.value),
        suit=_SUIT_TOKEN[scored.contract.suit],
        multiplier=scored.contract.multiplier,
        declarer="X" if scored.declarer.team_side is TeamSide.NS else "Y",
        taken=(scored.taken[TeamSide.NS], scored.taken[TeamSide.EW]),
        belote=(scored.belote[TeamSide.NS], scored.belote[TeamSide.EW]),
        marked=(
            (scored.marked[TeamSide.NS].made, scored.marked[TeamSide.NS].announced),
            (scored.marked[TeamSide.EW].made, scored.marked[TeamSide.EW].announced),
        ),
    )


@pytest.fixture
def synthesize():
    """Turns a record into the frames a session would have produced for it.

    Returns:
        A callable taking a record's events and returning ``(text, socket)``
        pairs.
    """

    return synthesize_frames


@pytest.fixture
def session_frames(synthesize):
    """A whole game as ``RawFrame``s, ready for a fake frame source.

    Args:
        (fixtures)

    Returns:
        A callable taking the same keyword arguments as ``synthesize_frames``
        and returning ``RawFrame`` objects rather than ``(text, socket)``
        pairs, which is what a ``FrameSource`` yields.
    """

    from contrai_scraper import RECEIVED, RawFrame

    def build(events, **kwargs):
        return [
            RawFrame(socket=socket, direction=RECEIVED, at=index / 10, text=text)
            for index, (text, socket) in enumerate(synthesize(events, **kwargs))
        ]

    return build


@pytest.fixture
def game_builders():
    """The record builders, for tests that need a game of their own shape.

    Returns:
        A namespace carrying the round and game builders plus the seat maps.
    """

    return SimpleNamespace(
        round_events=round_events,
        game_events=game_events,
        card_glyph=card_glyph,
        stock_for=stock_for,
        rotation=ROTATION,
        handle_of_seat=HANDLE_OF_SEAT,
        seat_of_handle=SEAT_OF_HANDLE,
        ts=TS,
    )


@pytest.fixture
def raw_log_path(tmp_path, source_game):
    """The fixture game, written out as a raw log the CLI can re-parse.

    Written through the real :class:`RawLogWriter`, so the log the CLI reads
    is the log a session would have left — mirrored socket, keepalives and
    all.

    Returns:
        The log file.
    """

    from contrai_scraper import RawFrame, RawLogWriter, raw_path

    path = raw_path(tmp_path / "corpus", "session-1")
    with RawLogWriter(path) as log:
        for index, (text, socket) in enumerate(synthesize_frames(source_game)):
            log.write_frame(
                RawFrame(socket=socket, direction="recv", at=index / 10, text=text)
            )
    return path


@pytest.fixture
def partial_raw_log_path(tmp_path, source_game):
    """The same game with its first deal missing, so a round must be skipped.

    Returns:
        The log file.
    """

    from contrai_scraper import RawFrame, RawLogWriter, raw_path

    path = raw_path(tmp_path / "partial", "session-2")
    with RawLogWriter(path) as log:
        kept = [
            pair for pair in synthesize_frames(source_game)
            if ",1,0,0" not in pair[0]
        ]
        for index, (text, socket) in enumerate(kept):
            log.write_frame(
                RawFrame(socket=socket, direction="recv", at=index / 10, text=text)
            )
    return path
