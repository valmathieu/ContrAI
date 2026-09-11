"""A dependency-free port of the LZ-String base64 codec.

LZ-String is a JavaScript LZW variant that packs its output into a chosen
number of bits per character; the base64 flavour used here packs six, over the
standard alphabet. Browser applications reach for it to keep a large JSON blob
small inside a message that must stay text, which is why a decoder is needed
on this side of the socket at all.

Both directions are here. Decoding is what the parser needs; encoding exists
so the suite can build a payload and read it back, and so an encoder bug and a
decoder bug cannot cancel out unnoticed — a fixed vector produced by an
independent implementation is pinned in the tests for exactly that reason.

The code is transcribed from the reference implementation rather than
rewritten: LZ-String's bit order, its two-entry-wide dictionary preamble and
its "enlarge in" counter are conventions, not derivations, and a tidier-looking
Python version is a decoder that disagrees with every producer.
"""

from __future__ import annotations

from collections.abc import Callable

#: The alphabet LZ-String's base64 flavour packs into, and the reverse index.
#: Note the trailing ``=``: LZ-String gives the pad character an index of its
#: own rather than treating it as padding, so a decoder must accept it.
_KEY_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_B64_INDEX = {character: index for index, character in enumerate(_KEY_B64)}

#: Six bits per output character, and therefore a reset mask of ``1 << 5``.
_BITS_PER_CHAR = 6
_RESET_VALUE = 32


def decompress_from_base64(value: str) -> str | None:
    """Decode an LZ-String base64 payload.

    Base64 is the correct alphabet, not encoded-URI-component: a payload
    holding a literal ``/`` decodes only under this one.

    Args:
        value: The encoded payload.

    Returns:
        The decoded text, ``""`` for an empty payload, or ``None`` when the
        payload is not decodable — a character outside the alphabet, or a
        dictionary reference that points past what has been built. A payload
        that is merely cut short decodes to ``""``, as in the reference.
    """

    if not value:
        return "" if value == "" else None
    if any(character not in _B64_INDEX for character in value):
        return None

    def read_at(index: int) -> int:
        # Past the end the reference's ``charAt`` yields an empty string and
        # the arithmetic that follows goes to NaN, which its own
        # ran-off-the-end check then catches. Indexing a Python string raises
        # instead, so a truncated payload is fed a zero here and caught by the
        # same check — the alternative is an IndexError out of a decoder whose
        # documented answer to bad input is a value.
        return _B64_INDEX[value[index]] if index < len(value) else 0

    return _decompress(len(value), _RESET_VALUE, read_at)


def compress_to_base64(value: str) -> str:
    """Encode text as an LZ-String base64 payload.

    Args:
        value: The text to encode.

    Returns:
        The encoded payload, padded with ``=`` to a multiple of four so it is
        valid base64.
    """

    encoded = _compress(value, _BITS_PER_CHAR, lambda index: _KEY_B64[index])
    padding = {0: "", 1: "===", 2: "==", 3: "="}[len(encoded) % 4]
    return encoded + padding


