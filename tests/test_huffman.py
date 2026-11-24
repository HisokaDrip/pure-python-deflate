import random

import pytest

from flate.bitio import BitReader, BitWriter
from flate.errors import DeflateError
from flate.huffman import (HuffmanDecoder, canonical_codes, huffman_lengths,
                           limited_lengths, package_merge, reverse_bits)


def kraft(lengths):
    return sum(2.0 ** -n for n in lengths if n)


def cost(freqs, lengths):
    return sum(f * n for f, n in zip(freqs, lengths))


def test_rfc_example_codes():
    # RFC 1951 3.2.2: lengths (3,3,3,3,3,2,4,4) for A..H
    lengths = [3, 3, 3, 3, 3, 2, 4, 4]
    expected = ["010", "011", "100", "101", "110", "00", "1110", "1111"]
    codes = canonical_codes(lengths)
    for code, n, want in zip(codes, lengths, expected):
        assert format(reverse_bits(code, n), "0%db" % n) == want


def fibonacci_freqs(n):
    # Fibonacci weights are the worst case for depth: a plain Huffman tree
    # degenerates into a chain n-1 deep
    a, b = 1, 1
    out = []
    for _ in range(n):
        out.append(a)
        a, b = b, a + b
    return out


def test_fibonacci_overflows_plain_huffman():
    freqs = fibonacci_freqs(30)
    assert max(huffman_lengths(freqs)) == 29


@pytest.mark.parametrize("limit", [7, 9, 12, 15])
def test_length_limit_property(limit):
    freqs = fibonacci_freqs(30) + [0, 0, 5]
    lengths = limited_lengths(freqs, limit)
    assert max(lengths) <= limit
    assert kraft(lengths) == pytest.approx(1.0)   # complete code, nothing wasted
    assert all((f > 0) == (n > 0) for f, n in zip(freqs, lengths))


def test_package_merge_matches_huffman_when_unconstrained():
    rng = random.Random(3)
    for _ in range(50):
        freqs = [rng.choice([0, 0, rng.randint(1, 1000)]) for _ in range(40)]
        if sum(1 for f in freqs if f) < 2:
            continue
        free = huffman_lengths(freqs)
        pm = package_merge(freqs, 15)
        # both optimal, so total cost must agree even if individual lengths differ
        assert cost(freqs, pm) == cost(freqs, free)


def test_package_merge_is_optimal_small_brute_force():
    # every complete prefix code over 5 symbols with max length 3, by brute force
    import itertools
    rng = random.Random(11)
    for _ in range(30):
        freqs = [rng.randint(1, 50) for _ in range(5)]
        best = min(cost(freqs, ls) for ls in itertools.product(range(1, 4), repeat=5)
                   if kraft(ls) <= 1)
        assert cost(freqs, package_merge(freqs, 3)) == best


def test_too_many_symbols_for_limit():
    with pytest.raises(ValueError):
        package_merge([1] * 9, 3)


def test_decoder_roundtrip_including_long_codes():
    freqs = fibonacci_freqs(20) + [3] * 30
    lengths = limited_lengths(freqs, 15)
    assert max(lengths) > 9      # make sure the slow path gets exercised
    codes = canonical_codes(lengths)
    rng = random.Random(5)
    symbols = [rng.randrange(len(freqs)) for _ in range(3000)]
    bw = BitWriter()
    for s in symbols:
        bw.write(codes[s], lengths[s])
    dec = HuffmanDecoder(lengths)
    br = BitReader(bw.getvalue())
    assert [dec.decode(br) for _ in symbols] == symbols


def test_oversubscribed_rejected():
    with pytest.raises(DeflateError):
        HuffmanDecoder([1, 1, 1])


def test_incomplete_rejected_unless_single_code():
    with pytest.raises(DeflateError):
        HuffmanDecoder([2, 2, 2])
    HuffmanDecoder([0, 1, 0])         # single 1-bit code is fine (zlib agrees)
    with pytest.raises(DeflateError):
        HuffmanDecoder([0, 1, 0], allow_incomplete=False)
