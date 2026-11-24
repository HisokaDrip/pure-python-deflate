from .checksums import adler32, crc32
from .containers import (detect_format, gzip_compress, gzip_decompress,
                         zlib_compress, zlib_decompress)
from .deflate import compress_raw
from .errors import DeflateError
from .inflate import decompress_raw, inflate

__version__ = "0.1.0"


def compress(data, level=6, fmt="zlib"):
    if fmt == "zlib":
        return zlib_compress(data, level)
    if fmt == "gzip":
        return gzip_compress(data, level, mtime=0)
    if fmt == "raw":
        return compress_raw(data, level)
    raise ValueError("unknown format %r" % fmt)


def decompress(data, fmt="auto"):
    if fmt == "auto":
        fmt = detect_format(data)
    if fmt == "zlib":
        return zlib_decompress(data)
    if fmt == "gzip":
        return gzip_decompress(data)
    if fmt == "raw":
        out, used = inflate(data)
        return out
    raise ValueError("unknown format %r" % fmt)
