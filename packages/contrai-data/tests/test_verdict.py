"""Pins the verdict vocabulary: the three-way rule, the roll-up, the JSON.

The rule under test is small enough to state in one line — any mismatch
is ``suspect``, otherwise any unchecked check is ``partial``, otherwise
``verified`` — and load-bearing enough that the corpus gate is phrased in
terms of it, so each branch gets its own case.
"""

from __future__ import annotations

import json

import pytest

from contrai_data import (
    GameVerdict,
    RecordError,
    Mismatch,
    MismatchKind,
    RoundVerdict,
    Verdict,
    VerdictFormatError,
    read_verdict,
    verdict_path,
    verdicts_dir,
    write_verdict,
)


def _mismatch(kind=MismatchKind.SCORE, **kwargs) -> Mismatch:
    """A mismatch with everything but ``kind`` defaulted."""

    return Mismatch(kind=kind, detail="something disagreed", **kwargs)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class TestTokens:
    @pytest.mark.parametrize("member", list(Verdict))
    def test_a_verdict_renders_as_its_token(self, member):
        assert str(member) == member.value

    @pytest.mark.parametrize("member", list(MismatchKind))
    def test_a_kind_renders_as_its_token(self, member):
        assert str(member) == member.value

    def test_the_five_mismatch_classes_are_the_spec_s(self):
        assert {str(k) for k in MismatchKind} == {
            "illegal_bid",
            "illegal_play",
            "trick_winner",
            "belote",
            "score",
        }


# ---------------------------------------------------------------------------
# The three-way rule
# ---------------------------------------------------------------------------


class TestDecide:
    def test_nothing_wrong_and_nothing_skipped_is_verified(self):
        assert RoundVerdict.decide(1).verdict is Verdict.VERIFIED

    def test_a_mismatch_is_suspect(self):
        result = RoundVerdict.decide(1, mismatches=(_mismatch(),))

        assert result.verdict is Verdict.SUSPECT

    def test_an_unchecked_check_is_partial(self):
        result = RoundVerdict.decide(1, unchecked=("score",))

        assert result.verdict is Verdict.PARTIAL

    def test_a_mismatch_outranks_an_unchecked_check(self):
        # A round that failed one check and skipped another is suspect:
        # the failure is the fact, the gap is only a caveat.
        result = RoundVerdict.decide(
            1, mismatches=(_mismatch(),), unchecked=("score",)
        )

        assert result.verdict is Verdict.SUSPECT

    def test_a_round_that_was_not_replayed_is_partial(self):
        result = RoundVerdict.decide(3, replayed=False)

        assert result.verdict is Verdict.PARTIAL
        assert result.replayed is False

    def test_it_keeps_what_it_was_given(self):
        mismatch = _mismatch()

        result = RoundVerdict.decide(
            7, mismatches=(mismatch,), unchecked=("belote",)
        )

        assert result.number == 7
        assert result.mismatches == (mismatch,)
        assert result.unchecked == ("belote",)


# ---------------------------------------------------------------------------
# The roll-up
# ---------------------------------------------------------------------------


class TestGameVerdict:
    @staticmethod
    def _game(*verdicts: Verdict, notes=()) -> GameVerdict:
        rounds = tuple(
            RoundVerdict(number=index, verdict=verdict)
            for index, verdict in enumerate(verdicts, start=1)
        )
        return GameVerdict(
            game_id="engine-test",
            source="engine",
            preset="classic",
            rounds=rounds,
            notes=notes,
        )

    def test_all_verified_is_verified(self):
        assert self._game(Verdict.VERIFIED, Verdict.VERIFIED).verdict is (
            Verdict.VERIFIED
        )

    def test_one_partial_makes_the_game_partial(self):
        game = self._game(Verdict.VERIFIED, Verdict.PARTIAL, Verdict.VERIFIED)

        assert game.verdict is Verdict.PARTIAL

    def test_one_suspect_makes_the_game_suspect(self):
        # The point of taking the worst rather than a majority: a single
        # bad round is never averaged away by good ones.
        game = self._game(
            Verdict.VERIFIED, Verdict.SUSPECT, Verdict.PARTIAL
        )

        assert game.verdict is Verdict.SUSPECT

    def test_a_record_with_no_rounds_is_partial(self):
        assert self._game().verdict is Verdict.PARTIAL

    def test_counts_name_every_verdict_including_the_empty_ones(self):
        game = self._game(Verdict.VERIFIED, Verdict.VERIFIED, Verdict.SUSPECT)

        assert game.counts == {
            Verdict.VERIFIED: 2,
            Verdict.PARTIAL: 0,
            Verdict.SUSPECT: 1,
        }


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------


