"""Pins the package's public surface and its dependency edges."""

import pathlib
import re

import contrai_scraper


class TestWiring:
    def test_every_exported_name_resolves(self):
        # ``__all__`` is the package's whole public surface — consumers import
        # from ``contrai_scraper`` and never from its modules. A name listed
        # here but not actually bound is an import error for somebody else.
        missing = [
            name for name in contrai_scraper.__all__
            if not hasattr(contrai_scraper, name)
        ]
        assert missing == []

    def test_the_public_surface_is_listed(self):
        # The converse: a symbol re-exported but left off ``__all__`` is
        # invisible to ``from contrai_scraper import *`` and to the API docs.
        exported = set(contrai_scraper.__all__)
        public = {
            name for name, value in vars(contrai_scraper).items()
            if not name.startswith("_")
            and getattr(value, "__module__", "").startswith("contrai_scraper")
        }
        assert public - exported == set()

    def test_the_literal_seat_map_is_never_used(self):
        # Position.from_french maps the four names one to one, which is the
        # exact bug the mirrored seat map exists to avoid.
        sources = pathlib.Path(contrai_scraper.__path__[0]).rglob("*.py")
        offenders = [
            path.name for path in sources
            if "from_french" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_never_imports_the_engine(self):
        # The dependency edge is core <- data <- {engine, scraper}. CI runs
        # this suite with only the scraper's own dependencies installed, so an
        # engine import is not merely wrong, it does not resolve.
        forbidden = ("contrai_engine", "rich")
        offenders = []
        for path in pathlib.Path(contrai_scraper.__path__[0]).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            offenders += [
                f"{path.name} -> {bad}"
                for bad in forbidden
                if re.search(rf"^\s*(import|from)\s+{bad}\b", text, re.M)
            ]
        assert offenders == []
