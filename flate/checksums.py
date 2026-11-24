from itertools import accumulate

_CRC_TABLE = []
for n in range(256):
    c = n
    for _ in range(8):
        c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
    _CRC_TABLE.append(c)


def crc32(data, crc=0):
    table = _CRC_TABLE
    crc ^= 0xFFFFFFFF
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


ADLER_MOD = 65521
# zlib's NMAX: the largest chunk where b can't overflow 32 bits. Python ints
# don't overflow, but keeping chunks bounded keeps the temp list small.
_NMAX = 5552


def adler32(data, value=1):
    a = value & 0xFFFF
    b = value >> 16
    mv = memoryview(data)
    for start in range(0, len(mv), _NMAX):
        # running sums of a are exactly the values added into b
        sums = list(accumulate(mv[start:start + _NMAX], initial=a))
        a = sums[-1] % ADLER_MOD
        b = (b + sum(sums) - sums[0]) % ADLER_MOD
    return (b << 16) | a
