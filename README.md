# pure-python-deflate

`flate` is DEFLATE (RFC 1951) plus the zlib (RFC 1950) and gzip (RFC 1952)
wrappers, written from scratch in plain Python. No `zlib` import anywhere in
the package; the tests use the real `zlib` and `gzip` modules only as the
referee.

The goal was byte-level interop, not a toy: anything this writes, `zlib` /
`gunzip` can read, and anything `zlib` writes (every level, every window
size, every strategy, with sync/full flushes in the middle) this can read.

What's in it:

- **inflate** - stored, fixed and dynamic Huffman blocks. Canonical Huffman
  decoding goes through a 9-bit lookup table, so most symbols are one table
  hit; longer codes fall back to a puff-style walk over the code counts.
  Rejects the same things zlib rejects (over-subscribed / incomplete codes,
  distances past the start of output, symbols 286/287 and 30/31, bad
  LEN/NLEN), and the fuzz tests check it agrees with zlib on corrupted input.
- **lz77** - hash chains (head table + a 32K `prev` ring), greedy matching
  for levels 1-3, lazy matching for 4-9, with zlib's per-level tuning
  numbers (good/lazy/nice/chain). Drops 3-byte matches that are more than 4K
  back, since they cost more than the literals.
- **block encoder** - Huffman lengths via a heap, and if the tree comes out
  deeper than 15 bits (7 for the code-length code), package-merge for the
  optimal length-limited code instead. Code lengths are RLE'd with 16/17/18.
- **block splitting** - tokens are cut into 4096-token segments and greedily
  merged while one block costs no more than two. Every block then gets
  written as stored, fixed or dynamic, whichever is actually smallest in
  bits (including the stored block's byte-alignment padding at that point in
  the stream).
- **crc32 / adler32** - table-driven crc32; adler32 uses
  `itertools.accumulate` over 5552-byte chunks, which is a cute trick: the
  running sums of `a` are exactly what gets added into `b`.
- **containers** - zlib with header check + preset dictionaries, gzip with
  FEXTRA/FNAME/FCOMMENT/FHCRC, multi-member files, trailing zero padding.
- **cli** - `compress`, `decompress`, and `inspect`, which dumps the block
  structure of any gzip/zlib/raw stream.

## running it

Python 3.10+, nothing to install for the package itself. Tests need pytest.

```
python -m flate compress -l 9 somefile           # -> somefile.gz
python -m flate compress -F zlib -c < in > out.zz
python -m flate decompress somefile.gz
python -m flate inspect somefile.gz

pip install pytest
python -m pytest -q
python tools/bench.py [more files...]
```

Or `pip install -e .` and use the `flate` command. As a library:

```python
import flate
blob = flate.compress(data, level=9, fmt="gzip")   # or "zlib" / "raw"
assert flate.decompress(blob) == data              # format is sniffed
```

## example

Compressing the stdlib's `typing.py` at level 9 and looking inside:

```
$ python -m flate compress -l 9 typing.py
typing.py.gz: 132718 -> 33842 bytes (25.5%)
$ python -m flate inspect typing.py.gz
format: gzip, 33842 bytes
member 0: name='typing.py' mtime=1795485209 os=255 xfl=2, 132718 bytes out, crc ok
     #  type     final   in bytes  out bytes literals  matches avg len  table
     0  dynamic            6245.2      19663     1991     2105     8.4  hlit=279 hdist=29 hclen=13 hdr=560b maxlen=12
     1  dynamic    yes    27568.1     113055     4875     9777    11.1  hlit=286 hdist=30 hclen=17 hdr=593b maxlen=14

blocks: 2 dynamic
symbols: 6866 literals, 11882 matches (avg length 10.6), 94.8% of output came from matches
original 132718 bytes, compressed 33842 bytes: 2.04 bits/byte, ratio 0.255
```

The splitter decided the first ~19K of the file (imports, docstring, small
helpers) is different enough from the rest to deserve its own tables.
`gunzip -t` is happy with it.

## vs zlib

`tools/bench.py`, raw deflate sizes in bytes, ours / zlib at the same level:

```
file                          size  L1 ours / zlib (delta)   L6 ours / zlib (delta)   L9 ours / zlib (delta)
flate/*.py (this repo)       46466    16212 /   16455 (-1.48%)     13959 /   13989 (-0.21%)     13897 /   13927 (-0.22%)
                                    compress 0.02 / 0.06 / 0.26 s, inflate 0.01 s (4.2 MB/s)
stdlib typing.py            132718    41402 /   42256 (-2.02%)     34048 /   34221 (-0.51%)     33814 /   33978 (-0.48%)
                                    compress 0.05 / 0.18 / 0.83 s, inflate 0.03 s (3.8 MB/s)
_decimal .so (binary)       351112   164679 /  166851 (-1.30%)    151776 /  153248 (-0.96%)    151258 /  152572 (-0.86%)
                                    compress 0.30 / 0.71 / 2.52 s, inflate 0.13 s (2.8 MB/s)
64K random bytes             65536    65546 /   65556 (-0.02%)     65546 /   65556 (-0.02%)     65546 /   65556 (-0.02%)
```

So it comes out slightly *smaller* than zlib everywhere, which surprised me.
The match finding is basically zlib's, so the gain is all on the entropy
coding side: zlib flushes a block whenever its 16K-symbol buffer fills,
while this picks block boundaries by cost and builds optimal length-limited
codes. zlib does all that in a fixed memory budget, streaming, at hundreds
of MB/s; this holds the whole input in memory and runs at a few hundred
KB/s, so it's not exactly a fair fight. Speed numbers are from one run on
my box, treat them as rough.

## tests

734 tests, about 11 seconds:

- round trips at levels 0-9 on edge inputs: empty, 1-3 bytes, runs of
  259/1000/100K, period-2 and period-7 data, matches at exactly distance
  32768 (must be found) and 32769 (must not be)
- both directions against zlib: all levels, wbits 9-15 for raw/zlib/gzip,
  Z_FILTERED/Z_HUFFMAN_ONLY/Z_RLE/Z_FIXED, memLevel 1/4/9, sync/full/block
  flushes, preset dictionaries, multi-member gzip, hand-built gzip headers
  with every optional field
- seeded-random fuzzing (no hypothesis): random inputs both ways, plus
  bit-flipped and truncated streams where the decoder has to either fail
  with `DeflateError` or produce exactly what zlib produces
- crc32/adler32 vs zlib, including running checksums
- Huffman: RFC 1951's example codes, Fibonacci weights (worst case depth)
  for the length limit, package-merge checked against brute force
- hand-crafted corrupt streams for each error path, and the CLI end to end

## what I'd do next

- Streaming API (`compressobj` / `decompressobj` style) so big files don't
  have to sit in memory; the bit reader already tracks byte positions, it's
  mostly plumbing.
- A smarter parse: optimal/near-optimal matching using the previous block's
  code lengths as a cost model (what zopfli does) instead of zlib's lazy
  heuristic.
- Make inflate faster. A two-level table for long codes and decoding
  length+extra bits in one lookup would probably double it, but pure
  Python's ceiling is pretty low here.

## license

MIT
