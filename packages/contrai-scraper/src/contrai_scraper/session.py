"""Browser navigation from the lobby to a spectated tournament table.

The three steps are kept separate so a caller can stop at any of them: log in,
walk the Online → Spectator → Contrée menus, then hunt for a table that is part
of a tournament (tournaments are preferred because their players are ranked,
which makes the recorded games more useful as training data).
"""

from playwright.async_api import Page

from contrai_scraper.config import ACCOUNT_EMAIL, TARGET_URL, VERIFICATION_CODE

#: How many tables to inspect before giving up on finding a tournament match.
MAX_TABLE_ATTEMPTS = 20


async def log_in(page: Page) -> None:
    """Navigates to the lobby and signs in with the scraping account.

    Dismisses the quick-start tutorial if the site offers it, then submits the
    account address and its fixed verification code.

    Args:
        page: Freshly opened page.
    """
    print(f"🌐 Navigation to {TARGET_URL}...")
    await page.goto(TARGET_URL)

    print("⏳ Waiting for page load (Hard wait 5s)...")
    await page.wait_for_timeout(5000)

    tutorial_btn = page.locator('button[data-i18n="gui.quick-start.launch.no"]')
    if await tutorial_btn.is_visible():
        await tutorial_btn.click()
        print("✅ Tutorial closed.")

    print("🖱️ Logging in...")
    # The button is labelled "Email" in some locales and icon-only in others.
    try:
        await page.click('button:has-text("Email")', timeout=5000)
    except Exception:
        await page.click('button[data-icon="email"]')

    await page.fill('input[placeholder="Adresse électronique"]', ACCOUNT_EMAIL)
    await page.click('button[data-i18n="gui.users.email-wizard.continue"]')

    await page.wait_for_selector("#verificationCode", state="visible")
    await page.fill("#verificationCode", VERIFICATION_CODE)
    await page.click("#validateBtn")

    print("✅ Login successful. Waiting for Lobby (10s)...")
    await page.wait_for_timeout(10000)


async def open_spectator_mode(page: Page) -> None:
    """Walks the lobby menus down to the Contrée spectator table list.

    Args:
        page: Page sitting in the lobby, already logged in.
    """
    steps = (
        ('button[data-i18n="gui.actions.mode.online"]', "Mode Online"),
        ('button[data-i18n="gui.actions.online.observe"]', "Online Spectator"),
        ('button[data-i18n="gui.versions.contree"]', "Contree Spectator"),
    )

    for selector, label in steps:
        button = page.locator(selector)
        await button.wait_for(state="visible", timeout=5000)
        await button.click()
        print(f"✅ {label} Button clicked.")


async def find_tournament_table(page: Page) -> bool:
    """Joins tables in turn until one belongs to a tournament.

    Args:
        page: Page showing the Contrée spectator table list.

    Returns:
        ``True`` once a tournament match is on screen, ``False`` if the table
        list ran out or :data:`MAX_TABLE_ATTEMPTS` was exhausted first.
    """
    print("🎲 Joining first available table...")
    try:
        await page.locator(".table-list-item").first.click(timeout=3000)
    except Exception:
        print("⚠️ Auto-join failed. Please CLICK A TABLE manually within 5 seconds!")
        await page.wait_for_timeout(5000)

    remaining_attempts = MAX_TABLE_ATTEMPTS

    while remaining_attempts > 0:
        remaining_attempts -= 1
        await page.wait_for_timeout(2000)

        if await page.locator("#tournamentMatchInfo").is_visible():
            print("✅ TOURNAMENT MATCH FOUND.")
            return True

        print(f"❌ Standard table. searching next... ({remaining_attempts} left)")
        next_table_btn = page.locator('button[data-i18n="gui.online.tables.other-table"]')
        if not await next_table_btn.is_visible():
            break
        await next_table_btn.click()
        await page.wait_for_timeout(3000)

    return False
