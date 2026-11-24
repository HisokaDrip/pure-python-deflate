import gzip
import zlib

import pytest

import flate
from flate import compress_raw, gzip_compress, gzip_decompress, inflate, zlib_compress, zlib_decompress

from helpers import sample_inputs

INPUTS = sample_inputs()
NAMES = list(INPUTS)


def zlib_compress_with(data, level=6, wbits=15, mem=8, strategy=zlib.Z_DEFAULT_STRATEGY, flushes=()):
    c = zlib.compressobj(level, zlib.DEFLATED, wbits, mem, strategy)
    out = []
    pos = 0
    for at, mode in flushes:
        out.append(c.compress(data[pos:at]))
        out.append(c.flush(mode))
        pos = at
    out.append(c.compress(data[pos:]))
    out.append(c.flush())
    return b"".join(out)


# ---- ours -> zlib


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("level", range(10))
def test_zlib_reads_our_raw(name, level):
    data = INPUTS[name]
    assert zlib.decompress(compress_raw(data, level), -15) == data


@pytest.mark.parametrize("name", NAMES)
def test_zlib_reads_our_zlib_stream(name):
    data = INPUTS[name]
    assert zlib.decompress(zlib_compress(data, 9)) == data


@pytest.mark.parametrize("name", NAMES)
def test_gzip_module_reads_ours(name):
    data = INPUTS[name]
    assert gzip.decompress(gzip_compress(data, 6, filename="x.bin", mtime=0)) == data


@pytest.mark.parametrize("force", ["fixed", "dynamic"])
def test_zlib_reads_forced_blocks(force):
    for name, data in INPUTS.items():
        assert zlib.decompress(compress_raw(data, 6, force=force), -15) == data, name


# ---- zlib -> ours


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("level", range(10))
def test_we_read_zlib_levels(name, level):
    data = INPUTS[name]
    assert zlib_decompress(zlib.compress(data, level)) == data


@pytest.mark.parametrize("wbits", list(range(9, 16)))
def test_we_read_all_window_sizes(wbits):
    data = INPUTS["mixed"] + INPUTS["window_32768"]
    assert zlib_decompress(zlib_compress_with(data, 9, wbits)) == data
    assert inflate(zlib_compress_with(data, 9, -wbits))[0] == data
    assert gzip_decompress(zlib_compress_with(data, 9, 16 + wbits)) == data


@pytest.mark.parametrize("strategy", [zlib.Z_FILTERED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE, zlib.Z_FIXED])
@pytest.mark.parametrize("name", ["text", "mixed", "run_1000", "random_small", "empty"])
def test_we_read_zlib_strategies(strategy, name):
    data = INPUTS[name]
    assert zlib_decompress(zlib_compress_with(data, 6, strategy=strategy)) == data


@pytest.mark.parametrize("mem", [1, 4, 9])
def test_we_read_small_and_big_memlevel(mem):
    # memLevel 1 makes zlib emit lots of small blocks
    data = INPUTS["mixed"] * 3
    assert zlib_decompress(zlib_compress_with(data, 6, mem=mem)) == data


def test_we_read_sync_and_full_flushes():
    # sync/full flush emit empty stored blocks mid-stream
    data = INPUTS["mixed"]
    flushes = [(100, zlib.Z_SYNC_FLUSH), (2000, zlib.Z_FULL_FLUSH), (2001, zlib.Z_SYNC_FLUSH),
               (5000, zlib.Z_BLOCK)]
    stream = zlib_compress_with(data, 6, flushes=flushes)
    trace = []
    assert zlib_decompress(stream, trace=trace) == data
    assert any(b["type"] == "stored" and b["out_bytes"] == 0 for b in trace)


def test_we_read_preset_dictionary():
    zdict = b"the quick brown fox"
    data = INPUTS["text"]
    c = zlib.compressobj(9, zlib.DEFLATED, 15, 8, zlib.Z_DEFAULT_STRATEGY, zdict)
    stream = c.compress(data) + c.flush()
    assert zlib_decompress(stream, zdict=zdict) == data
    with pytest.raises(flate.DeflateError):
        zlib_decompress(stream)
    with pytest.raises(flate.DeflateError):
        zlib_decompress(stream, zdict=b"wrong")


def test_gzip_multi_member_and_header_fields(tmp_path):
    a, b = INPUTS["text"], INPUTS["random_small"]
    path = tmp_path / "x.gz"
    with gzip.GzipFile(filename="hello.txt", mode="wb", fileobj=open(path, "wb"), mtime=1234) as f:
        f.write(a)
    stream = path.read_bytes() + gzip.compress(b)
    members = list(flate.containers.gzip_members(stream))
    assert [p for _, p in members] == [a, b]
    assert members[0][0]["name"] == "hello.txt"
    assert members[0][0]["mtime"] == 1234


def test_gzip_header_crc_and_comment():
    # gzip module never writes FHCRC/FCOMMENT/FEXTRA, so build one by hand
    import struct
    body = zlib_compress_with(INPUTS["text"], 6, -15)
    flags = 2 | 4 | 8 | 16
    header = b"\x1f\x8b\x08" + bytes([flags]) + struct.pack("<IBB", 0, 0, 3)
    header += struct.pack("<H", 4) + b"ab\x01\x00" + b"name\0" + b"a comment\0"
    header += struct.pack("<H", zlib.crc32(header) & 0xFFFF)
    trailer = struct.pack("<II", zlib.crc32(INPUTS["text"]), len(INPUTS["text"]))
    stream = header + body + trailer
    assert gzip.decompress(stream) == INPUTS["text"]   # sanity: real gzip agrees
    info, payload = next(flate.containers.gzip_members(stream))
    assert payload == INPUTS["text"]
    assert info["comment"] == "a comment"