class TestJson:
    def test_a_mismatch_omits_the_fields_that_do_not_apply(self):
        # An absent key reads as "not applicable here"; a null does not.
        payload = _mismatch(kind=MismatchKind.SCORE).as_json()

        assert payload == {"kind": "score", "detail": "something disagreed"}

    def test_a_mismatch_carries_the_fields_that_do(self):
        payload = _mismatch(
            kind=MismatchKind.TRICK_WINNER,
            position="North",
            trick=3,
            expected="North",
            observed="West",
        ).as_json()

        assert payload["position"] == "North"
        assert payload["trick"] == 3
        assert payload["expected"] == "North"
        assert payload["observed"] == "West"
        assert "seq" not in payload

    def test_a_round_renders_its_number_verdict_and_mismatches(self):
        payload = RoundVerdict.decide(
            4, mismatches=(_mismatch(kind=MismatchKind.BELOTE),)
        ).as_json()

        assert payload["round"] == 4
        assert payload["verdict"] == "suspect"
        assert payload["replayed"] is True
        assert [m["kind"] for m in payload["mismatches"]] == ["belote"]

    def test_a_game_renders_its_identity_verdict_counts_and_rounds(self):
        game = GameVerdict(
            game_id="engine-20260911T120000Z-abcdef",
            source="engine",
            preset="tournament",
            rounds=(
                RoundVerdict.decide(1),
                RoundVerdict.decide(2, unchecked=("score",)),
            ),
            notes=("the preset drifted",),
        )

        payload = game.as_json()

        assert payload["game_id"] == "engine-20260911T120000Z-abcdef"
        assert payload["source"] == "engine"
        assert payload["preset"] == "tournament"
        assert payload["verdict"] == "partial"
        assert payload["counts"] == {"verified": 1, "partial": 1, "suspect": 0}
        assert payload["notes"] == ["the preset drifted"]
        assert [r["round"] for r in payload["rounds"]] == [1, 2]


# ---------------------------------------------------------------------------
# Paths and writing
# ---------------------------------------------------------------------------


class TestPaths:
    def test_verdicts_sit_beside_games(self, tmp_path):
        assert verdicts_dir(tmp_path) == tmp_path / "verdicts"

    def test_a_verdict_is_named_after_its_record(self, tmp_path):
        assert verdict_path(tmp_path, "obs-77a99195") == (
            tmp_path / "verdicts" / "obs-77a99195.json"
        )

    @pytest.mark.parametrize(
        "game_id", ["", ".", "..", "a/b", "a\\b"]
    )
    def test_a_game_id_that_is_not_one_segment_is_refused(
        self, tmp_path, game_id
    ):
        with pytest.raises(ValueError):
            verdict_path(tmp_path, game_id)


class TestWrite:
    @staticmethod
    def _game() -> GameVerdict:
        return GameVerdict(
            game_id="engine-test",
            source="engine",
            preset="classic",
            rounds=(RoundVerdict.decide(1),),
        )

    def test_it_creates_the_directory(self, tmp_path):
        root = tmp_path / "records"

        path = write_verdict(root, self._game())

        assert path == root / "verdicts" / "engine-test.json"
        assert path.is_file()

    def test_it_writes_the_verdict_as_json(self, tmp_path):
        path = write_verdict(tmp_path, self._game())

        assert json.loads(path.read_text(encoding="utf-8")) == (
            self._game().as_json()
        )

    def test_it_ends_with_a_newline(self, tmp_path):
        path = write_verdict(tmp_path, self._game())

        assert path.read_text(encoding="utf-8").endswith("\n")

    def test_re_verifying_overwrites_rather_than_appends(self, tmp_path):
        write_verdict(tmp_path, self._game())

        path = write_verdict(tmp_path, self._game())

        assert len(json.loads(path.read_text(encoding="utf-8"))["rounds"]) == 1


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------


