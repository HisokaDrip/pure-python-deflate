import argparse
import os
import sys

from . import __version__
from .bitio import BitReader
from .containers import (detect_format, gzip_compress, gzip_members, zlib_compress,
                         zlib_decompress, _zlib_parse_header)
from .deflate import compress_raw
from .errors import DeflateError
from .inflate import inflate, inflate_reader

SUFFIX = {"gzip": ".gz", "zlib": ".zz", "raw": ".deflate"}


def _read_input(path):
    if path in (None, "-"):
        return sys.stdin.buffer.read()
    with open(path, "rb") as f:
        return f.read()


def _write_output(path, data, force):
    if path in (None, "-"):
        sys.stdout.buffer.write(data)
        return
    if os.path.exists(path) and not force:
        raise SystemExit("flate: %s exists, use -f to overwrite" % path)
    with open(path, "wb") as f:
        f.write(data)


def cmd_compress(args):
    data = _read_input(args.input)
    if args.format == "gzip":
        name = os.path.basename(args.input) if args.input not in (None, "-") else None
        out = gzip_compress(data, args.level, filename=name)
    elif args.format == "zlib":
        out = zlib_compress(data, args.level)
    else:
        out = compress_raw(data, args.level)
    dest = args.output
    if dest is None and args.input not in (None, "-") and not args.stdout:
        dest = args.input + SUFFIX[args.format]
    _write_output(None if args.stdout else dest, out, args.force)
    if dest and not args.stdout:
        print("%s: %d -> %d bytes (%.1f%%)" % (
            dest, len(data), len(out), 100.0 * len(out) / max(1, len(data))), file=sys.stderr)


def _decompress(data, fmt):
    if fmt == "auto":
        fmt = detect_format(data)
    if fmt == "gzip":
        return b"".join(p for _, p in gzip_members(data))
    if fmt == "zlib":
        return zlib_decompress(data)
    return inflate(data)[0]


def cmd_decompress(args):
    data = _read_input(args.input)
    out = _decompress(data, args.format)
    dest = args.output
    if dest is None and args.input not in (None, "-") and not args.stdout:
        for suffix in SUFFIX.values():
            if args.input.endswith(suffix):
                dest = args.input[:-len(suffix)]
                break
        else:
            raise SystemExit("flate: can't guess output name for %s, use -o or -c" % args.input)
    _write_output(None if args.stdout else dest, out, args.force)


def _print_blocks(trace, file=sys.stdout):
    print("  %4s  %-8s %5s %10s %10s %8s %8s %7s  %s" % (
        "#", "type", "final", "in bytes", "out bytes", "literals", "matches", "avg len", "table"),
        file=file)
    for i, b in enumerate(trace):
        lits = b.get("literals", "-")
        matches = b.get("matches", "-")
        avg = "%.1f" % (b["match_bytes"] / b["matches"]) if b.get("matches") else "-"
        table = ""
        if b["type"] == "dynamic":
            table = "hlit=%d hdist=%d hclen=%d hdr=%db maxlen=%d" % (
                b["hlit"], b["hdist"], b["hclen"], b["header_bits"], b["max_code_len"])
        print("  %4d  %-8s %5s %10.1f %10d %8s %8s %7s  %s" % (
            i, b["type"], "yes" if b["final"] else "", b["in_bits"] / 8, b["out_bytes"],
            lits, matches, avg, table), file=file)


def cmd_inspect(args):
    data = _read_input(args.input)
    fmt = args.format if args.format != "auto" else detect_format(data)
    trace = []
    print("format: %s, %d bytes" % (fmt, len(data)))
    if fmt == "gzip":
        out_len = 0
        for n, (info, payload) in enumerate(gzip_members(data, trace)):
            print("member %d: name=%r mtime=%d os=%d xfl=%d, %d bytes out, crc ok" % (
                n, info["name"], info["mtime"], info["os"], info["xfl"], len(payload)))
            out_len += len(payload)
    elif fmt == "zlib":
        header = _zlib_parse_header(data)
        print("zlib header: window=%d level-hint=%d dict=%s" % (
            header["window"], header["level"], header["fdict"]))
        out_len = len(zlib_decompress(data, trace=trace))
        print("adler32 ok")
    else:
        br = BitReader(data)
        out_len = len(inflate_reader(br, trace=trace))
        used = br.byte_pos()
        if used != len(data):
            print("note: %d trailing bytes after the deflate stream" % (len(data) - used))

    _print_blocks(trace)

    kinds = {}
    for b in trace:
        kinds[b["type"]] = kinds.get(b["type"], 0) + 1
    lits = sum(b.get("literals", 0) for b in trace)
    matches = sum(b.get("matches", 0) for b in trace)
    match_bytes = sum(b.get("match_bytes", 0) for b in trace)
    print()
    print("blocks: " + ", ".join("%d %s" % (v, k) for k, v in sorted(kinds.items())))
    if lits + matches:
        print("symbols: %d literals, %d matches (avg length %.1f), %.1f%% of output came from matches"
              % (lits, matches, match_bytes / max(1, matches), 100.0 * match_bytes / max(1, out_len)))
    ratio = len(data) / out_len if out_len else 0.0
    print("original %d bytes, compressed %d bytes: %.2f bits/byte, ratio %.3f" % (
        out_len, len(data), 8.0 * len(data) / max(1, out_len), ratio))


def build_parser():
    p = argparse.ArgumentParser(prog="flate", description="DEFLATE / zlib / gzip in pure Python")
    p.add_argument("--version", action="version", version="flate " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compress", help="compress a file")
    c.add_argument("input", nargs="?")
    c.add_argument("-l", "--level", type=int, default=6, choices=range(10), metavar="0-9")
    c.add_argument("-F", "--format", choices=("gzip", "zlib", "raw"), default="gzip")
    c.set_defaults(func=cmd_compress)

    d = sub.add_parser("decompress", help="decompress a file")
    d.add_argument("input", nargs="?")
    d.add_argument("-F", "--format", choices=("auto", "gzip", "zlib", "raw"), default="auto")
    d.set_defaults(func=cmd_decompress)

    for sp in (c, d):
        sp.add_argument("-o", "--output")
        sp.add_argument("-c", "--stdout", action="store_true", help="write to stdout")
        sp.add_argument("-f", "--force", action="store_true", help="overwrite output")

    i = sub.add_parser("inspect", help="dump the block structure of a stream")
    i.add_argument("input", nargs="?")
    i.add_argument("-F", "--format", choices=("auto", "gzip", "zlib", "raw"), default="auto")
    i.set_defaults(func=cmd_inspect)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except DeflateError as e:
        print("flate: corrupt input: %s" % e, file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print("flate: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
