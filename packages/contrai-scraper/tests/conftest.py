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
#: The seat map is **mirrored on purpose**: ``right`` is West and ``left`` is
#: East. Walking ``seat_rotation`` through it yields N → W → S → E, core's own
#: cycle; a literal map would give N → E → S → W and fail the translator's
#: rotation assertion. That mirror is the P-A finding D1, in miniature.
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
login_email = "#email"
login_continue = ["#go", "#go-icon"]
code_input = "#code"
code_submit = "#submit"
mode_online = "#online"
mode_observe = "#observe"
variant = "#variant"
table_row = ".table-row"
tournament_marker = "#table-kind"
tournament_marker_text = "cup"
next_table = "#next"
options_button = "#options"
options_row = ".option-row"
options_id_attr = "data-option"
options_on_class = "on"
panel_close = "#close"
leave_table = "#leave"
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
seats = { top = "N", right = "W", bottom = "S", left = "E" }
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

[output]
root = "./out"
raw_root = "./out/raw"
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


def score_row(*, made=True, value=80, suit="wood", multiplier=1,
              taken=(90, 72), belote=(0, 0), marked=((80, 0), (0, 0))) -> dict:
    """One row of the per-round breakdown, keyed by team letter."""

    return {
        "deal": {"status": "ok" if made else "down", "level": value,
                 "suit": suit, "coeff": multiplier},
        "X": {"done": {"points": taken[0], "belotes": belote[0]},
              "marks": {"points": marked[0][0], "bid": marked[0][1]}},
        "Y": {"done": {"points": taken[1], "belotes": belote[1]},
              "marks": {"points": marked[1][0], "bid": marked[1][1]}},
    }


def deal_frame(game="g1", round_=1, cards=()) -> str:
    """The four-field key whose payload is the compressed pre-deal order."""

    blob = compress_to_base64(json.dumps({"beforeDeal": list(cards)}))
    return envelope("payload", f"{game},{round_},0,0", blob)


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
