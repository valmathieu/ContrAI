"""Pins corpus backups: what an archive holds, and how a damaged one is caught."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from contrai_data import (
    MANIFEST_FILE,
    MANIFEST_FORMAT,
    ArchiveCheck,
    CorpusError,
    backup_corpus,
    catalog_path,
    check_archive,
    verdicts_dir,
)

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def corpus(tmp_path) -> Path:
    """A small corpus: two raw logs, one game, a build report, and the two
    derived things a backup leaves out."""

    root = tmp_path / "corpus"
    for name, text in {
        "raw/box/s1.jsonl": "frame one\n",
        "raw/laptop/s2.jsonl": "frame two\n",
        "games/obs-g1.jsonl": "a record\n",
        "build.json": "{}\n",
        "verdicts/obs-g1.json": "{}\n",
        "catalog.sqlite": "index",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return root


def _rezip(source: Path, target: Path, change: Callable[[str, bytes], bytes | None]) -> Path:
    """Copy an archive member by member, letting ``change`` alter or drop each."""

    with zipfile.ZipFile(source) as old, zipfile.ZipFile(target, "w") as new:
        for name in old.namelist():
            data = change(name, old.read(name))
            if data is not None:
                new.writestr(name, data)
    return target


class TestBackup:
    def test_it_holds_the_raw_logs_games_and_report_and_nothing_derived(
        self, corpus, tmp_path
    ):
        summary = backup_corpus(corpus, tmp_path / "out", now=NOW)
        with zipfile.ZipFile(summary.path) as zipped:
            assert sorted(zipped.namelist()) == [
                MANIFEST_FILE, "build.json", "games/obs-g1.jsonl",
                "raw/box/s1.jsonl", "raw/laptop/s2.jsonl"]

    def test_the_archive_is_named_by_its_stamp(self, corpus, tmp_path):
        summary = backup_corpus(corpus, tmp_path / "out", now=NOW)
        assert summary.path == tmp_path / "out" / "contrai-corpus-20260928T120000Z.zip"

    def test_the_summary_counts_what_went_in(self, corpus, tmp_path):
        summary = backup_corpus(corpus, tmp_path / "out", now=NOW)
        assert (summary.files, summary.raw_logs, summary.games, summary.size) == (
            4, 2, 1, len("frame one\n") + len("frame two\n") + len("a record\n") + 3)

    def test_the_manifest_hashes_every_file(self, corpus, tmp_path):
        summary = backup_corpus(corpus, tmp_path / "out", now=NOW)
        with zipfile.ZipFile(summary.path) as zipped:
            manifest = json.loads(zipped.read(MANIFEST_FILE))
        assert (manifest["format"], manifest["created_at"], manifest["counts"]) == (
            MANIFEST_FORMAT, "2026-09-28T12:00:00Z", {"raw_logs": 2, "games": 1})
        assert manifest["files"]["games/obs-g1.jsonl"] == {
            "sha256": hashlib.sha256(b"a record\n").hexdigest(), "size": 9}
        assert manifest["generator"].startswith("contrai-data")

    def test_a_fresh_backup_checks_clean(self, corpus, tmp_path):
        summary = backup_corpus(corpus, tmp_path / "out", now=NOW)
        assert check_archive(summary.path) == ArchiveCheck(files=4, problems=())

    def test_a_corpus_without_a_build_report_backs_up_the_rest(self, corpus, tmp_path):
        (corpus / "build.json").unlink()
        assert backup_corpus(corpus, tmp_path / "out", now=NOW).files == 3

    def test_an_empty_corpus_is_refused(self, tmp_path):
        (tmp_path / "empty" / "verdicts").mkdir(parents=True)
        catalog_path(tmp_path / "empty").write_text("index only")
        with pytest.raises(CorpusError, match="nothing|no raw log"):
            backup_corpus(tmp_path / "empty", tmp_path / "out", now=NOW)

    def test_an_existing_archive_is_never_overwritten(self, corpus, tmp_path):
        backup_corpus(corpus, tmp_path / "out", now=NOW)
        with pytest.raises(CorpusError, match="already exists"):
            backup_corpus(corpus, tmp_path / "out", now=NOW)

    def test_an_interrupted_backup_leaves_nothing_behind(self, corpus, tmp_path,
                                                         monkeypatch):
        def full(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(zipfile.ZipFile, "writestr", full)
        with pytest.raises(OSError, match="disk full"):
            backup_corpus(corpus, tmp_path / "out", now=NOW)
        assert list((tmp_path / "out").iterdir()) == []

    def test_verdicts_and_catalog_are_not_needed(self, corpus, tmp_path):
        # They are rebuilt after a restore, so a corpus without them backs up.
        for derived in verdicts_dir(corpus).iterdir():
            derived.unlink()
        catalog_path(corpus).unlink()
        assert backup_corpus(corpus, tmp_path / "out", now=NOW).files == 4


class TestCheck:
    @pytest.fixture
    def archive(self, corpus, tmp_path) -> Path:
        return backup_corpus(corpus, tmp_path / "out", now=NOW).path

    def test_an_altered_file_is_caught(self, archive, tmp_path):
        tampered = _rezip(archive, tmp_path / "t.zip", lambda name, data: (
            b"another record\n" if name == "games/obs-g1.jsonl" else data))
        assert check_archive(tampered).problems == ("altered: games/obs-g1.jsonl",)

    def test_a_size_that_disagrees_with_the_manifest_is_caught(self, archive, tmp_path):
        def shrink(name, data):
            if name != MANIFEST_FILE:
                return data
            manifest = json.loads(data)
            manifest["files"]["build.json"]["size"] = 1
            return json.dumps(manifest).encode()

        assert check_archive(_rezip(archive, tmp_path / "t.zip", shrink)).problems == (
            "altered: build.json",)

    def test_a_missing_file_is_caught(self, archive, tmp_path):
        dropped = _rezip(archive, tmp_path / "t.zip", lambda name, data: (
            None if name == "raw/box/s1.jsonl" else data))
        check = check_archive(dropped)
        assert (check.ok, check.problems) == (False, ("missing: raw/box/s1.jsonl",))

    def test_a_file_the_manifest_does_not_list_is_caught(self, archive, tmp_path):
        extra = _rezip(archive, tmp_path / "t.zip", lambda name, data: data)
        with zipfile.ZipFile(extra, "a") as zipped:
            zipped.writestr("games/obs-extra.jsonl", "smuggled\n")
        assert check_archive(extra).problems == ("unlisted: games/obs-extra.jsonl",)

    def test_every_problem_is_reported(self, archive, tmp_path):
        both = _rezip(archive, tmp_path / "t.zip", lambda name, data: (
            None if name == "build.json"
            else b"x" if name == "raw/laptop/s2.jsonl" else data))
        assert check_archive(both).problems == (
            "missing: build.json", "altered: raw/laptop/s2.jsonl")

    def test_a_member_whose_bytes_rotted_is_unreadable(self, tmp_path):
        # Stored, not deflated, so a flipped byte reaches the CRC check
        # rather than breaking the decompressor first.
        data = b"a record that will rot\n"
        manifest = {"format": MANIFEST_FORMAT, "files": {"games/obs-g1.jsonl": {
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}}}
        path = tmp_path / "rot.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zipped:
            zipped.writestr("games/obs-g1.jsonl", data)
            zipped.writestr(MANIFEST_FILE, json.dumps(manifest))
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"will rot", b"did  rot", 1))
        (problem,) = check_archive(path).problems
        assert problem.startswith("unreadable: games/obs-g1.jsonl")

    def test_a_file_that_is_not_a_zip_is_refused(self, tmp_path):
        path = tmp_path / "not.zip"
        path.write_text("plain text")
        with pytest.raises(CorpusError, match="not a zip"):
            check_archive(path)

    def test_a_zip_without_a_manifest_is_refused(self, tmp_path):
        path = tmp_path / "bare.zip"
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr("games/obs-g1.jsonl", "a record\n")
        with pytest.raises(CorpusError, match="no MANIFEST.json"):
            check_archive(path)

    def test_a_manifest_of_another_format_is_refused(self, tmp_path):
        path = tmp_path / "future.zip"
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr(MANIFEST_FILE, json.dumps({"format": "other/9", "files": {}}))
        with pytest.raises(CorpusError, match="other/9"):
            check_archive(path)
