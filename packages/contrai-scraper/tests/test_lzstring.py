"""Pins the LZ-String codec the deal payload arrives in."""

import pytest

from contrai_scraper import compress_to_base64, decompress_from_base64

#: One payload and its encoding, cross-checked against an independent decoder
#: already validated on real traffic. Round-tripping alone cannot catch a
#: *symmetric* mistake — an encoder and a decoder that agree with each other
#: and with nobody else — so the pair is pinned here. The payload text is
#: invented; nothing site-shaped is committed.
FIXED_VECTOR = (
    '{"cards":["2w","3x","4y","5z"],"n":1}',
    "N4IgxghgTgJgziAXAbRAJgO4gDQgMwAeOIALAJ7ECsAXiALq4B2SAjAL5A==",
)


class TestRoundTrip:
    @pytest.mark.parametrize("text", [
        "", "a", "aaaaaaaaaaaaaaaa",
        '{"cards": ["2w", "3x", "4y", "5z"], "n": 1}',
        "unicode: é ☃ 漢",
        "x" * 5000,
        # A wide character *first* takes the sixteen-bit preamble branch,
        # which an ASCII payload never reaches.
        "漢字漢字漢字",
        # Long repetition then fresh characters: the only shape that makes
        # the dictionary widen from inside a new-character entry, on both
        # sides of the codec.
        "a" * 20 + "bcde",
    ])
    def test_compress_then_decompress_is_identity(self, text):
        assert decompress_from_base64(compress_to_base64(text)) == text


class TestFixedVector:
    def test_a_pinned_payload_still_decodes(self):
        plain, encoded = FIXED_VECTOR
        assert decompress_from_base64(encoded) == plain


class TestRefusals:
    def test_a_non_base64_payload_returns_none(self):
        assert decompress_from_base64("!!!not base64!!!") is None

    def test_an_empty_payload_decodes_to_empty(self):
        assert decompress_from_base64("") == ""

    def test_a_truncated_payload_decodes_to_empty(self):
        # A raw log line cut off mid-payload must be a value, not a crash:
        # the reader runs off the end and the stream ends without its marker.
        _, encoded = FIXED_VECTOR
        assert decompress_from_base64(encoded[:2]) == ""

    def test_a_corrupt_header_returns_none(self):
        # The first two bits name the preamble's width; there is no third
        # width, so a payload starting with both bits set is not LZ-String.
        assert decompress_from_base64("wwww") is None

    def test_a_reference_past_the_dictionary_returns_none(self):
        # A payload whose codes address dictionary entries that were never
        # built. Found by search — there is no readable way to write one.
        assert decompress_from_base64("Jq8Mt3oa9") is None
