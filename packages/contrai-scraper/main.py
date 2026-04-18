import asyncio
import sys
import re
from playwright.async_api import async_playwright
from prompt_toolkit.shortcuts import button_dialog

# --- CONFIGURATION ---
CIBLED_URL = "https://app.belote-rebelote.fr/"
EMAIL = "contrai-michel@proton.me"
FIXED_CODE = "0343"

async def get_players(page) -> dict:
    """Extracts player names from the 4 cardinal positions."""
    positions = ['nord', 'sud', 'est', 'ouest']
    players = {}

    print("👥 Identifying players...")
    for pos in positions:
        # We construct the selector dynamically: #nord div[data-role="badge"]
        selector = f"#{pos} div[data-role='badge']"

        # Wait a brief moment to ensure UI is rendered
        try:
            name = await page.inner_text(selector, timeout=2000)
            players[pos] = name
        except Exception:
            players[pos] = "Unknown"
            print(f"⚠️ Could not find player name for {pos}")

    print(f"✅ Players found: {players}")
    return players

def is_game_scrapeable(players: dict) -> bool:
    """
    Checks if we should record this game.
    Future logic: Check against a database if these players are already being scraped.
    """
    # For now, we accept everything.
    return True

async def get_current_round(page) -> int:
    """Reads the DOM to find the current round number."""
    try:
        # Selector based on your provided HTML
        text_content = await page.inner_text('#tour label[data-i18n="gui.scores.tour"]')
        # Regex to find the first number in string "TOUR 11"
        match = re.search(r'\d+', text_content)
        if match:
            return int(match.group())
    except:
        pass
    return -1  # Return -1 if not found or waiting for UI


async def wait_for_new_round(page, current_round: int) -> int:
    """Loops until the round number detected in DOM is different from current_round."""
    print(f"⏳ Waiting for round change (Current: {current_round})...")

    while True:
        detected_round = await get_current_round(page)

        # Check if round is valid and different from the one we know
        if detected_round != -1 and detected_round != current_round:
            print(f"🔔 NEW ROUND DETECTED: Round {detected_round}")
            return detected_round

        await page.wait_for_timeout(1000)  # Check every 1 second


async def observe_game(page):
    """
    Main Logic Loop:
    1. Identify Players
    2. Wait for Round Synchronization
    3. (Future) Observe Bids/Play
    """
    print("\n👁️ STARTING GAME OBSERVER...")

    # 1. Player Identification
    players = await get_players(page)

    if not is_game_scrapeable(players):
        print("⛔ Game skipped (Criteria not met).")
        return

    # 2. Synchronization Loop
    # We initialize the round as whatever is currently on screen
    last_known_round = await get_current_round(page)
    print(f"ℹ️  Initial Round detected: {last_known_round}")
    print("⏳ Waiting for the NEXT round to start recording fresh data...")

    # Infinite loop to watch the game
    is_observing = True
    while is_observing:

        # Check round number constantly
        current_round = await get_current_round(page)

        # LOGIC: Start recording only when the round number changes
        if current_round != -1 and current_round != last_known_round:
            print("\n" + "=" * 40)
            print(f"🎬 ROUND {current_round} STARTED - RECORDING")
            print("=" * 40)

            # --- FUTURE LOGIC FOR BIDDING/PLAY WILL GO HERE ---
            # await observe_bidding(page)
            # await observe_gameplay(page)
            # --------------------------------------------------

            # Update state
            last_known_round = current_round

        # Small pause to prevent CPU burn
        await page.wait_for_timeout(1000)

async def main():
    print("🚀 Bot starts...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        page = await browser.new_page()

        print(f"🌐 Navigation to {CIBLED_URL}...")
        await page.goto(CIBLED_URL)

        # --- PHASE 1: LOGIN FLOW ---
        print("⏳ Waiting for page load (Hard wait 5s)...")
        await page.wait_for_timeout(5000)

        # Handle Tutorial
        tutorial_btn = page.locator('button[data-i18n="gui.quick-start.launch.no"]')
        if await tutorial_btn.is_visible():
            await tutorial_btn.click()
            print("✅ Tutorial closed.")

        # Login Process
        print("🖱️ Logging in...")
        try:
            await page.click('button:has-text("Email")', timeout=5000)
        except:
            await page.click('button[data-icon="email"]')

        await page.fill('input[placeholder="Adresse électronique"]', EMAIL)
        await page.click('button[data-i18n="gui.users.email-wizard.continue"]')

        await page.wait_for_selector('#verificationCode', state="visible")
        await page.fill('#verificationCode', FIXED_CODE)
        await page.click('#validateBtn')

        print("✅ Login successful. Waiting for Lobby (10s)...")
        await page.wait_for_timeout(10000)

        # --- STEP 5 : ONLINE MODE ---
        bouton_online_mode = page.locator('button[data-i18n="gui.actions.mode.online"]')
        await bouton_online_mode.wait_for(state="visible", timeout=5000)
        await bouton_online_mode.click()
        print("✅ Mode Online Button clicked.")

        # --- STEP 6 : ONLINE SPECTATOR MODE ---
        bouton_online_spectator = page.locator('button[data-i18n="gui.actions.online.observe"]')
        await bouton_online_spectator.wait_for(state="visible", timeout=5000)
        await bouton_online_spectator.click()
        print("✅ Online Spectator Button clicked.")

        # --- STEP 7 : CONTREE SPECTATOR MODE ---
        bouton_spectator_contree = page.locator('button[data-i18n="gui.versions.contree"]')
        await bouton_spectator_contree.wait_for(state="visible", timeout=5000)
        await bouton_spectator_contree.click()
        print("✅ Contree Spectator Button clicked.")

        # --- PHASE 2: FINDING A TABLE ---
        print("🎲 Joining first available table...")
        try:
            await page.locator('.table-list-item').first.click(timeout=3000)
        except:
            print("⚠️ Auto-join failed. Please CLICK A TABLE manually within 5 seconds!")
            await page.wait_for_timeout(5000)

        # Table Hunting Loop
        match_found = False
        max_attempts = 20

        while not match_found and max_attempts > 0:
            max_attempts -= 1
            await page.wait_for_timeout(2000)

            # Check for Tournament Match
            if await page.locator('#tournamentMatchInfo').is_visible():
                print("✅ TOURNAMENT MATCH FOUND.")
                match_found = True
            else:
                print(f"❌ Standard table. searching next... ({max_attempts} left)")
                next_table_btn = page.locator('button[data-i18n="gui.online.tables.other-table"]')
                if await next_table_btn.is_visible():
                    await next_table_btn.click()
                    await page.wait_for_timeout(3000)
                else:
                    break

        # --- PHASE 3: START OBSERVATION ---
        if match_found:
            # handover control to the specific game observer function
            await observe_game(page)
        else:
            print("❌ Could not find a suitable table.")

        await browser.close()
        print("🏁 Script finished.")

if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())