def _full_game() -> GameVerdict:
    """A game touching every field: a suspect, a partial, a verified round."""

    return GameVerdict(
        game_id="obs-0a1b2c3d",
        source="observed",
        preset="tournament",
        rounds=(
            RoundVerdict.decide(
                3,
                mismatches=(
                    _mismatch(
                        kind=MismatchKind.TRICK_WINNER,
                        position="North",
                        trick=5,
                        expected="North",
                        observed="West",
                    ),
                ),
            ),
            RoundVerdict.decide(4, unchecked=("score",)),
            RoundVerdict.decide(6),
            RoundVerdict.decide(
                7, unchecked=tuple(str(k) for k in MismatchKind), replayed=False
            ),
        ),
        notes=("the preset drifted",),
    )


def _payload() -> dict:
    """The full game as a fresh, mutable JSON object."""

    return json.loads(json.dumps(_full_game().as_json()))


class TestFromJson:
    def test_a_written_game_reads_back_equal(self):
        assert GameVerdict.from_json(_payload()) == _full_game()

    def test_a_mismatch_keeps_only_its_fields(self):
        mismatch = Mismatch.from_json({"kind": "score", "detail": "off by ten"})

        assert mismatch == Mismatch(kind=MismatchKind.SCORE, detail="off by ten")

    def test_a_mismatch_reads_every_optional_field(self):
        payload = {
            "kind": "illegal_bid",
            "detail": "d",
            "position": "West",
            "trick": 2,
            "seq": 4,
            "expected": "e",
            "observed": "o",
        }

        assert Mismatch.from_json(payload).as_json() == payload

    def test_an_unknown_key_is_refused(self):
        payload = _payload()
        payload["hash"] = "abc"

        with pytest.raises(VerdictFormatError, match="unknown"):
            GameVerdict.from_json(payload)

    def test_a_missing_key_is_refused(self):
        payload = _payload()
        del payload["rounds"][0]["replayed"]

        with pytest.raises(VerdictFormatError, match="missing"):
            GameVerdict.from_json(payload)

    def test_a_null_optional_field_is_refused(self):
        # ``as_json`` leaves an inapplicable field out; a ``null`` is not
        # something it writes.
        with pytest.raises(VerdictFormatError, match="position"):
            Mismatch.from_json({"kind": "score", "detail": "d", "position": None})

    def test_a_mismatch_number_of_the_wrong_type_is_refused(self):
        with pytest.raises(VerdictFormatError, match="trick"):
            Mismatch.from_json({"kind": "score", "detail": "d", "trick": "5"})

    @pytest.mark.parametrize(
        ("where", "value"),
        [
            ("verdict", "fine"),
            ("kind", "cheating"),
            ("unchecked", "tempo"),
            ("source", "replayed"),
        ],
    )
    def test_an_unknown_token_is_refused(self, where, value):
        payload = _payload()
        if where == "verdict":
            payload["rounds"][2]["verdict"] = value
        elif where == "kind":
            payload["rounds"][0]["mismatches"][0]["kind"] = value
        elif where == "unchecked":
            payload["rounds"][1]["unchecked"] = [value]
        else:
            payload["source"] = value

        with pytest.raises(VerdictFormatError, match="unknown token"):
            GameVerdict.from_json(payload)

    def test_a_token_of_the_wrong_type_is_refused(self):
        payload = _payload()
        payload["source"] = 1

        with pytest.raises(VerdictFormatError, match="string"):
            GameVerdict.from_json(payload)

    def test_a_bool_round_number_is_refused(self):
        # ``True == 1`` to ``isinstance``; the check is on the exact type.
        payload = _payload()
        payload["rounds"][2]["round"] = True

        with pytest.raises(VerdictFormatError, match="integer"):
            GameVerdict.from_json(payload)

    def test_a_non_bool_replayed_is_refused(self):
        payload = _payload()
        payload["rounds"][2]["replayed"] = 1

        with pytest.raises(VerdictFormatError, match="boolean"):
            GameVerdict.from_json(payload)

    def test_a_repeated_round_is_refused(self):
        payload = _payload()
        payload["rounds"][2]["round"] = 4

        with pytest.raises(VerdictFormatError, match="repeat"):
            GameVerdict.from_json(payload)

    def test_a_round_verdict_its_mismatches_contradict_is_refused(self):
        payload = _payload()
        payload["rounds"][0]["verdict"] = "partial"

        with pytest.raises(VerdictFormatError, match="round 3"):
            GameVerdict.from_json(payload)

    def test_a_game_verdict_its_rounds_contradict_is_refused(self):
        payload = _payload()
        payload["verdict"] = "partial"

        with pytest.raises(VerdictFormatError, match="Stored verdict"):
            GameVerdict.from_json(payload)

    def test_counts_its_rounds_contradict_are_refused(self):
        payload = _payload()
        payload["counts"]["verified"] = 2

        with pytest.raises(VerdictFormatError, match="counts"):
            GameVerdict.from_json(payload)

    def test_counts_missing_a_verdict_are_refused(self):
        payload = _payload()
        del payload["counts"]["suspect"]

        with pytest.raises(VerdictFormatError, match="missing"):
            GameVerdict.from_json(payload)

    @pytest.mark.parametrize("field", ["rounds", "notes"])
    def test_a_list_field_that_is_not_an_array_is_refused(self, field):
        payload = _payload()
        payload[field] = {}

        with pytest.raises(VerdictFormatError, match="array"):
            GameVerdict.from_json(payload)

    @pytest.mark.parametrize("payload", [[], "verdict", None, 3])
    def test_a_payload_that_is_not_an_object_is_refused(self, payload):
        with pytest.raises(VerdictFormatError, match="object"):
            GameVerdict.from_json(payload)


