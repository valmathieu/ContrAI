"""The scraper's error family.

Dual-based like ``contrai-data``'s: one ``except ContraiError`` catches the
whole workspace family, and a plain ``except ValueError`` still catches each.
"""

from contrai_core import ContraiError


class ScraperError(ContraiError):
    """Base class for every error this package raises."""


class ProfileError(ScraperError, ValueError):
    """The profile is missing, malformed, or names something unknown."""


class BrowserError(ScraperError, RuntimeError):
    """A step of the browser walk did not complete."""


class WireError(ScraperError, ValueError):
    """A frame could not be unwrapped, or its key could not be read."""


class ParseError(ScraperError, ValueError):
    """An observed game could not be turned into a record."""
