# Seeded random fuzzing, no hypothesis. Each seed is deterministic so a
# failure can be reproduced by re-running that one case.

import random
import zlib

import pytest

from flate import DeflateError, compress_raw, inflate


def random_input(rng):
    kind = rng.randrange(5)
    n = rng.choice([0, 1, 2, 3, 10, 100, 1000, 5000, 40_000])
    if kind == 0:
        return bytes(rng.getrandbits(8) for _ in range(n))
    if kind == 1:   # small alphabet
        alphabet = bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 6)))
        return bytes(rng.choice(alphabet) for _ in range(n))
    if kind == 2:   # repeated phrases with noise, roughly like text
        words = [bytes(rng.getrandbits(7) for _ in range(rng.randint(1, 8))) for _ in range(30)]
        out = bytearray()
        while len(out) < n:
            out += rng.choice(words)
            if rng.random() < 0.05:
                out.append(rng.getrandbits(8))
        return bytes(out[:n])
    if kind == 3:   # long runs
        out = bytearray()
        while len(out) < n:
            out += bytes([rng.getrandbits(8)]) * rng.randint(1, 600)
        return bytes(out[:n])
    # copy of an earlier chunk at a random far distance
    head = bytes(rng.getrandbits(8) for _ in range(min(n, 500)))
    gap = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 33_000)))
    return head + gap + head


@pytest.mark.parametrize("seed", range(40))
def test_fuzz_roundtrip_both_ways(seed):
    rng = random.Random(seed)
    data = random_input(rng)
    level = rng.randrange(10)
    ours = compress_raw(data, level)
    assert zlib.decompress(ours, -15) == data
    assert inflate(ours)[0] == data
    theirs = zlib.compressobj(level, zlib.DEFLATED, -rng.randint(9, 15))
    stream = theirs.compress(data) + theirs.flush()
    assert inflate(stream)[0] == data


def zlib_inflate_or_none(stream):
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(stream)
    except zlib.error:
        return None
    return out if d.eof else None


@pytest.mark.parametrize("seed", range(30))
def test_fuzz_corrupted_streams_agree_with_zlib(seed):
    # Flip bits in valid streams. We must never crash with anything other than
    # DeflateError, and when either side accepts the stream both must produce
    # the same bytes.
    rng = random.Random(1000 + seed)
    data = random_input(rng)
    stream = bytearray(compress_raw(data, rng.randrange(1, 10)))
    for _ in range(20):
        broken = bytearray(stream)
        for _ in range(rng.randint(1, 4)):
            if broken:
                broken[rng.randrange(len(broken))] ^= 1 << rng.randrange(8)
        if rng.random() < 0.3:
            broken = broken[:rng.randrange(len(broken) + 1)]
        broken = bytes(broken)
        expected = zlib_inflate_or_none(broken)
        try:
            ours = inflate(broken)[0]
        except DeflateError:
            ours = None
        assert ours == expected


@pytest.mark.parametrize("seed", range(10))
def test_fuzz_random_garbage(seed):
    rng = random.Random(5000 + seed)
    for _ in range(200):
        junk = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 64)))
        expected = zlib_inflate_or_none(junk)
        try:
            ours = inflate(junk)[0]
        except DeflateError:
            ours = None
        assert ours == expected
