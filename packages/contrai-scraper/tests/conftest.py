"""Fixtures for the scraper suite.

This directory deliberately has **no** ``__init__.py``, unlike
``contrai-core``'s and ``contrai-engine``'s test directories: ``pytest``
registers each ``conftest.py`` as a plugin keyed by its module name, and a
second ``tests`` *package* carrying one aborts a whole-workspace run with
"Plugin already registered under a different name". Leaving this a plain
directory is what lets all four suites be collected in one run.

Helper data is exposed as fixtures rather than imported by name, because the
root ``pyproject.toml`` runs pytest with ``--import-mode=importlib`` and
``from conftest import ...`` is not reliable under it.
"""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """An output root for records and raw logs, outside the real corpus.

    Nothing in this suite may write to the machine's records root: it holds
    personal data until roadmap step A.

    Returns:
        A freshly created scratch directory.
    """

    root = tmp_path / "scraper-root"
    root.mkdir()
    return root
