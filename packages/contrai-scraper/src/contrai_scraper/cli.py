"""Console entry point for the spectator scraper."""

import asyncio
import sys

from playwright.async_api import async_playwright

from contrai_scraper.observer import observe_game
from contrai_scraper.session import find_tournament_table, log_in, open_spectator_mode


async def scrape() -> None:
    """Runs one full scraping session, from cold browser to observed table.

    The browser stays headed and slowed down: the site is watched, not driven
    hard, and a visible window makes the run auditable while the flow is still
    being reverse-engineered.
    """
    print("🚀 Bot starts...")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, slow_mo=500)
        page = await browser.new_page()

        await log_in(page)
        await open_spectator_mode(page)

        if await find_tournament_table(page):
            await observe_game(page)
        else:
            print("❌ Could not find a suitable table.")

        await browser.close()
        print("🏁 Script finished.")


def main() -> None:
    """Entry point of the ``contrai-scrape`` console script.

    Playwright's subprocess transport needs the proactor event loop on Windows;
    the default selector loop cannot spawn the browser driver.
    """
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(scrape())


if __name__ == "__main__":
    main()
