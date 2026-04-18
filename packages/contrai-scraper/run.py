from playwright.async_api import async_playwright

# Configuration
URL_CIBLE = "https://www.belote.com"  # Remets ton URL ici
MON_EMAIL = "valentin.mathieu13@gmail.com"


async def run():
    async with async_playwright() as p:
        # Lancement du navigateur
        browser = await p.chromium.launch(headless=False, slow_mo=500)
        context = await browser.new_context()
        page = await context.new_page()

        print(f"🤖 Navigation vers {URL_CIBLE}...")
        await page.goto(URL_CIBLE)

        # --- TON CODE DE CONNEXION GOOGLE ---
        # Note : Si le site a changé ou si le sélecteur n'est pas trouvé, le script s'arrêtera ici.
        # Assure-toi que les sélecteurs 'button[data-icon="google"]' sont toujours valides sur la page d'accueil.

        # On attend la popup après le clic
        async with context.expect_page() as new_page_info:
            print("🤖 Clic sur le bouton Google...")
            # Si ce sélecteur échoue, vérifie qu'il est bien présent sur la page d'accueil
            if await page.locator('button[data-icon="google"]').count() > 0:
                await page.click('button[data-icon="google"]')
            else:
                print("❌ ERREUR : Le bouton Google est introuvable sur la page d'accueil.")
                await browser.close()
                return

        popup_google = await new_page_info.value
        await popup_google.wait_for_load_state()
        print("✅ Popup Google détectée !")

        print(f"🤖 Sélection du compte : {MON_EMAIL}")
        await popup_google.click(f'div[data-email="{MON_EMAIL}"]')

        print("⏳ Connexion en cours...")
        await page.wait_for_timeout(5000)  # On attend 5 secondes pour voir le résultat

        await browser.close()
        print("🏁 Fin du test.")


await run()