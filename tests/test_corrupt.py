import struct
import zlib

import pytest

from flate import DeflateError, gzip_compress, gzip_decompress, inflate, zlib_compress, zlib_decompress
from flate.bitio import BitWriter


def bits(*fields):
    bw = BitWriter()
    for value, n in fields:
        bw.write(value, n)
    return bw.getvalue()


def test_empty_input():
    with pytest.raises(DeflateError):
        inflate(b"")


def test_block_type_3():
    with pytest.raises(DeflateError, match="block type"):
        inflate(bits((1, 1), (3, 2)))


def test_stored_len_nlen_mismatch():
    with pytest.raises(DeflateError, match="length check"):
        inflate(b"\x01" + struct.pack("<HH", 5, 5) + b"hello")


def test_stored_truncated():
    with pytest.raises(DeflateError):
        inflate(b"\x01" + struct.pack("<HH", 5, 0xFFFA) + b"hel")


def test_distance_too_far_back():
    # fixed block: literal 'a' then length 3 distance 2 (only 1 byte of history)
    from flate.huffman import canonical_codes
    from flate.tables import FIXED_LIT_LENGTHS
    codes = canonical_codes(FIXED_LIT_LENGTHS)
    stream = bits((1, 1), (1, 2), (codes[ord("a")], 8), (codes[257], 7), (1, 5), (codes[256], 7))
    with pytest.raises(DeflateError, match="too far"):
        inflate(stream)


def test_fixed_block_invalid_symbols():
    from flate.huffman import canonical_codes
    from flate.tables import FIXED_LIT_LENGTHS
    # symbol 286 lives in the fixed code but is never valid
    codes = canonical_codes(FIXED_LIT_LENGTHS)
    assert len(codes) == 288
    stream = bits((1, 1), (1, 2), (codes[286], 8))
    with pytest.raises(DeflateError, match="length symbol"):
        inflate(stream)
    # distance code 30
    stream = bits((1, 1), (1, 2), (codes[ord("a")], 8), (codes[257], 7), (0b01111, 5))
    with pytest.raises(DeflateError, match="distance symbol"):
        inflate(stream)


def test_dynamic_too_many_symbols():
    # HLIT field 30 -> 287 lit/len codes, which the format forbids
    with pytest.raises(DeflateError, match="too many"):
        inflate(bits((1, 1), (2, 2), (30, 5), (0, 5), (0, 4)))


def test_dynamic_repeat_without_previous():
    # code length code where only symbol 16 and 0 have lengths (1 bit each)
    fields = [(1, 1), (2, 2), (0, 5), (0, 5), (4 - 4, 4)]
    # CL_ORDER starts 16, 17, 18, 0
    fields += [(1, 3), (0, 3), (0, 3), (1, 3)]
    # first code length symbol: 16 (repeat previous) with no previous
    from flate.huffman import canonical_codes
    cl = [0] * 19
    cl[16] = cl[0] = 1
    codes = canonical_codes(cl)
    fields += [(codes[16], 1), (0, 2)]
    with pytest.raises(DeflateError, match="no previous"):
        inflate(bits(*fields))


def test_truncated_everywhere():
    data = b"truncate me please " * 200
    stream = zlib.compress(data, 9)[2:-4]
    for cut in range(len(stream)):
        with pytest.raises(DeflateError):
            inflate(stream[:cut])


def test_zlib_bad_header_and_checksum():
    good = zlib_compress(b"hello hello hello")
    with pytest.raises(DeflateError, match="header check"):
        zlib_decompress(bytes([good[0], good[1] ^ 1]) + good[2:])
    with pytest.raises(DeflateError, match="compression method"):
        zlib_decompress(b"\x79\x9c" + good[2:])
    with pytest.raises(DeflateError, match="adler32"):
        zlib_decompress(good[:-1] + bytes([good[-1] ^ 0xFF]))
    with pytest.raises(DeflateError, match="trailer"):
        zlib_decompress(good[:-2])


def test_gzip_bad_crc_and_size():
    good = gzip_compress(b"some data" * 10, mtime=0)
    with pytest.raises(DeflateError, match="crc32"):
        gzip_decompress(good[:-8] + b"\0\0\0\0" + good[-4:])
    with pytest.raises(DeflateError, match="length"):
        gzip_decompress(good[:-4] + b"\0\0\0\0")
    with pytest.raises(DeflateError, match="not a gzip"):
        gzip_decompress(good + b"garbage!")
    with pytest.raises(DeflateError, match="reserved"):
        gzip_decompress(good[:3] + b"\x20" + good[4:])


def test_gzip_trailing_zero_padding_ok():
    good = gzip_compress(b"padded", mtime=0)
    assert gzip_decompress(good + b"\0" * 16) == b"padded"
