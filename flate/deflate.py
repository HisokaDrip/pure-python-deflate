from .bitio import BitWriter
from .huffman import canonical_codes, limited_lengths
from .lz77 import tokenize
from .tables import (CL_ORDER, DIST_LOOKUP, END_OF_BLOCK, FIXED_DIST_LENGTHS,
                     FIXED_LIT_LENGTHS, LEN_LOOKUP, MAX_BITS, MAX_CL_BITS)

MAX_STORED = 65535
# tokens per segment for the block splitter; blocks are built out of these
SEGMENT = 4096
# don't let a single block grow forever, the stats drift on big inputs
MAX_BLOCK_TOKENS = 1 << 17

_FIXED_LIT_CODES = canonical_codes(FIXED_LIT_LENGTHS)
_FIXED_DIST_CODES = canonical_codes(FIXED_DIST_LENGTHS)

_CL_EXTRA = {16: 2, 17: 3, 18: 7}


class Segment:
    """A run of tokens plus their symbol histogram and the input bytes they cover."""

    __slots__ = ("lo", "hi", "byte_lo", "byte_hi", "lit", "dist", "extra")

    def __init__(self, tokens, lo, hi, byte_lo):
        lit = [0] * 286
        dist = [0] * 30
        extra = 0
        nbytes = 0
        for t in tokens[lo:hi]:
            if t < 256:
                lit[t] += 1
                nbytes += 1
            else:
                length = t >> 16
                ls, le, _ = LEN_LOOKUP[length]
                ds, de, _ = DIST_LOOKUP[t & 0xFFFF]
                lit[ls] += 1
                dist[ds] += 1
                extra += le + de
                nbytes += length
        self.lo, self.hi = lo, hi
        self.byte_lo, self.byte_hi = byte_lo, byte_lo + nbytes
        self.lit, self.dist, self.extra = lit, dist, extra

    def merged(self, other):
        seg = Segment.__new__(Segment)
        seg.lo, seg.hi = self.lo, other.hi
        seg.byte_lo, seg.byte_hi = self.byte_lo, other.byte_hi
        seg.lit = [a + b for a, b in zip(self.lit, other.lit)]
        seg.dist = [a + b for a, b in zip(self.dist, other.dist)]
        seg.extra = self.extra + other.extra
        return seg


def _rle_lengths(lengths):
    """Code length sequence -> list of (symbol, extra_value) using 16/17/18."""
    out = []
    i, n = 0, len(lengths)
    while i < n:
        v = lengths[i]
        run = 1
        while i + run < n and lengths[i + run] == v:
            run += 1
        i += run
        if v == 0:
            while run >= 11:
                k = min(run, 138)
                out.append((18, k - 11))
                run -= k
            if run >= 3:
                out.append((17, run - 3))
                run = 0
            out.extend([(0, 0)] * run)
        else:
            out.append((v, 0))
            run -= 1
            while run >= 3:
                k = min(run, 6)
                out.append((16, k - 3))
                run -= k
            out.extend([(v, 0)] * run)
    return out


def _complete(lengths):
    # A code with a single symbol gets length 1 and leaves the other 1-bit
    # code unused. zlib's inflate tolerates that for lit/dist but not for the
    # code length code, so just always hand out a second 1-bit code.
    used = [i for i, n in enumerate(lengths) if n]
    if len(used) == 0:
        lengths[0] = lengths[1] = 1
    elif len(used) == 1:
        lengths[1 if used[0] == 0 else 0] = 1
    return lengths


class DynamicPlan:
    def __init__(self, lit_freq, dist_freq):
        lit_freq = list(lit_freq)
        lit_freq[END_OF_BLOCK] = 1
        self.lit_lengths = _complete(limited_lengths(lit_freq, MAX_BITS))
        self.dist_lengths = _complete(limited_lengths(dist_freq, MAX_BITS))

        hlit = 286
        while hlit > 257 and self.lit_lengths[hlit - 1] == 0:
            hlit -= 1
        hdist = 30
        while hdist > 1 and self.dist_lengths[hdist - 1] == 0:
            hdist -= 1
        self.hlit, self.hdist = hlit, hdist

        self.rle = _rle_lengths(self.lit_lengths[:hlit] + self.dist_lengths[:hdist])
        cl_freq = [0] * 19
        for sym, _ in self.rle:
            cl_freq[sym] += 1
        self.cl_lengths = _complete(limited_lengths(cl_freq, MAX_CL_BITS))
        hclen = 19
        while hclen > 4 and self.cl_lengths[CL_ORDER[hclen - 1]] == 0:
            hclen -= 1
        self.hclen = hclen

        self.header_bits = 14 + 3 * hclen + sum(
            self.cl_lengths[sym] + _CL_EXTRA.get(sym, 0) for sym, _ in self.rle)
        self.body_bits = (sum(f * n for f, n in zip(lit_freq, self.lit_lengths))
                          + sum(f * n for f, n in zip(dist_freq, self.dist_lengths)))

    def cost(self, extra):
        return 3 + self.header_bits + self.body_bits + extra

    def write_header(self, bw):
        bw.write(self.hlit - 257, 5)
        bw.write(self.hdist - 1, 5)
        bw.write(self.hclen - 4, 4)
        for i in range(self.hclen):
            bw.write(self.cl_lengths[CL_ORDER[i]], 3)
        codes = canonical_codes(self.cl_lengths)
        for sym, extra in self.rle:
            bw.write(codes[sym], self.cl_lengths[sym])
            if sym in _CL_EXTRA:
                bw.write(extra, _CL_EXTRA[sym])


