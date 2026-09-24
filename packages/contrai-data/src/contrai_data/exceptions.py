"""Errors raised while reading or writing a game record.

Mirrors :mod:`contrai_core.exceptions`: every error here subclasses both
:class:`~contrai_core.ContraiError` and :class:`ValueError`, so a single
``except ContraiError`` catches the whole workspace family while a plain
``except ValueError`` still catches each member on its own.
"""

from __future__ import annotations

from contrai_core import ContraiError


class RecordError(ContraiError, ValueError):
    """Base class for every game-record error."""


class RecordFormatError(RecordError):
    """A record does not say what the format says it must.

    Raised by an event's construction-time invariant, by a strict token
    parse, by the codec on an unknown event name or a malformed line, and
    by the projection on an event stream that cannot be folded into
    rounds. Always a producer bug or a corrupted file — never a rule of
    the game.
    """


class UnsupportedFormatError(RecordError):
    """The record names a format major version this build cannot read.

    The header's ``format`` field carries a major version precisely so a
    future format change surfaces here rather than as a confusing pile of
    :class:`RecordFormatError`.
    """


class VerdictFormatError(RecordError):
    """A verdict file does not say what ``write_verdict`` would have written.

    Raised by :func:`~contrai_data.read_verdict` and the ``from_json``
    readers on an unknown or missing key, a wrong type, an unknown token,
    a repeated round, or a stored verdict or count that disagrees with the
    rounds it summarises. A :class:`RecordError`, because a verdict file is
    part of a corpus and is read with the same distrust as a record.
    """
