from pathlib import Path

import pytest

from flate import compress_raw, decompress, inflate
from flate import deflate as flate_src
from flate.lz77 import expand, tokenize
from flate.tables import MAX_MATCH, WINDOW_SIZE

from helpers import sample_inputs

INPUTS = sample_inputs()


@pytest.mark.parametrize("name", INPUTS)
@pytest.mark.parametrize("level", range(10))
def test_roundtrip_all_levels(name, level):
    data = INPUTS[name]
    packed = compress_raw(data, level)
    out, used = inflate(packed)
    assert out == data
    assert used == len(packed)


@pytest.mark.parametrize("force", ["stored", "fixed", "dynamic"])
@pytest.mark.parametrize("name", ["empty", "one_byte", "text", "random_80k", "window_32768"])
def test_forced_block_types(name, force):
    data = INPUTS[name]
    report = []
    packed = compress_raw(data, 6, force=force, report=report)
    assert set(report) == {force}
    assert inflate(packed)[0] == data


@pytest.mark.parametrize("fmt", ["zlib", "gzip", "raw"])
def test_containers_roundtrip(fmt):
    from flate import compress
    data = INPUTS["mixed"]
    assert decompress(compress(data, 6, fmt), fmt) == data


@pytest.mark.parametrize("level", range(1, 10))
def test_tokens_are_legal(level):
    data = INPUTS["window_32768"] + INPUTS["run_1000"] + INPUTS["text"]
    tokens = tokenize(data, level)
    assert expand(tokens) == data
    for t in tokens:
        if t >= 256:
            assert 3 <= t >> 16 <= MAX_MATCH
            assert 1 <= t & 0xFFFF <= WINDOW_SIZE


def test_finds_max_distance_match():
    data = INPUTS["window_32768"]
    dists = {t & 0xFFFF for t in tokenize(data, 9) if t >= 256}
    assert WINDOW_SIZE in dists


def test_never_reaches_past_window():
    data = INPUTS["window_32769"]
    dists = {t & 0xFFFF for t in tokenize(data, 9) if t >= 256}
    assert max(dists, default=0) <= WINDOW_SIZE


def test_long_runs_use_258_matches():
    tokens = tokenize(b"a" * 5000, 6)
    lengths = [t >> 16 for t in tokens if t >= 256]
    assert lengths.count(258) >= 18


def test_level_trend():
    # not strictly monotonic in general: on the 4-letter DNA-ish sample level 9
    # comes out bigger than level 6 (zlib does exactly the same thing, longer
    # matches early aren't always better). On source code effort should pay.
    data = Path(flate_src.__file__).read_bytes()
    sizes = [len(compress_raw(data, lvl)) for lvl in (0, 1, 6, 9)]
    assert sizes[0] > sizes[1] > sizes[2] >= sizes[3]


def test_incompressible_falls_back_to_stored():
    report = []
    data = INPUTS["random_80k"]
    packed = compress_raw(data, 6, report=report)
    assert "stored" in report
    # 5 bytes of framing per 64K stored block, and that's it
    assert len(packed) <= len(data) + 5 * 2 + 1