def fixed_cost(seg):
    bits = 3 + FIXED_LIT_LENGTHS[END_OF_BLOCK] + seg.extra
    bits += sum(f * n for f, n in zip(seg.lit, FIXED_LIT_LENGTHS))
    return bits + 5 * sum(seg.dist)


def stored_cost(nbytes, bit_pos):
    # each stored block: 3 header bits, pad to a byte, LEN/NLEN, raw bytes
    chunks = max(1, -(-nbytes // MAX_STORED))
    first_pad = -(bit_pos + 3) % 8
    return chunks * (3 + 32) + first_pad + (chunks - 1) * 5 + 8 * nbytes


def _estimate(seg):
    return min(DynamicPlan(seg.lit, seg.dist).cost(seg.extra), fixed_cost(seg),
               stored_cost(seg.byte_hi - seg.byte_lo, 0))


def split_blocks(tokens):
    """Greedy block splitting: walk fixed-size segments and keep extending the
    current block while coding it as one is no worse than coding the pieces
    separately. A change in the data (text -> binary, say) makes the merged
    Huffman tables fit both halves badly and starts a new block."""
    segs = []
    byte_pos = 0
    for lo in range(0, len(tokens), SEGMENT):
        seg = Segment(tokens, lo, min(lo + SEGMENT, len(tokens)), byte_pos)
        byte_pos = seg.byte_hi
        segs.append(seg)
    if not segs:
        return [Segment(tokens, 0, 0, 0)]

    blocks = []
    cur = segs[0]
    cur_cost = _estimate(cur)
    for seg in segs[1:]:
        merged = cur.merged(seg)
        merged_cost = _estimate(merged)
        too_big = merged.hi - merged.lo > MAX_BLOCK_TOKENS
        if not too_big and merged_cost <= cur_cost + _estimate(seg):
            cur, cur_cost = merged, merged_cost
        else:
            blocks.append(cur)
            cur, cur_cost = seg, _estimate(seg)
    blocks.append(cur)
    return blocks


def _write_tokens(bw, tokens, lo, hi, lit_lengths, dist_lengths):
    lit_codes = canonical_codes(lit_lengths)
    dist_codes = canonical_codes(dist_lengths)
    # length symbol + its extra bits glued into one write
    len_combo = [None] * 259
    for length in range(3, 259):
        sym, ebits, evalue = LEN_LOOKUP[length]
        n = lit_lengths[sym]
        len_combo[length] = (lit_codes[sym] | (evalue << n), n + ebits)
    dist_lookup = DIST_LOOKUP
    write = bw.write
    for t in tokens[lo:hi]:
        if t < 256:
            write(lit_codes[t], lit_lengths[t])
        else:
            code, n = len_combo[t >> 16]
            write(code, n)
            ds, ebits, evalue = dist_lookup[t & 0xFFFF]
            n = dist_lengths[ds]
            write(dist_codes[ds] | (evalue << n), n + ebits)
    write(lit_codes[END_OF_BLOCK], lit_lengths[END_OF_BLOCK])


def _write_stored(bw, chunk, final):
    pos = 0
    while True:
        piece = chunk[pos:pos + MAX_STORED]
        pos += len(piece)
        last = pos >= len(chunk)
        bw.write(1 if (final and last) else 0, 1)
        bw.write(0, 2)
        bw.flush_to_byte()
        n = len(piece)
        bw.write_bytes(bytes((n & 0xFF, n >> 8, ~n & 0xFF, (~n >> 8) & 0xFF)))
        bw.write_bytes(piece)
        if last:
            return


def _write_block(bw, data, tokens, block, final, force=None):
    nbytes = block.byte_hi - block.byte_lo
    plan = DynamicPlan(block.lit, block.dist)
    costs = {
        "dynamic": plan.cost(block.extra),
        "fixed": fixed_cost(block),
        "stored": stored_cost(nbytes, bw.bitcnt),
    }
    kind = force or min(costs, key=costs.get)

    if kind == "stored":
        _write_stored(bw, data[block.byte_lo:block.byte_hi], final)
        return kind
    bw.write(1 if final else 0, 1)
    if kind == "fixed":
        bw.write(1, 2)
        _write_tokens(bw, tokens, block.lo, block.hi, FIXED_LIT_LENGTHS, FIXED_DIST_LENGTHS)
    else:
        bw.write(2, 2)
        plan.write_header(bw)
        _write_tokens(bw, tokens, block.lo, block.hi, plan.lit_lengths, plan.dist_lengths)
    return kind


def compress_raw(data, level=6, force=None, report=None):
    """Raw DEFLATE (no header/trailer).

    `force` pins every block to "stored", "fixed" or "dynamic" (handy for
    tests). If `report` is a list, the chosen block types are appended."""
    data = bytes(data)
    if not 0 <= level <= 9:
        raise ValueError("level must be 0..9")
    bw = BitWriter()
    if level == 0 or force == "stored":
        _write_stored(bw, data, final=True)
        if report is not None:
            report.append("stored")
        return bw.getvalue()

    tokens = tokenize(data, level)
    blocks = split_blocks(tokens)
    for i, block in enumerate(blocks):
        kind = _write_block(bw, data, tokens, block, i == len(blocks) - 1, force)
        if report is not None:
            report.append(kind)
    return bw.getvalue()
