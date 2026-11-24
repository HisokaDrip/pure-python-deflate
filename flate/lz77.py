# LZ77 match finder in the shape of zlib's deflate.c: a head table keyed by
# the next 3 bytes, a prev[] ring that chains older positions with the same
# key, and per-level knobs for how hard to search.
#
# Output is a flat list of ints. A literal is just the byte value (< 256); a
# match is (length << 16) | distance, which is always >= 3 << 16. Keeps the
# token list cheap compared to a list of tuples.

from .tables import MAX_MATCH, MIN_MATCH, WINDOW_SIZE

WINDOW_MASK = WINDOW_SIZE - 1

# (good_length, max_lazy, nice_length, max_chain), same numbers as zlib.
# Levels 1-3 are greedy and max_lazy doubles as "don't bother inserting the
# hashes of matches longer than this".
LEVELS = {
    1: (4, 4, 8, 4),
    2: (4, 5, 16, 8),
    3: (4, 6, 32, 32),
    4: (4, 4, 16, 16),
    5: (8, 16, 32, 32),
    6: (8, 16, 128, 128),
    7: (8, 32, 128, 256),
    8: (32, 128, 258, 1024),
    9: (32, 258, 258, 4096),
}

# a 3-byte match 4K+ back costs about as much as three literals once you
# pay for the distance extra bits, so zlib drops them. so do we.
TOO_FAR = 4096


def _match_length(data, a, b, limit):
    n = 0
    # compare in slices first; slicing + == runs in C, byte-at-a-time doesn't
    while n + 16 <= limit and data[a + n:a + n + 16] == data[b + n:b + n + 16]:
        n += 16
    while n < limit and data[a + n] == data[b + n]:
        n += 1
    return n


class Matcher:
    def __init__(self, data, max_chain, nice_length):
        self.data = data
        self.n = len(data)
        self.head = {}
        self.prev = [-1] * WINDOW_SIZE
        self.max_chain = max_chain
        self.nice = nice_length

    def key(self, i):
        d = self.data
        return (d[i] << 16) | (d[i + 1] << 8) | d[i + 2]

    def insert(self, i):
        k = self.key(i)
        self.prev[i & WINDOW_MASK] = self.head.get(k, -1)
        self.head[k] = i

    def longest(self, i, beat, chain):
        """Best match at i that is longer than `beat`. Returns (0, 0) if none.

        Must be called before insert(i): that way every candidate on the
        chain is < i and its prev[] slot hasn't been recycled yet."""
        data = self.data
        cand = self.head.get(self.key(i), -1)
        limit = min(MAX_MATCH, self.n - i)
        if beat >= limit:
            return 0, 0
        low = i - WINDOW_SIZE
        prev = self.prev
        nice = self.nice
        best_len, best_dist = beat, 0
        while cand >= low and cand >= 0 and chain > 0:
            # cheap reject: a better match must at least agree on the byte
            # just past the current best
            if data[cand + best_len] == data[i + best_len]:
                length = _match_length(data, cand, i, limit)
                if length > best_len:
                    best_len, best_dist = length, i - cand
                    if length >= nice or length >= limit:
                        break
            nxt = prev[cand & WINDOW_MASK]
            if nxt >= cand:
                break
            cand = nxt
            chain -= 1
        if best_dist == 0:
            return 0, 0
        return best_len, best_dist


def tokenize(data, level=6):
    if level not in LEVELS:
        raise ValueError("level must be 1..9 for lz77, got %r" % level)
    good, lazy, nice, chain = LEVELS[level]
    m = Matcher(data, chain, nice)
    if level <= 3:
        return _greedy(data, m, lazy)
    return _lazy(data, m, good, lazy)


def _greedy(data, m, max_insert):
    tokens = []
    n = len(data)
    i = 0
    last_hashable = n - MIN_MATCH   # last position with 3 bytes to hash
    while i < n:
        if i <= last_hashable:
            length, dist = m.longest(i, MIN_MATCH - 1, m.max_chain)
            m.insert(i)
            if length and not (length == MIN_MATCH and dist > TOO_FAR):
                tokens.append((length << 16) | dist)
                if length <= max_insert:
                    for j in range(i + 1, min(i + length, last_hashable + 1)):
                        m.insert(j)
                i += length
                continue
        tokens.append(data[i])
        i += 1
    return tokens


def _lazy(data, m, good, max_lazy):
    # Lazy evaluation: having found a match at i-1, peek at i. If i has a
    # longer match, emit data[i-1] as a literal and keep the new one instead.
    tokens = []
    n = len(data)
    last_hashable = n - MIN_MATCH
    i = 0
    prev_len = prev_dist = 0
    pending = False     # data[i-1] not yet emitted
    max_chain = m.max_chain
    while i < n:
        cur_len = cur_dist = 0
        if i <= last_hashable:
            if prev_len < max_lazy:
                chain = max_chain >> 2 if prev_len >= good else max_chain
                cur_len, cur_dist = m.longest(i, max(prev_len, MIN_MATCH - 1), chain)
                if cur_len == MIN_MATCH and cur_dist > TOO_FAR:
                    cur_len = 0
            m.insert(i)

        if prev_len >= MIN_MATCH and cur_len <= prev_len:
            tokens.append((prev_len << 16) | prev_dist)
            end = i - 1 + prev_len
            for j in range(i + 1, min(end, last_hashable + 1)):
                m.insert(j)
            i = end
            prev_len = prev_dist = 0
            pending = False
        else:
            if pending:
                tokens.append(data[i - 1])
            pending = True
            prev_len, prev_dist = cur_len, cur_dist
            i += 1
    if pending:
        tokens.append(data[n - 1])
    return tokens


def expand(tokens):
    """Turn tokens back into bytes. Only used by tests and debugging."""
    out = bytearray()
    for t in tokens:
        if t < 256:
            out.append(t)
        else:
            length, dist = t >> 16, t & 0xFFFF
            for _ in range(length):
                out.append(out[-dist])
    return bytes(out)