def _decompress(
    length: int, reset_value: int, get_next: Callable[[int], int]
) -> str | None:
    """The shared LZ-String decompression core, transcribed from the reference.

    Args:
        length: How many encoded characters there are.
        reset_value: The bit mask a fresh character starts at.
        get_next: Reads the ``i``-th encoded character as an integer.

    Returns:
        The decoded text, or ``None`` when the stream is not decodable.
    """

    dictionary: list[str] = [chr(0), chr(1), chr(2)]
    enlarge_in, dict_size, num_bits = 4, 4, 3
    result: list[str] = []

    data_val = get_next(0)
    data_position = reset_value
    data_index = 1

    def read_bits(count: int) -> int:
        nonlocal data_val, data_position, data_index
        bits, power, maxpower = 0, 1, 1 << count
        while power != maxpower:
            # The bit is read *before* the position shifts; swapping the two
            # yields a decoder that silently returns empty strings.
            resb = data_val & data_position
            data_position >>= 1
            if data_position == 0:
                data_position = reset_value
                data_val = get_next(data_index)
                data_index += 1
            bits |= (1 if resb > 0 else 0) * power
            power <<= 1
        return bits

    next_ = read_bits(2)
    if next_ == 0:
        char = chr(read_bits(8))
    elif next_ == 1:
        char = chr(read_bits(16))
    elif next_ == 2:
        return ""
    else:
        return None

    dictionary.append(char)
    word = char
    result.append(char)

    while True:
        if data_index > length:
            return ""
        index = read_bits(num_bits)
        if index == 0:
            dictionary.append(chr(read_bits(8)))
            index = dict_size
            dict_size += 1
            enlarge_in -= 1
        elif index == 1:
            dictionary.append(chr(read_bits(16)))
            index = dict_size
            dict_size += 1
            enlarge_in -= 1
        elif index == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

        if index < len(dictionary):
            entry = dictionary[index]
        elif index == dict_size:
            entry = word + word[0]
        else:
            return None

        result.append(entry)
        dictionary.append(word + entry[0])
        dict_size += 1
        enlarge_in -= 1
        word = entry
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1


def _compress(
    uncompressed: str, bits_per_char: int, get_char: Callable[[int], str]
) -> str:
    """The shared LZ-String compression core, transcribed from the reference.

    Args:
        uncompressed: The text to encode.
        bits_per_char: How many bits each output character carries.
        get_char: Renders a packed value as one output character.

    Returns:
        The packed output. Empty input is *not* empty output: the reference
        still emits the end-of-stream marker, and a decoder that has learned
        to expect nothing from one producer chokes on the other.
    """

    dictionary: dict[str, int] = {}
    to_create: set[str] = set()
    word = ""
    enlarge_in = 2  # Compensates for the first entry, which must not count.
    dict_size = 3
    num_bits = 2
    data: list[str] = []
    data_val = 0
    data_position = 0

    def write_bit(bit: int) -> None:
        """Push one bit, flushing a character every ``bits_per_char``."""

        nonlocal data_val, data_position
        data_val = (data_val << 1) | bit
        if data_position == bits_per_char - 1:
            data_position = 0
            data.append(get_char(data_val))
            data_val = 0
        else:
            data_position += 1

    def write_value(value: int, count: int) -> None:
        """Push ``count`` bits of ``value``, least significant first."""

        for _ in range(count):
            write_bit(value & 1)
            value >>= 1

    def write_word(current: str) -> None:
        """Emit one dictionary word, defining it first if it is new."""

        nonlocal enlarge_in, num_bits
        if current in to_create:
            # A character seen for the first time is written out literally,
            # prefixed by a width marker: zero for a byte, one for a wide
            # character. The marker is the only thing that keeps a decoder
            # from reading the eight bits of an accented letter as two.
            code = ord(current[0])
            if code < 256:
                write_value(0, num_bits)
                write_value(code, 8)
            else:
                for index in range(num_bits):
                    write_bit(1 if index == 0 else 0)
                write_value(code, 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 1 << num_bits
                num_bits += 1
            to_create.discard(current)
        else:
            write_value(dictionary[current], num_bits)

    for character in uncompressed:
        if character not in dictionary:
            dictionary[character] = dict_size
            dict_size += 1
            to_create.add(character)

        candidate = word + character
        if candidate in dictionary:
            word = candidate
            continue

        write_word(word)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
        dictionary[candidate] = dict_size
        dict_size += 1
        word = character

    if word:
        write_word(word)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

    # The end-of-stream marker, then enough zero bits to finish the character
    # in hand — a reader stops at the marker, so the padding is never seen.
    write_value(2, num_bits)
    while True:
        data_val <<= 1
        if data_position == bits_per_char - 1:
            data.append(get_char(data_val))
            break
        data_position += 1

    return "".join(data)
