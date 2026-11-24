# DEFLATE packs bits LSB-first into bytes. Huffman codes are the odd one out:
# they go in MSB-first, so both sides store codes bit-reversed and then treat
# everything as plain LSB-first integers.

from .errors import DeflateError


class BitReader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos          # next byte to pull into the buffer
        self.bitbuf = 0
        self.bitcnt = 0

    def _fill(self, n):
        data, pos = self.data, self.pos
        while self.bitcnt < n:
            if pos >= len(data):
                self.pos = pos
                return False
            self.bitbuf |= data[pos] << self.bitcnt
            pos += 1
            self.bitcnt += 8
        self.pos = pos
        return True

    def bits(self, n):
        if n == 0:
            return 0
        if self.bitcnt < n and not self._fill(n):
            raise DeflateError("unexpected end of stream")
        v = self.bitbuf & ((1 << n) - 1)
        self.bitbuf >>= n
        self.bitcnt -= n
        return v

    def peek(self, n):
        # may return fewer real bits than asked near the end of input; the
        # caller checks the code length against bitcnt before consuming
        if self.bitcnt < n:
            self._fill(n)
        return self.bitbuf & ((1 << n) - 1)

    def drop(self, n):
        if n > self.bitcnt:
            raise DeflateError("unexpected end of stream")
        self.bitbuf >>= n
        self.bitcnt -= n

    def align(self):
        self.drop(self.bitcnt & 7)

    def _give_back(self):
        # after align() the buffer only holds whole bytes we read ahead
        self.pos -= self.bitcnt >> 3
        self.bitbuf = 0
        self.bitcnt = 0

    def read_bytes(self, n):
        self.align()
        self._give_back()
        if self.pos + n > len(self.data):
            raise DeflateError("unexpected end of stream in stored block")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def byte_pos(self):
        """Offset of the first byte not belonging to the deflate stream."""
        self.align()
        self._give_back()
        return self.pos


class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.bitbuf = 0
        self.bitcnt = 0

    def write(self, value, n):
        self.bitbuf |= value << self.bitcnt
        self.bitcnt += n
        if self.bitcnt >= 48:
            nbytes = self.bitcnt >> 3
            self.out += (self.bitbuf & ((1 << (nbytes * 8)) - 1)).to_bytes(nbytes, "little")
            self.bitbuf >>= nbytes * 8
            self.bitcnt -= nbytes * 8

    def flush_to_byte(self):
        nbytes = (self.bitcnt + 7) >> 3
        if nbytes:
            self.out += self.bitbuf.to_bytes(nbytes, "little")
        self.bitbuf = 0
        self.bitcnt = 0

    def write_bytes(self, data):
        # only valid on a byte boundary
        assert self.bitcnt == 0
        self.out += data

    @property
    def bit_length(self):
        return len(self.out) * 8 + self.bitcnt

    def getvalue(self):
        self.flush_to_byte()
        return bytes(self.out)
