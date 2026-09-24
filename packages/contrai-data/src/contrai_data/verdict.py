"""What a verification concluded, and how it is written down.

Three verdicts, and the middle one is the interesting one.

``verified``
    Every check that applied to this round ran, and every one passed.

``suspect``
    At least one check failed. The round describes something that could
    not have happened at a table playing the ruleset the record names.

``partial``
    Every check that ran passed, but at least one could not run for want
    of something to check against — overwhelmingly a round with no
    ``round_scored`` event, which is the *common* case in observed games:
    a spectator sees the cards long before it sees a score sheet.

That third verdict is why the corpus gate reads "no round is suspect"
rather than "every round is verified". Collapsing ``partial`` into
``suspect`` would cry wolf on most of the corpus; collapsing it into
``verified`` would quietly claim a score was checked when none was seen.

A game's verdict is the worst of its rounds, on the order
``verified < partial < suspect``, so one bad round is never averaged away
by good ones.

**Reading a verdict back is strict.** Whatever indexes a corpus trusts
what it reads, so :func:`read_verdict` refuses anything
:func:`write_verdict` would not have written — an unknown or missing key,
a ``null`` where a key should be absent, a wrong type, an unknown token,
a repeated round — and re-derives every stored conclusion (each round's
verdict, the game's, the counts), refusing a file that disagrees with its
own rounds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .events import RecordSource
from .exceptions import VerdictFormatError


class Verdict(Enum):
    """How a round, or a whole game, came out of verification."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    SUSPECT = "suspect"

    def __str__(self) -> str:
        """Render as the JSON token, e.g. ``"suspect"``."""

        return self.value


#: Increasing severity. A game takes the worst verdict among its rounds,
#: which is a ``max`` over this ordering — spelled out rather than relying
#: on the enum's definition order, so reordering the members above cannot
#: silently change what a game's verdict means.
_SEVERITY: dict[Verdict, int] = {
    Verdict.VERIFIED: 0,
    Verdict.PARTIAL: 1,
    Verdict.SUSPECT: 2,
}


def _object(payload: object, where: str) -> dict[str, Any]:
    """Check ``payload`` is a JSON object.

    Args:
        payload: The decoded JSON value.
        where: What it should be, for the message.

    Returns:
        The payload, typed as a mapping.

    Raises:
        VerdictFormatError: If it is not an object.
    """

    if not isinstance(payload, dict):
        raise VerdictFormatError(
            f"{where} is a JSON object, got {type(payload).__name__}"
        )
    return payload


def _keys(
    payload: dict[str, Any],
    required: frozenset[str],
    where: str,
    optional: frozenset[str] = frozenset(),
) -> None:
    """Check ``payload`` holds every required key and nothing unknown.

    Args:
        payload: The JSON object.
        required: Keys that must be present.
        where: What the object is, for the message.
        optional: Keys that may be present.

    Raises:
        VerdictFormatError: If a required key is missing or a key is
            neither required nor optional.
    """

    missing = required - payload.keys()
    unknown = payload.keys() - required - optional
    if missing or unknown:
        raise VerdictFormatError(
            f"{where}: missing {sorted(missing)}, unknown {sorted(unknown)}"
        )


def _int(value: object, where: str) -> int:
    """Check ``value`` is exactly an ``int``.

    ``type(...) is int`` rather than ``isinstance``: a ``bool`` is an
    ``int`` to ``isinstance``, and ``true`` is not a round number.

    Args:
        value: The decoded JSON value.
        where: What it is, for the message.

    Returns:
        The integer.

    Raises:
        VerdictFormatError: If it is anything else.
    """

    if type(value) is not int:
        raise VerdictFormatError(f"{where} is an integer, got {value!r}")
    return value


def _str(value: object, where: str) -> str:
    """Check ``value`` is a string.

    Args:
        value: The decoded JSON value.
        where: What it is, for the message.

    Returns:
        The string.

    Raises:
        VerdictFormatError: If it is anything else.
    """

    if not isinstance(value, str):
        raise VerdictFormatError(f"{where} is a string, got {value!r}")
    return value


def _list(value: object, where: str) -> list[Any]:
    """Check ``value`` is a JSON array.

    Args:
        value: The decoded JSON value.
        where: What it is, for the message.

    Returns:
        The list.

    Raises:
        VerdictFormatError: If it is anything else.
    """

    if not isinstance(value, list):
        raise VerdictFormatError(f"{where} is an array, got {value!r}")
    return value


def _token[E: Enum](enum: type[E], value: object, where: str) -> E:
    """Read an enum member off its JSON token.

    Args:
        enum: The vocabulary.
        value: The decoded JSON value.
        where: What it is, for the message.

    Returns:
        The member.

    Raises:
        VerdictFormatError: If the token is not one of the vocabulary's.
    """

    token = _str(value, where)
    try:
        return enum(token)
    except ValueError:
        raise VerdictFormatError(
            f"{where}: unknown token {value!r}, expected one of "
            f"{[member.value for member in enum]}"
        ) from None


