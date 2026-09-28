"""Pins the corpus parse cache: when an entry is trusted, and when it is not."""

import json
import types
from pathlib import Path

import pytest
from contrai_data import encode, file_sha256

from contrai_scraper import corpus_cache
from contrai_scraper.corpus_cache import (
    CACHE_FORMAT,
    ParsedLog,
    cache_path,
    load_parsed,
    parse_or_load,
    parser_fingerprint,
    prune_cache,
    save_parsed,
)


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "corpus"


@pytest.fixture
def log(tmp_path) -> Path:
    path = tmp_path / "raw" / "box" / "session-1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind": "header"}\n', encoding="utf-8")
    return path


@pytest.fixture
def parsed(source_game) -> ParsedLog:
    return ParsedLog(visits=3, not_tournament=1, records=(tuple(source_game),))


def _save(root, log, parsed, fingerprint="fp"):
    save_parsed(root, "box", log, parsed, fingerprint=fingerprint, digest=file_sha256(log))


class TestFingerprint:
    def test_it_is_stable(self, profile):
        assert parser_fingerprint(profile) == parser_fingerprint(profile)

    def test_another_wire_vocabulary_changes_it(self, tmp_path, profile, profile_text):
        # A renamed wire field reads other bytes out of the same log.
        other = tmp_path / "other-profile.toml"
        other.write_text(profile_text.replace('table_id = "table.id"',
                                              'table_id = "table.key"'), encoding="utf-8")
        from contrai_scraper import load_profile

        assert parser_fingerprint(load_profile(other)) != parser_fingerprint(profile)

    def test_another_ruleset_changes_it(self, profile):
        import dataclasses

        rules = dataclasses.replace(profile.rules, preset="classic")
        assert parser_fingerprint(dataclasses.replace(profile, rules=rules)) != (
            parser_fingerprint(profile))

    def test_an_edited_source_file_changes_it(self, tmp_path, profile, monkeypatch):
        package = tmp_path / "fake_package"
        package.mkdir()
        module = package / "parse.py"
        module.write_text("RULE = 1\n", encoding="utf-8")
        fake = types.ModuleType("fake_package")
        fake.__file__ = str(package / "__init__.py")
        monkeypatch.setattr(corpus_cache, "_PARSER_PACKAGES", (fake,))
        before = parser_fingerprint(profile)
        module.write_text("RULE = 2\n", encoding="utf-8")
        assert parser_fingerprint(profile) != before


class TestEntries:
    def test_an_entry_reads_back_what_was_saved(self, root, log, parsed):
        _save(root, log, parsed)
        assert load_parsed(root, "box", log, fingerprint="fp",
                           digest=file_sha256(log)) == parsed

    def test_it_lives_under_the_corpus_cache(self, root, log):
        assert cache_path(root, "box", log) == root / "cache" / "box" / "session-1.jsonl.json"

    def test_no_entry_is_a_miss(self, root, log):
        assert load_parsed(root, "box", log, fingerprint="fp",
                           digest=file_sha256(log)) is None

    def test_another_parser_is_a_miss(self, root, log, parsed):
        _save(root, log, parsed, fingerprint="old")
        assert load_parsed(root, "box", log, fingerprint="new",
                           digest=file_sha256(log)) is None

    def test_a_log_that_grew_is_a_miss(self, root, log, parsed):
        _save(root, log, parsed)
        with log.open("a", encoding="utf-8") as handle:
            handle.write('{"kind": "frame"}\n')
        assert load_parsed(root, "box", log, fingerprint="fp",
                           digest=file_sha256(log)) is None

    @pytest.mark.parametrize(
        "damage",
        [
            lambda entry: "not json",
            lambda entry: json.dumps({**entry, "format": "other/9"}),
            lambda entry: json.dumps({k: v for k, v in entry.items() if k != "visits"}),
            lambda entry: json.dumps({**entry, "records": [["not a record line"]]}),
            lambda entry: json.dumps({**entry, "records": 7}),
        ],
        ids=["not-json", "format", "missing-key", "bad-line", "wrong-type"],
    )
    def test_a_damaged_entry_is_a_miss(self, root, log, parsed, damage):
        _save(root, log, parsed)
        path = cache_path(root, "box", log)
        entry = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(damage(entry), encoding="utf-8")
        assert load_parsed(root, "box", log, fingerprint="fp",
                           digest=file_sha256(log)) is None

    def test_an_entry_holds_the_record_codec_lines(self, root, log, parsed):
        _save(root, log, parsed)
        entry = json.loads(cache_path(root, "box", log).read_text(encoding="utf-8"))
        assert (entry["format"], entry["records"][0][0]) == (
            CACHE_FORMAT, encode(parsed.records[0][0]))

    def test_an_interrupted_save_leaves_no_partial_entry(self, root, log, parsed,
                                                         monkeypatch):
        def broken(source, target):
            raise OSError("disk full")

        monkeypatch.setattr(corpus_cache.os, "replace", broken)
        with pytest.raises(OSError, match="disk full"):
            _save(root, log, parsed)
        assert list(cache_path(root, "box", log).parent.iterdir()) == []


class TestPrune:
    def test_entries_for_logs_that_are_gone_are_deleted(self, root, tmp_path, parsed):
        kept, gone = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        for log in (kept, gone):
            log.write_text("x\n", encoding="utf-8")
            _save(root, log, parsed)
        assert prune_cache(root, [cache_path(root, "box", kept)]) == 1
        assert [p.name for p in (root / "cache" / "box").iterdir()] == ["a.jsonl.json"]

    def test_a_corpus_without_a_cache_prunes_nothing(self, root):
        assert prune_cache(root, []) == 0


class TestParseOrLoad:
    def test_a_first_build_parses_and_a_second_reads_back(self, root, log, parsed):
        calls = []

        def parse(path):
            calls.append(path)
            return parsed

        first = parse_or_load(root, "box", log, fingerprint="fp", parse=parse)
        second = parse_or_load(root, "box", log, fingerprint="fp", parse=parse)
        assert (first, second, calls) == ((parsed, False), (parsed, True), [log])

    def test_force_parses_even_a_good_entry(self, root, log, parsed):
        calls = []

        def parse(path):
            calls.append(path)
            return parsed

        parse_or_load(root, "box", log, fingerprint="fp", parse=parse)
        assert parse_or_load(root, "box", log, fingerprint="fp", parse=parse,
                             force=True) == (parsed, False)
        assert len(calls) == 2
