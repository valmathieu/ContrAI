"""Pins the package's wiring: it imports, and it imports nothing it must not."""

import importlib
import pkgutil

import contrai_data


class TestWiring:
    def test_package_imports(self):
        assert contrai_data.__all__ == []

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
