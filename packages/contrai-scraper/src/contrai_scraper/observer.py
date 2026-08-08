"""Observation of a Contrée table the browser is already spectating.

Everything here reads the DOM of a seated spectator view: who is playing, and
which round is on screen. Bidding and card-play observation are still
``# FUTURE LOGIC`` placeholders.
"""

import re

from playwright.async_api import Page

#: Cardinal element ids the site uses for the four seats.
SEAT_IDS = ("nord", "sud", "est", "ouest")

#: Sentinel returned by :func:`get_current_round` when the round number cannot
#: be read — either the UI has not rendered yet, or the table changed layout.
UNKNOWN_ROUND = -1


async def get_players(page: Page) -> dict[str, str]:
    """Extracts the player names sitting at the four cardinal positions.

    Args:
        page: Page showing a table in spectator mode.

    Returns:
        Mapping of seat id (``nord``/``sud``/``est``/``ouest``) to the displayed
        player name, or ``"Unknown"`` for a seat whose badge could not be read.
    """
    players: dict[str, str] = {}

    print("👥 Identifying players...")
    for seat in SEAT_IDS:
        # The badge selector is built per seat: #nord div[data-role="badge"]
        selector = f"#{seat} div[data-role='badge']"

        try:
            players[seat] = await page.inner_text(selector, timeout=2000)
        except Exception:
            players[seat] = "Unknown"
            print(f"⚠️ Could not find player name for {seat}")

    print(f"✅ Players found: {players}")
    return players


def is_game_scrapeable(players: dict[str, str]) -> bool:
    """Decides whether a table is worth recording.

    Args:
        players: Seat-to-name mapping as returned by :func:`get_players`.

    Returns:
        ``True`` while every table is accepted. Future logic will check the
        store for games from these players that were already recorded.
    """
    return True


async def get_current_round(page: Page) -> int:
    """Reads the round number currently displayed by the table.

    Args:
        page: Page showing a table in spectator mode.

    Returns:
        The round number, or :data:`UNKNOWN_ROUND` when the label is missing or
        holds no digits.
    """
    try:
        text_content = await page.inner_text('#tour label[data-i18n="gui.scores.tour"]')
        # The label reads like "TOUR 11" — the first integer is the round.
        match = re.search(r"\d+", text_content)
        if match:
            return int(match.group())
    except Exception:
        pass
    return UNKNOWN_ROUND


async def wait_for_new_round(page: Page, current_round: int) -> int:
    """Blocks until the table displays a round number other than the given one.

    Args:
        page: Page showing a table in spectator mode.
        current_round: Round number already known to the caller.

    Returns:
        The newly detected round number.
    """
    print(f"⏳ Waiting for round change (Current: {current_round})...")

    while True:
        detected_round = await get_current_round(page)

        if detected_round != UNKNOWN_ROUND and detected_round != current_round:
            print(f"🔔 NEW ROUND DETECTED: Round {detected_round}")
            return detected_round

        await page.wait_for_timeout(1000)


async def observe_game(page: Page) -> None:
    """Watches a table indefinitely, announcing every new round.

    Identifies the players, then polls the round counter. Recording only starts
    at the next round boundary, so a round already half-played when the scraper
    sits down is skipped rather than captured partially.

    Args:
        page: Page showing a table in spectator mode.
    """
    print("\n👁️ STARTING GAME OBSERVER...")

    players = await get_players(page)

    if not is_game_scrapeable(players):
        print("⛔ Game skipped (Criteria not met).")
        return

    last_known_round = await get_current_round(page)
    print(f"ℹ️  Initial Round detected: {last_known_round}")
    print("⏳ Waiting for the NEXT round to start recording fresh data...")

    while True:
        current_round = await get_current_round(page)

        if current_round != UNKNOWN_ROUND and current_round != last_known_round:
            print("\n" + "=" * 40)
            print(f"🎬 ROUND {current_round} STARTED - RECORDING")
            print("=" * 40)

            # --- FUTURE LOGIC FOR BIDDING/PLAY WILL GO HERE ---
            # await observe_bidding(page)
            # await observe_gameplay(page)
            # --------------------------------------------------

            last_known_round = current_round

        # Small pause to prevent CPU burn
        await page.wait_for_timeout(1000)