class TestReadVerdict:
    def test_what_write_verdict_writes_reads_back_equal(self, tmp_path):
        path = write_verdict(tmp_path, _full_game())

        assert read_verdict(path) == _full_game()

    def test_crlf_line_endings_read_back(self, tmp_path):
        # A verdict carried through a Windows checkout or an editor.
        path = write_verdict(tmp_path, _full_game())
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

        assert read_verdict(path) == _full_game()

    def test_a_file_that_is_not_json_is_refused(self, tmp_path):
        path = tmp_path / "v.json"
        path.write_text('{"game_id": ', encoding="utf-8")

        with pytest.raises(VerdictFormatError, match="not JSON"):
            read_verdict(path)

    def test_a_file_that_is_not_utf8_is_refused(self, tmp_path):
        path = tmp_path / "v.json"
        path.write_bytes(b'{"game_id": "\xff"}')

        with pytest.raises(VerdictFormatError, match="not UTF-8"):
            read_verdict(path)

    def test_a_repeated_key_is_refused(self, tmp_path):
        # ``json`` would silently keep the second value.
        path = tmp_path / "v.json"
        text = json.dumps(_full_game().as_json())
        path.write_text(text[:-1] + ', "preset": "classic"}', encoding="utf-8")

        with pytest.raises(VerdictFormatError, match="twice"):
            read_verdict(path)

    def test_the_error_names_the_file(self, tmp_path):
        payload = _full_game().as_json()
        payload["verdict"] = "verified"
        path = tmp_path / "obs-0a1b2c3d.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(VerdictFormatError, match="obs-0a1b2c3d.json"):
            read_verdict(path)

    def test_an_absent_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_verdict(tmp_path / "absent.json")

    def test_a_verdict_format_error_is_a_record_error(self):
        assert issubclass(VerdictFormatError, RecordError)
        assert issubclass(VerdictFormatError, ValueError)
