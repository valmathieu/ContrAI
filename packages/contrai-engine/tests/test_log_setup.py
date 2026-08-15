"""Unit tests for :class:`DebugOptions` and :func:`configure_logging`.

Every test that flips ``debug=True`` chdirs into ``tmp_path`` (so the
``contrai-debug.log`` file it writes never lands in the repo) and relies
on the ``_clean_debug_loggers`` autouse fixture to strip handlers off
the two package loggers afterwards, keeping the suite handler-leak-free.
"""

from __future__ import annotations

import dataclasses
import logging
import re

import pytest

from contrai_engine.log_setup import configure_logging
from contrai_engine.options import DebugOptions

_PACKAGE_LOGGER_NAMES = ("contrai_engine", "contrai_core")


@pytest.fixture(autouse=True)
def _clean_debug_loggers():
    """Detach and close any handler ``configure_logging`` attached.

    Guards against handler leakage across tests (a handler left open
    keeps a file descriptor on the previous test's ``tmp_path`` alive)
    and against a stray ``contrai-debug.log`` surviving in the repo.
    """

    yield
    for name in _PACKAGE_LOGGER_NAMES:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)


def test_debug_options_defaults_are_all_off():
    """``DebugOptions()`` is the back-compat anchor: every flag off."""

    assert DebugOptions() == DebugOptions(debug=False, autoplay=False, seed=None)


def test_debug_options_is_frozen():
    """Assigning to a field after construction raises."""

    options = DebugOptions()
    with pytest.raises(dataclasses.FrozenInstanceError):
        options.debug = True


def test_configure_logging_noop_without_debug_flag(tmp_path, monkeypatch):
    """With ``debug=False``, no handler is attached and no file is written."""

    monkeypatch.chdir(tmp_path)

    configure_logging(DebugOptions())

    assert logging.getLogger("contrai_engine").handlers == []
    assert logging.getLogger("contrai_core").handlers == []
    assert not (tmp_path / "contrai-debug.log").exists()


def test_configure_logging_debug_attaches_file_handler_to_package_loggers(tmp_path, monkeypatch):
    """``debug=True`` wires one DEBUG file handler onto both package loggers."""

    monkeypatch.chdir(tmp_path)
    options = DebugOptions(debug=True, seed=42)

    configure_logging(options)

    log_path = tmp_path / "contrai-debug.log"
    assert log_path.exists()

    engine_logger = logging.getLogger("contrai_engine")
    core_logger = logging.getLogger("contrai_core")

    assert engine_logger.level == logging.DEBUG
    assert core_logger.level == logging.DEBUG
    assert len(engine_logger.handlers) == 1
    assert len(core_logger.handlers) == 1
    assert engine_logger.handlers[0] is core_logger.handlers[0]
    assert isinstance(engine_logger.handlers[0], logging.FileHandler)
    assert engine_logger.handlers[0].level == logging.DEBUG

    core_logger.debug("dealt %s cards", 8)
    engine_logger.handlers[0].flush()

    content = log_path.read_text(encoding="utf-8")
    assert "run started" in content
    assert "seed 42" in content
    assert "DEBUG   contrai_core: dealt 8 cards" in content
    # No per-line timestamps: a `Formatter` with `%(asctime)s` prefixes every
    # line with a `YYYY-MM-DD HH:MM:SS,mmm` stamp. Absence of that pattern at
    # the start of any line is how two same-seed runs stay diff-identical.
    assert re.search(r"^\d{4}-\d{2}-\d{2}", content, re.MULTILINE) is None


def test_configure_logging_open_failure_warns_without_raising(tmp_path, monkeypatch, capsys):
    """A log path that can't be opened (here, a directory) warns once and continues."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "contrai-debug.log").mkdir()

    configure_logging(DebugOptions(debug=True))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "contrai-debug.log" in captured.err
    assert logging.getLogger("contrai_engine").handlers == []
    assert logging.getLogger("contrai_core").handlers == []


def test_configure_logging_is_idempotent(tmp_path, monkeypatch):
    """Calling ``configure_logging`` twice does not stack duplicate handlers."""

    monkeypatch.chdir(tmp_path)
    options = DebugOptions(debug=True)

    configure_logging(options)
    configure_logging(options)

    assert len(logging.getLogger("contrai_engine").handlers) == 1
    assert len(logging.getLogger("contrai_core").handlers) == 1
