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

from pathlib import Path

import pytest


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