class MismatchKind(Enum):
    """The five ways a round can disagree with its record (spec §5.4).

    Each names the engine component that is the source of truth, not the
    symptom: ``illegal_bid`` is ``Auction.apply`` refusing, ``score`` is
    ``score_round`` disagreeing. Anything the verifier cannot pin on one
    of these is not reported as a mismatch at all.
    """

    ILLEGAL_BID = "illegal_bid"
    ILLEGAL_PLAY = "illegal_play"
    TRICK_WINNER = "trick_winner"
    BELOTE = "belote"
    SCORE = "score"

    def __str__(self) -> str:
        """Render as the JSON token, e.g. ``"illegal_play"``."""

        return self.value


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One disagreement between a replayed round and its record.

    Attributes:
        kind: Which of the five classes this is.
        detail: One sentence in §10 vocabulary saying what disagreed.
        position: The seat it happened at, as a token, or ``None`` when
            the disagreement is not a seat's — a score line, say.
        trick: The 1-based trick it happened in, or ``None`` outside the
            play phase.
        seq: The 1-based bid sequence it happened at, or ``None`` outside
            the auction.
        expected: What the engine derived, rendered.
        observed: What the record claimed, rendered.
    """

    kind: MismatchKind
    detail: str
    position: str | None = None
    trick: int | None = None
    seq: int | None = None
    expected: str | None = None
    observed: str | None = None

    def as_json(self) -> dict[str, Any]:
        """This mismatch as the JSON object a verdict file holds.

        Returns:
            A mapping with ``kind`` and ``detail`` always present, and
            each optional field only when it is set — an absent key reads
            as "not applicable here", which a ``null`` does not.
        """

        payload: dict[str, Any] = {
            "kind": str(self.kind),
            "detail": self.detail,
        }
        for name in ("position", "trick", "seq", "expected", "observed"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_json(cls, payload: object) -> Mismatch:
        """Read a mismatch back from its JSON object.

        Args:
            payload: The decoded object, as :meth:`as_json` wrote it.

        Returns:
            The mismatch.

        Raises:
            VerdictFormatError: If a key is unknown or missing, an
                optional key holds ``null`` (``as_json`` leaves it out),
                a value has the wrong type, or ``kind`` is not a
                :class:`MismatchKind` token.
        """

        data = _object(payload, "mismatch")
        _keys(data, _MISMATCH_REQUIRED, "mismatch", _MISMATCH_OPTIONAL)

        def text(name: str) -> str | None:
            return _str(data[name], f"mismatch {name}") if name in data else None

        def number(name: str) -> int | None:
            return _int(data[name], f"mismatch {name}") if name in data else None

        return cls(
            kind=_token(MismatchKind, data["kind"], "mismatch kind"),
            detail=_str(data["detail"], "mismatch detail"),
            position=text("position"),
            trick=number("trick"),
            seq=number("seq"),
            expected=text("expected"),
            observed=text("observed"),
        )


#: The key sets a verdict file's three objects are written with. Reading
#: checks against them exactly, so a key this build does not write is
#: refused rather than dropped.
_MISMATCH_REQUIRED = frozenset({"kind", "detail"})
_MISMATCH_OPTIONAL = frozenset({"position", "trick", "seq", "expected", "observed"})
_ROUND_KEYS = frozenset({"round", "verdict", "replayed", "unchecked", "mismatches"})
_GAME_KEYS = frozenset(
    {"game_id", "source", "preset", "verdict", "counts", "notes", "rounds"}
)


@dataclass(frozen=True, slots=True)
class RoundVerdict:
    """One round's verification result.

    Attributes:
        number: The round number, as the record numbers it.
        verdict: How the round came out.
        mismatches: Every disagreement found, in the order they were
            found.
        unchecked: The checks that could not run, named as the JSON
            tokens of :class:`MismatchKind` — what makes a verdict
            ``partial`` rather than ``verified``.
        replayed: Whether the round was replayed at all. A structurally
            incomplete round is not, and reports ``partial``.
    """

    number: int
    verdict: Verdict
    mismatches: tuple[Mismatch, ...] = ()
    unchecked: tuple[str, ...] = ()
    replayed: bool = True

    @classmethod
    def decide(
        cls,
        number: int,
        mismatches: tuple[Mismatch, ...] = (),
        unchecked: tuple[str, ...] = (),
        *,
        replayed: bool = True,
    ) -> "RoundVerdict":
        """Build a verdict from what the checks found.

        The rule, in one line: any mismatch is ``suspect``, otherwise any
        unchecked check is ``partial``, otherwise ``verified``.

        Args:
            number: The round number.
            mismatches: The disagreements found.
            unchecked: The checks that had nothing to run against.
            replayed: Whether the round ran at all.

        Returns:
            The round's verdict.
        """

        if mismatches:
            verdict = Verdict.SUSPECT
        elif unchecked or not replayed:
            verdict = Verdict.PARTIAL
        else:
            verdict = Verdict.VERIFIED
        return cls(
            number=number,
            verdict=verdict,
            mismatches=tuple(mismatches),
            unchecked=tuple(unchecked),
            replayed=replayed,
        )

    def as_json(self) -> dict[str, Any]:
        """This round as the JSON object a verdict file holds.

        Returns:
            The round number, its verdict, and its mismatches.
        """

        return {
            "round": self.number,
            "verdict": str(self.verdict),
            "replayed": self.replayed,
            "unchecked": list(self.unchecked),
            "mismatches": [m.as_json() for m in self.mismatches],
        }

    @classmethod
    def from_json(cls, payload: object) -> RoundVerdict:
        """Read a round back from its JSON object.

        The stored verdict is not taken on trust: the round is rebuilt
        through :meth:`decide` from its own mismatches, unchecked checks
        and ``replayed`` flag, and a file whose stored verdict disagrees
        is refused.

        Args:
            payload: The decoded object, as :meth:`as_json` wrote it.

        Returns:
            The round's verdict.

        Raises:
            VerdictFormatError: If a key is unknown or missing, a value
                has the wrong type, a token is unknown, or the stored
                verdict is not the one the round's contents decide.
        """

        data = _object(payload, "round")
        _keys(data, _ROUND_KEYS, "round")
        where = f"round {_int(data['round'], 'round number')}"
        stored = _token(Verdict, data["verdict"], f"{where} verdict")
        replayed = data["replayed"]
        if not isinstance(replayed, bool):
            raise VerdictFormatError(
                f"{where} replayed is a boolean, got {replayed!r}"
            )
        unchecked = tuple(
            str(_token(MismatchKind, check, f"{where} unchecked"))
            for check in _list(data["unchecked"], f"{where} unchecked")
        )
        mismatches = tuple(
            Mismatch.from_json(item)
            for item in _list(data["mismatches"], f"{where} mismatches")
        )
        round_ = cls.decide(
            data["round"], mismatches, unchecked, replayed=replayed
        )
        if round_.verdict is not stored:
            raise VerdictFormatError(
                f"{where} is stored {stored} but its contents decide "
                f"{round_.verdict}"
            )
        return round_


@dataclass(frozen=True, slots=True)
class GameVerdict:
    """A whole record's verification result.

    Attributes:
        game_id: The record's own id, which is also its file name's stem.
        source: Who produced the record, as its header's token.
        preset: The preset the record names.
        rounds: One verdict per round, in file order.
        notes: Non-fatal observations — the ruleset drift a stale preset
            produces, say. They never change a verdict; they are there so
            a reader is not left to wonder.
    """

    game_id: str
    source: str
    preset: str
    rounds: tuple[RoundVerdict, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def verdict(self) -> Verdict:
        """The worst verdict among the rounds.

        A record with no rounds at all is ``partial``: nothing was found
        wrong, and nothing was checked either.
        """

        if not self.rounds:
            return Verdict.PARTIAL
        return max(
            (round_.verdict for round_ in self.rounds),
            key=_SEVERITY.__getitem__,
        )

    @property
    def counts(self) -> dict[Verdict, int]:
        """How many rounds landed on each verdict.

        Returns:
            One entry per :class:`Verdict`, zero included — a caller
            printing a summary line should not have to guard for a
            missing key.
        """

        tally = {verdict: 0 for verdict in Verdict}
        for round_ in self.rounds:
            tally[round_.verdict] += 1
        return tally

    def as_json(self) -> dict[str, Any]:
        """This game as the JSON object written to ``verdicts/``.

        Returns:
            The record's identity, its overall verdict, the per-verdict
            counts and every round.
        """

        return {
            "game_id": self.game_id,
            "source": self.source,
            "preset": self.preset,
            "verdict": str(self.verdict),
            "counts": {str(k): v for k, v in self.counts.items()},
            "notes": list(self.notes),
            "rounds": [round_.as_json() for round_ in self.rounds],
        }

    @classmethod
    def from_json(cls, payload: object) -> GameVerdict:
        """Read a whole game back from its JSON object.

        Args:
            payload: The decoded object, as :meth:`as_json` wrote it.

        Returns:
            The game's verdict.

        Raises:
            VerdictFormatError: If a key is unknown or missing, a value
                has the wrong type, ``source`` is not a
                :class:`~contrai_data.RecordSource` token, a round number
                repeats, or the stored game verdict or counts disagree
                with the rounds.
        """

        data = _object(payload, "verdict file")
        _keys(data, _GAME_KEYS, "verdict file")
        rounds = tuple(
            RoundVerdict.from_json(item)
            for item in _list(data["rounds"], "rounds")
        )
        numbers = [round_.number for round_ in rounds]
        if len(set(numbers)) != len(numbers):
            raise VerdictFormatError(f"Round numbers repeat: {numbers}")
        game = cls(
            game_id=_str(data["game_id"], "game_id"),
            source=str(_token(RecordSource, data["source"], "source")),
            preset=_str(data["preset"], "preset"),
            rounds=rounds,
            notes=tuple(
                _str(note, "note") for note in _list(data["notes"], "notes")
            ),
        )

        # The two summaries are derived from the rounds, so they are
        # recomputed and compared rather than read: a file whose header
        # says "partial" over a suspect round is a file somebody edited.
        stored = _token(Verdict, data["verdict"], "verdict")
        if stored is not game.verdict:
            raise VerdictFormatError(
                f"Stored verdict {stored} is not its rounds' {game.verdict}"
            )
        counts = _object(data["counts"], "counts")
        _keys(counts, frozenset(member.value for member in Verdict), "counts")
        stored_counts = {
            member: _int(counts[member.value], f"counts {member}")
            for member in Verdict
        }
        if stored_counts != game.counts:
            raise VerdictFormatError(
                f"Stored counts {counts} are not its rounds' "
                f"{ {str(k): v for k, v in game.counts.items()} }"
            )
        return game


def verdicts_dir(root: Path | str) -> Path:
    """The directory holding verdicts under ``root``.

    Sibling of ``games/`` (spec §3.3), so a corpus and its verdicts move
    together and a verdict is findable from the record it judges without
    a second path to configure.

    Args:
        root: A records root, the same one ``contrai-data`` uses.

    Returns:
        ``<root>/verdicts``. Not created.
    """

    return Path(root) / "verdicts"


def verdict_path(root: Path | str, game_id: str) -> Path:
    """The file one game's verdict lives in.

    Args:
        root: A records root.
        game_id: The game's identity, which becomes the file name's stem.

    Returns:
        ``<root>/verdicts/<game_id>.json``.

    Raises:
        ValueError: If ``game_id`` is not exactly one path segment. The
            id comes off a record file, which came from outside this
            process, so the name it becomes is checked rather than
            trusted.
    """

    if (
        not game_id
        or game_id in {".", ".."}
        or "/" in game_id
        or "\\" in game_id
    ):
        raise ValueError(f"A game id is one path segment, got {game_id!r}")
    return verdicts_dir(root) / f"{game_id}.json"


def write_verdict(root: Path | str, verdict: GameVerdict) -> Path:
    """Write ``verdict`` to ``<root>/verdicts/<game_id>.json``.

    Args:
        root: The records root to write under. Its ``verdicts``
            directory is created if missing.
        verdict: The verdict to write.

    Returns:
        The path written.
    """

    path = verdict_path(root, verdict.game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sorted keys and a trailing newline: a verdict file is re-written
    # every time a corpus is re-verified, and a diff that only shows what
    # actually changed is worth the two arguments.
    path.write_text(
        json.dumps(verdict.as_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object, refusing a key that appears twice.

    ``json`` keeps the last of two equal keys without a word; a file
    written by :func:`write_verdict` never repeats one, so a repeat is
    damage and is refused.

    Args:
        pairs: The object's key/value pairs, in file order.

    Returns:
        The object.

    Raises:
        VerdictFormatError: If a key repeats.
    """

    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise VerdictFormatError(f"Key {key!r} appears twice")
        payload[key] = value
    return payload


def read_verdict(path: Path | str) -> GameVerdict:
    """Read a verdict file back, as strictly as a record is read.

    Args:
        path: The verdict file, e.g. from :func:`verdict_path`.

    Returns:
        The game's verdict.

    Raises:
        FileNotFoundError: If the file does not exist.
        VerdictFormatError: If the file is not UTF-8, not JSON, or not
            what :func:`write_verdict` writes. The message names the
            file.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        return GameVerdict.from_json(
            json.loads(text, object_pairs_hook=_unique_keys)
        )
    except UnicodeDecodeError as exc:
        raise VerdictFormatError(f"{path}: not UTF-8 ({exc.reason})") from exc
    except json.JSONDecodeError as exc:
        raise VerdictFormatError(f"{path}: not JSON ({exc.msg})") from exc
    except VerdictFormatError as exc:
        raise VerdictFormatError(f"{path}: {exc}") from exc
