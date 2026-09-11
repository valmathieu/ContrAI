"""Pins the raw log: header, verbatim text, dedup, replay."""

import json

import pytest

from contrai_scraper import (
    RawFrame,
    RawLogFrameSource,
    RawLogWriter,
    WireError,
    new_session_id,
    raw_path,
    read_raw_log,
)


def frame(text: str, socket: int = 0) -> RawFrame:
    return RawFrame(socket=socket, direction="recv", at=1.5, text=text)


class TestWriter:
    def test_the_first_line_is_a_header(self, tmp_path):
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one"))
        first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert first["kind"] == "header" and first["format"] == "contrai-raw/1"

    def test_frame_text_is_stored_verbatim(self, tmp_path):
        # Re-parsing depends on this: whatever the socket said, byte for byte.
        path = tmp_path / "s.jsonl"
        awkward = '{"event":"x","data":"é ☃ \\"quoted\\""}'
        with RawLogWriter(path) as log:
            log.write_frame(frame(awkward))
        assert read_raw_log(path)[0].text == awkward

    def test_the_mirrored_copy_is_dropped(self, tmp_path):
        path = tmp_path / "s.jsonl"
        body = json.dumps({"id": "7", "event": "payload", "data": "{}"})
        with RawLogWriter(path) as log:
            log.write_frame(frame(body, socket=0))
            log.write_frame(frame(body, socket=1))
        assert len(read_raw_log(path)) == 1

    def test_dedup_can_be_switched_off(self, tmp_path):
        path = tmp_path / "s.jsonl"
        body = json.dumps({"id": "7", "event": "payload", "data": "{}"})
        with RawLogWriter(path, dedup=False) as log:
            log.write_frame(frame(body, socket=0))
            log.write_frame(frame(body, socket=1))
        assert len(read_raw_log(path)) == 2

    def test_a_frame_with_no_identity_is_always_written(self, tmp_path):
        # A keepalive is not JSON and has no de-duplication key; dropping
        # every repeat of it would empty the log of its heartbeat.
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("tick"))
            log.write_frame(frame("tick"))
        assert len(read_raw_log(path)) == 2

    def test_a_panel_read_is_a_line_of_its_own(self, tmp_path):
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_panel("scoreboard", "<html>…</html>")
        line = read_raw_log(path)[0]
        assert (line.kind, line.fields["name"]) == ("panel", "scoreboard")

    def test_a_note_carries_whatever_it_is_given(self, tmp_path):
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_note(event="left the table", tables_seen=3)
        line = read_raw_log(path)[0]
        assert (line.kind, line.fields["tables_seen"]) == ("note", 3)

    def test_every_line_is_readable_before_the_file_is_closed(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = RawLogWriter(path)
        log.write_frame(frame("one"))
        assert len(read_raw_log(path)) == 1
        log.close()

    def test_re_opening_a_log_does_not_write_a_second_header(self, tmp_path):
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one"))
        with RawLogWriter(path) as log:
            log.write_frame(frame("two"))
        assert [line.kind for line in read_raw_log(path)] == ["frame", "frame"]

    def test_writing_to_a_closed_log_is_refused(self, tmp_path):
        log = RawLogWriter(tmp_path / "s.jsonl")
        log.close()
        log.close()
        with pytest.raises(ValueError):
            log.write_frame(frame("one"))

    def test_line_endings_are_lf_on_every_platform(self, tmp_path):
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one"))
        assert b"\r" not in path.read_bytes()


class TestReader:
    def test_an_empty_log_reads_as_nothing(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text("", encoding="utf-8")
        assert read_raw_log(path) == ()

    def test_a_truncated_last_line_is_dropped(self, tmp_path):
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one"))
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"kind": "fra')
        assert len(read_raw_log(path)) == 1

    def test_an_unknown_format_is_refused(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text('{"kind":"header","format":"contrai-raw/9","session":"s"}\n',
                        encoding="utf-8")
        with pytest.raises(WireError, match="contrai-raw/9"):
            read_raw_log(path)

    def test_a_malformed_line_in_the_middle_is_an_error(self, tmp_path):
        # Only the last line can be a crash artefact; anywhere else it is
        # corruption, and swallowing it would silently drop a frame.
        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one"))
            log.write_frame(frame("two"))
        lines = path.read_text(encoding="utf-8").splitlines()
        lines.insert(2, '{"kind": "fra')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(WireError):
            read_raw_log(path)

    def test_a_line_of_an_unknown_kind_is_an_error(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text('{"kind":"nonsense"}\n{"kind":"nonsense"}\n', encoding="utf-8")
        with pytest.raises(WireError, match="nonsense"):
            read_raw_log(path)


    def test_a_line_that_is_not_an_object_is_an_error(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text("[1, 2]\n[1, 2]\n", encoding="utf-8")
        with pytest.raises(WireError, match="not an object"):
            read_raw_log(path)


class TestLayout:
    def test_logs_live_under_raw(self, tmp_path):
        assert raw_path(tmp_path, "s-1") == tmp_path / "raw" / "s-1.jsonl"

    @pytest.mark.parametrize("session_id", ["", ".", "..", "a/b", "a\\b"])
    def test_a_session_id_that_is_not_one_segment_is_refused(self, tmp_path, session_id):
        # The id comes off the clock and, in the browser half, off the table,
        # so the file name it becomes is checked rather than trusted.
        with pytest.raises(WireError):
            raw_path(tmp_path, session_id)

    def test_session_ids_are_distinct_and_well_formed(self):
        ids = {new_session_id() for _ in range(20)}
        assert len(ids) == 20

    def test_a_session_id_leads_with_its_timestamp(self):
        from datetime import UTC, datetime

        stamped = new_session_id(
            now=datetime(2026, 9, 11, 18, 30, 0, tzinfo=UTC), entropy="abc123"
        )
        assert stamped == "20260911T183000Z-abc123"


class TestNotes:
    def test_a_note_may_not_overwrite_the_common_shape(self, tmp_path):
        with RawLogWriter(tmp_path / "s.jsonl") as log:
            with pytest.raises(ValueError, match="text"):
                log.write_note(text="pretending to be a frame")


class TestIntrospection:
    def test_a_writer_names_its_file_and_reports_being_closed(self, tmp_path):
        path = tmp_path / "s.jsonl"
        log = RawLogWriter(path)
        assert (log.path, log.closed) == (path, False)
        log.close()
        assert log.closed is True


class TestReplay:
    def test_a_log_replays_as_the_frames_that_wrote_it(self, tmp_path):
        import asyncio

        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one", socket=0))
            log.write_frame(frame("two", socket=1))
            log.write_panel("scoreboard", "ignored by a frame source")

        async def scenario():
            return [(f.socket, f.text) async for f in RawLogFrameSource(path)]

        assert asyncio.run(scenario()) == [(0, "one"), (1, "two")]

    def test_a_replayed_source_closes(self, tmp_path):
        import asyncio

        path = tmp_path / "s.jsonl"
        with RawLogWriter(path) as log:
            log.write_frame(frame("one"))

        async def scenario():
            source = RawLogFrameSource(path)
            await source.aclose()
            return [f.text async for f in source]

        assert asyncio.run(scenario()) == []
