import os
import random
import zlib

import pytest

from flate import adler32, crc32

CASES = [b"", b"a", b"abc", b"\xff" * 10_000, bytes(range(256)) * 50, os.urandom(70_001)]


@pytest.mark.parametrize("data", CASES, ids=lambda d: "len%d" % len(d))
def test_crc32_matches_zlib(data):
    assert crc32(data) == zlib.crc32(data)


@pytest.mark.parametrize("data", CASES, ids=lambda d: "len%d" % len(d))
def test_adler32_matches_zlib(data):
    assert adler32(data) == zlib.adler32(data)


def test_running_checksums():
    rng = random.Random(7)
    data = bytes(rng.getrandbits(8) for _ in range(20_000))
    c, a = 0, 1
    for i in range(0, len(data), 777):
        c = crc32(data[i:i + 777], c)
        a = adler32(data[i:i + 777], a)
    assert c == zlib.crc32(data)
    assert a == zlib.adler32(data)


def test_adler_modulus_wraps():
    # long run of 0xff pushes both sums past 65521 many times over
    data = b"\xff" * 1_000_000
    assert adler32(data) == zlib.adler32(data)
