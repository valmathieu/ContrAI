"""Pins the package's wiring: it imports, and it imports nothing it must not."""

import importlib
import pkgutil

import contrai_data


class TestWiring:
    def test_every_exported_name_resolves(self):
        # ``__all__`` is the package's whole public surface — consumers import
        # from ``contrai_data`` and never from its modules. A name listed here
        # but not actually bound is an import error for somebody else.
        assert contrai_data.__all__
        for name in contrai_data.__all__:
            assert hasattr(contrai_data, name), name

    def test_the_public_surface_is_listed(self):
        # The converse: a symbol re-exported but left off ``__all__`` is
        # invisible to ``from contrai_data import *`` and to the API docs.
        public = {
            name
            for name in vars(contrai_data)
            if not name.startswith("_")
            and getattr(
                getattr(contrai_data, name), "__module__", ""
            ).startswith("contrai_data")
        }
        assert public <= set(contrai_data.__all__)

    def test_depends_on_core_only(self):
        # The dependency edge is core <- data <- {engine, scraper}. Importing
        # either consumer here would make it a cycle, and importing a
        # third-party package would put a wheel between a record and its
        # reader. Walk every module in the package and read its imports off
        # the module object rather than parsing source.
        forbidden = {"contrai_engine", "contrai_scraper", "rich", "playwright"}
        for info in pkgutil.walk_packages(
            contrai_data.__path__, prefix="contrai_data."
        ):
            module = importlib.import_module(info.name)
            imported = {
                getattr(value, "__module__", "").split(".")[0]
                for value in vars(module).values()
            }
            imported |= {
                name.split(".")[0]
                for name, value in vars(module).items()
                if getattr(value, "__name__", "").startswith(("contrai", "rich"))
            }
            assert not (imported & forbidden), f"{info.name} imports {imported & forbidden}"
