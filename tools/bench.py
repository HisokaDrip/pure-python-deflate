"""Compression ratio and speed vs zlib on a few real files.

    python tools/bench.py [extra files...]
"""

import importlib
import random
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flate import compress_raw, inflate  # noqa: E402


def corpus(extra):
    files = []
    src = b"".join(p.read_bytes() for p in sorted((ROOT / "flate").glob("*.py")))
    files.append(("flate/*.py (this repo)", src))
    for mod, label in (("typing", "stdlib typing.py"), ("_decimal", "_decimal .so (binary)")):
        try:
            path = importlib.import_module(mod).__file__
        except (ImportError, AttributeError):
            continue
        if path:
            files.append((label, Path(path).read_bytes()))
    rng = random.Random(42)
    files.append(("64K random bytes", bytes(rng.getrandbits(8) for _ in range(65536))))
    for p in extra:
        files.append((p, Path(p).read_bytes()))
    return files


def zlib_raw(data, level):
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def main():
    levels = (1, 6, 9)
    print("%-24s %9s  %s" % ("file", "size", "   ".join("L%d ours / zlib (delta)" % l for l in levels)))
    for label, data in corpus(sys.argv[1:]):
        cells = []
        times = []
        for level in levels:
            t0 = time.perf_counter()
            ours = compress_raw(data, level)
            times.append(time.perf_counter() - t0)
            theirs = zlib_raw(data, level)
            assert zlib.decompress(ours, -15) == data
            assert inflate(theirs)[0] == data
            delta = 100.0 * (len(ours) - len(theirs)) / len(theirs)
            cells.append("%7d / %7d (%+.2f%%)" % (len(ours), len(theirs), delta))
        print("%-24s %9d  %s" % (label, len(data), "   ".join(cells)))
        t0 = time.perf_counter()
        inflate(zlib_raw(data, 9))
        inf = time.perf_counter() - t0
        print("%-24s %9s  compress %s s, inflate %.2f s (%.1f MB/s)" % (
            "", "", " / ".join("%.2f" % t for t in times), inf, len(data) / inf / 1e6))


if __name__ == "__main__":
    main()
