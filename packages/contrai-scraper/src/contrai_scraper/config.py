"""Static configuration for the spectator scraper.

The site authenticates by mailing a verification code to the account address,
but the dedicated scraping account is pinned to a fixed code, so the whole
login flow can run unattended.
"""

TARGET_URL = "https://app.belote-rebelote.fr/"
"""Lobby entry point the scraper navigates to."""

ACCOUNT_EMAIL = "contrai-michel@proton.me"
"""Address of the dedicated scraping account."""

VERIFICATION_CODE = "0343"
"""Fixed verification code accepted for :data:`ACCOUNT_EMAIL`."""
