import heapq

from .errors import DeflateError
from .tables import MAX_BITS

FAST_BITS = 9   # same primary table width zlib uses for literal/length codes


def reverse_bits(code, n):
    r = 0
    for _ in range(n):
        r = (r << 1) | (code & 1)
        code >>= 1
    return r


def canonical_codes(lengths):
    """Canonical code per symbol (RFC 1951 3.2.2), already bit-reversed so it
    can be written LSB-first."""
    max_len = max(lengths, default=0)
    bl_count = [0] * (max_len + 1)
    for n in lengths:
        if n:
            bl_count[n] += 1
    next_code = [0] * (max_len + 1)
    code = 0
    for bits in range(1, max_len + 1):
        code = (code + bl_count[bits - 1]) << 1
        next_code[bits] = code
    codes = [0] * len(lengths)
    for sym, n in enumerate(lengths):
        if n:
            codes[sym] = reverse_bits(next_code[n], n)
            next_code[n] += 1
    return codes


class HuffmanDecoder:
    """Canonical Huffman decoder: a 2^FAST_BITS lookup table indexed by the
    next (bit-reversed) input bits handles every short code in one step.
    Anything longer falls back to walking the counts, puff.c style."""

    def __init__(self, lengths, allow_incomplete=True):
        counts = [0] * (MAX_BITS + 1)
        for n in lengths:
            if n < 0 or n > MAX_BITS:
                raise DeflateError("bad code length %d" % n)
            counts[n] += 1
        counts[0] = 0

        left = 1
        for n in range(1, MAX_BITS + 1):
            left = (left << 1) - counts[n]
            if left < 0:
                raise DeflateError("over-subscribed huffman code")
        used = sum(counts)
        # zlib accepts an incomplete code only in the degenerate case of a
        # single 1-bit code (one distance code, say). Same rule here.
        if left > 0 and used and not (allow_incomplete and used == 1 and counts[1] == 1):
            raise DeflateError("incomplete huffman code")

        self.counts = counts
        offs = [0] * (MAX_BITS + 2)
        for n in range(1, MAX_BITS + 1):
            offs[n + 1] = offs[n] + counts[n]
        symbols = [0] * used
        for sym, n in enumerate(lengths):
            if n:
                symbols[offs[n]] = sym
                offs[n] += 1
        self.symbols = symbols

        # -1 means "not resolvable from FAST_BITS bits", go the slow way
        fast = [-1] * (1 << FAST_BITS)
        codes = canonical_codes(lengths)
        for sym, n in enumerate(lengths):
            if 0 < n <= FAST_BITS:
                entry = (sym << 4) | n
                for idx in range(codes[sym], 1 << FAST_BITS, 1 << n):
                    fast[idx] = entry
        self.fast = fast
        self.empty = used == 0

    def decode(self, br):
        if br.bitcnt < FAST_BITS:
            br.peek(FAST_BITS)
        entry = self.fast[br.bitbuf & ((1 << FAST_BITS) - 1)]
        if entry >= 0:
            n = entry & 15
            if n > br.bitcnt:
                raise DeflateError("unexpected end of stream")
            br.bitbuf >>= n
            br.bitcnt -= n
            return entry >> 4
        return self._slow_decode(br)

    def _slow_decode(self, br):
        code = first = index = 0
        counts = self.counts
        for n in range(1, MAX_BITS + 1):
            code |= br.bits(1)
            count = counts[n]
            if code - count < first:
                return self.symbols[index + code - first]
            index += count
            first = (first + count) << 1
            code <<= 1
        raise DeflateError("invalid huffman code")


# ---------------------------------------------------------------------------
# encoder side: picking code lengths


def huffman_lengths(freqs):
    """Plain (unlimited) Huffman code lengths via a heap."""
    nodes = [(f, sym) for sym, f in enumerate(freqs) if f]
    lengths = [0] * len(freqs)
    if not nodes:
        return lengths
    if len(nodes) == 1:
        lengths[nodes[0][1]] = 1
        return lengths

    parent = {}
    heap = [(f, sym, ("leaf", sym)) for f, sym in nodes]
    heapq.heapify(heap)
    next_id = len(freqs)
    while len(heap) > 1:
        f1, _, a = heapq.heappop(heap)
        f2, _, b = heapq.heappop(heap)
        node = ("node", next_id)
        parent[a] = node
        parent[b] = node
        # tie-break on id so equal weights merge oldest-first, which keeps
        # the tree a bit shallower
        heapq.heappush(heap, (f1 + f2, next_id, node))
        next_id += 1

    depth_cache = {heap[0][2]: 0}

    def depth(node):
        chain = []
        while node not in depth_cache:
            chain.append(node)
            node = parent[node]
        d = depth_cache[node]
        for nd in reversed(chain):
            d += 1
            depth_cache[nd] = d
        return d

    for _, sym in nodes:
        lengths[sym] = depth(("leaf", sym))
    return lengths


def package_merge(freqs, max_bits):
    """Optimal length-limited code lengths (Larmore & Hirschberg).

    Each item carries a symbol multiset; a symbol's code length is how many
    times it shows up in the cheapest 2n-2 items of the final list."""
    leaves = sorted((f, (sym,)) for sym, f in enumerate(freqs) if f)
    n = len(leaves)
    lengths = [0] * len(freqs)
    if n == 0:
        return lengths
    if n == 1:
        lengths[leaves[0][1][0]] = 1
        return lengths
    if (1 << max_bits) < n:
        raise ValueError("%d symbols can't fit in %d bits" % (n, max_bits))

    current = leaves
    for _ in range(max_bits - 1):
        packages = [(current[i][0] + current[i + 1][0], current[i][1] + current[i + 1][1])
                    for i in range(0, len(current) - 1, 2)]
        current = list(heapq.merge(leaves, packages, key=lambda item: item[0]))
    for _, syms in current[:2 * n - 2]:
        for sym in syms:
            lengths[sym] += 1
    return lengths


def limited_lengths(freqs, max_bits):
    # Huffman is cheap and almost always fits, so only pay for package-merge
    # when the plain tree is too deep.
    lengths = huffman_lengths(freqs)
    if max(lengths, default=0) > max_bits:
        lengths = package_merge(freqs, max_bits)
    return lengths
