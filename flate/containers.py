# zlib (RFC 1950) and gzip (RFC 1952) framing around raw deflate.

import struct
import time

from .bitio import BitReader
from .checksums import adler32, crc32
from .deflate import compress_raw
from .errors import DeflateError
from .inflate import inflate_reader

# ---------------------------------------------------------------- zlib


def _zlib_header(level):
    cmf = 0x78   # CM=8 (deflate), CINFO=7 (32K window)
    if level < 2:
        flevel = 0
    elif level < 6:
        flevel = 1
    elif level == 6:
        flevel = 2
    else:
        flevel = 3
    flg = flevel << 6
    flg |= 31 - ((cmf << 8) | flg) % 31
    return bytes((cmf, flg))


def zlib_compress(data, level=6):
    return _zlib_header(level) + compress_raw(data, level) + struct.pack(">I", adler32(data))


def _zlib_parse_header(data):
    if len(data) < 2:
        raise DeflateError("zlib stream too short")
    cmf, flg = data[0], data[1]
    if cmf & 0x0F != 8:
        raise DeflateError("unknown compression method %d" % (cmf & 0x0F))
    if cmf >> 4 > 7:
        raise DeflateError("invalid window size")
    if ((cmf << 8) | flg) % 31:
        raise DeflateError("incorrect header check")
    return {"window": 1 << ((cmf >> 4) + 8), "level": flg >> 6, "fdict": bool(flg & 0x20)}


def zlib_decompress(data, zdict=None, trace=None):
    header = _zlib_parse_header(data)
    pos = 2
    out = bytearray()
    if header["fdict"]:
        if zdict is None:
            raise DeflateError("stream needs a preset dictionary")
        if len(data) < 6:
            raise DeflateError("zlib stream too short")
        (want,) = struct.unpack(">I", data[2:6])
        if want != adler32(zdict):
            raise DeflateError("wrong preset dictionary")
        pos = 6
        out += zdict    # matches may reach back into the dictionary
    br = BitReader(data, pos)
    inflate_reader(br, out, trace)
    end = br.byte_pos()
    result = bytes(out[len(zdict):]) if header["fdict"] else bytes(out)
    if end + 4 > len(data):
        raise DeflateError("missing adler32 trailer")
    (check,) = struct.unpack(">I", data[end:end + 4])
    if check != adler32(result):
        raise DeflateError("adler32 mismatch")
    return result


# ---------------------------------------------------------------- gzip

FTEXT, FHCRC, FEXTRA, FNAME, FCOMMENT = 1, 2, 4, 8, 16
GZIP_MAGIC = b"\x1f\x8b"


def gzip_compress(data, level=6, filename=None, mtime=None):
    flags = 0
    extra = b""
    if filename:
        flags |= FNAME
        if isinstance(filename, str):
            filename = filename.encode("latin-1", "replace")
        extra += filename + b"\0"
    if mtime is None:
        mtime = int(time.time())
    xfl = 2 if level == 9 else 4 if level == 1 else 0
    header = GZIP_MAGIC + struct.pack("<BBIBB", 8, flags, mtime, xfl, 255) + extra
    trailer = struct.pack("<II", crc32(data), len(data) & 0xFFFFFFFF)
    return header + compress_raw(data, level) + trailer


def _cstring(data, pos):
    end = data.find(b"\0", pos)
    if end < 0:
        raise DeflateError("unterminated string in gzip header")
    return data[pos:end], end + 1


def gzip_parse_header(data, pos=0):
    if data[pos:pos + 2] != GZIP_MAGIC:
        raise DeflateError("not a gzip stream")
    if len(data) < pos + 10:
        raise DeflateError("truncated gzip header")
    method, flags, mtime, xfl, os_id = struct.unpack("<BBIBB", data[pos + 2:pos + 10])
    if method != 8:
        raise DeflateError("unknown compression method %d" % method)
    if flags & 0xE0:
        raise DeflateError("reserved gzip flags set")
    info = {"mtime": mtime, "xfl": xfl, "os": os_id, "name": None, "comment": None}
    p = pos + 10
    if flags & FEXTRA:
        if len(data) < p + 2:
            raise DeflateError("truncated gzip header")
        (xlen,) = struct.unpack("<H", data[p:p + 2])
        p += 2 + xlen
    if flags & FNAME:
        name, p = _cstring(data, p)
        info["name"] = name.decode("latin-1")
    if flags & FCOMMENT:
        comment, p = _cstring(data, p)
        info["comment"] = comment.decode("latin-1")
    if flags & FHCRC:
        if len(data) < p + 2:
            raise DeflateError("truncated gzip header")
        (hcrc,) = struct.unpack("<H", data[p:p + 2])
        if hcrc != crc32(data[pos:p]) & 0xFFFF:
            raise DeflateError("gzip header crc mismatch")
        p += 2
    if p > len(data):
        raise DeflateError("truncated gzip header")
    return info, p


def gzip_members(data, trace=None):
    """Yield (header_info, payload) for each member. `gzip a b > c` style
    concatenation is legal, so keep going until the input runs out."""
    pos = 0
    while pos < len(data):
        # gzip(1) tolerates zero padding after the last member, so do we
        if pos and not data[pos:].strip(b"\0"):
            return
        info, pos = gzip_parse_header(data, pos)
        br = BitReader(data, pos)
        out = inflate_reader(br, trace=trace)
        pos = br.byte_pos()
        if pos + 8 > len(data):
            raise DeflateError("truncated gzip trailer")
        crc, isize = struct.unpack("<II", data[pos:pos + 8])
        pos += 8
        if crc != crc32(out):
            raise DeflateError("crc32 mismatch")
        if isize != len(out) & 0xFFFFFFFF:
            raise DeflateError("length mismatch")
        info["end"] = pos
        yield info, bytes(out)


def gzip_decompress(data):
    return b"".join(payload for _, payload in gzip_members(data))


# ---------------------------------------------------------------- sniffing


def detect_format(data):
    if data[:2] == GZIP_MAGIC:
        return "gzip"
    if len(data) >= 2 and data[0] & 0x0F == 8 and data[0] >> 4 <= 7 \
            and ((data[0] << 8) | data[1]) % 31 == 0:
        return "zlib"
    # a raw stream can start with pretty much anything, so it's the fallback
    return "raw"
