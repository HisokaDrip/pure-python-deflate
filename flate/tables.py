# Constant tables straight out of RFC 1951 section 3.2.5 / 3.2.6.

LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
            35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
LEN_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
             3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]

DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
             257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
             8193, 12289, 16385, 24577]
DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
              7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]

# order in which code-length code lengths are sent
CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]

FIXED_LIT_LENGTHS = [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8
FIXED_DIST_LENGTHS = [5] * 30

MAX_BITS = 15
MAX_CL_BITS = 7
WINDOW_SIZE = 32768
MIN_MATCH = 3
MAX_MATCH = 258
END_OF_BLOCK = 256


def _build_len_lookup():
    # length (3..258) -> (symbol, extra bit count, extra value)
    table = [None] * (MAX_MATCH + 1)
    for code, (base, extra) in enumerate(zip(LEN_BASE, LEN_EXTRA)):
        for length in range(base, base + (1 << extra)):
            if length <= MAX_MATCH and table[length] is None:
                table[length] = (257 + code, extra, length - base)
    # 258 has its own code (285) even though 284 + 31 would also reach it;
    # zlib never emits that form and some decoders reject it
    table[258] = (285, 0, 0)
    return table


def _build_dist_lookup():
    table = [None] * (WINDOW_SIZE + 1)
    for code, (base, extra) in enumerate(zip(DIST_BASE, DIST_EXTRA)):
        for dist in range(base, min(base + (1 << extra), WINDOW_SIZE + 1)):
            table[dist] = (code, extra, dist - base)
    return table


LEN_LOOKUP = _build_len_lookup()
DIST_LOOKUP = _build_dist_lookup()
