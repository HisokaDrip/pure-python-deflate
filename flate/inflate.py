from .bitio import BitReader
from .errors import DeflateError
from .huffman import HuffmanDecoder
from .tables import (CL_ORDER, DIST_BASE, DIST_EXTRA, END_OF_BLOCK,
                     FIXED_DIST_LENGTHS, FIXED_LIT_LENGTHS, LEN_BASE, LEN_EXTRA)

BLOCK_NAMES = {0: "stored", 1: "fixed", 2: "dynamic"}

_fixed = None


def _fixed_decoders():
    global _fixed
    if _fixed is None:
        # 288 lit/len lengths and 30 distance codes. 286/287 and 30/31 exist
        # in the fixed code but are invalid to actually use.
        _fixed = (HuffmanDecoder(FIXED_LIT_LENGTHS),
                  HuffmanDecoder(FIXED_DIST_LENGTHS + [5, 5]))
    return _fixed


def _read_dynamic_tables(br, info=None):
    start = br.pos * 8 - br.bitcnt
    hlit = br.bits(5) + 257
    hdist = br.bits(5) + 1
    hclen = br.bits(4) + 4
    if hlit > 286 or hdist > 30:
        raise DeflateError("too many length or distance symbols")

    cl_lengths = [0] * 19
    for i in range(hclen):
        cl_lengths[CL_ORDER[i]] = br.bits(3)
    cl_dec = HuffmanDecoder(cl_lengths, allow_incomplete=False)
    if cl_dec.empty:
        raise DeflateError("empty code length code")

    lengths = []
    total = hlit + hdist
    while len(lengths) < total:
        sym = cl_dec.decode(br)
        if sym < 16:
            lengths.append(sym)
            continue
        if sym == 16:
            if not lengths:
                raise DeflateError("repeat with no previous length")
            value, count = lengths[-1], 3 + br.bits(2)
        elif sym == 17:
            value, count = 0, 3 + br.bits(3)
        else:
            value, count = 0, 11 + br.bits(7)
        if len(lengths) + count > total:
            raise DeflateError("code length repeat runs past the end")
        lengths.extend([value] * count)

    lit_lengths, dist_lengths = lengths[:hlit], lengths[hlit:]
    if lit_lengths[END_OF_BLOCK] == 0:
        raise DeflateError("no end-of-block code")
    if info is not None:
        info.update(hlit=hlit, hdist=hdist, hclen=hclen,
                    header_bits=br.pos * 8 - br.bitcnt - start,
                    max_code_len=max(lit_lengths + dist_lengths))
    return HuffmanDecoder(lit_lengths), HuffmanDecoder(dist_lengths)


def _inflate_huffman(br, out, lit_dec, dist_dec, stats):
    decode_lit = lit_dec.decode
    decode_dist = dist_dec.decode
    literals = matches = match_bytes = 0
    while True:
        sym = decode_lit(br)
        if sym < 256:
            out.append(sym)
            literals += 1
            continue
        if sym == END_OF_BLOCK:
            break
        sym -= 257
        if sym >= 29:
            raise DeflateError("invalid length symbol %d" % (sym + 257))
        length = LEN_BASE[sym] + br.bits(LEN_EXTRA[sym])

        dsym = decode_dist(br)
        if dsym >= 30:
            raise DeflateError("invalid distance symbol %d" % dsym)
        dist = DIST_BASE[dsym] + br.bits(DIST_EXTRA[dsym])
        if dist > len(out):
            raise DeflateError("distance %d too far back" % dist)

        start = len(out) - dist
        if dist >= length:
            out += out[start:start + length]
        else:
            # overlapping copy: the match repeats its own first `dist` bytes
            chunk = out[start:]
            reps, rem = divmod(length, dist)
            out += chunk * reps + chunk[:rem]
        matches += 1
        match_bytes += length
    if stats is not None:
        stats.update(literals=literals, matches=matches, match_bytes=match_bytes)


def inflate_reader(br, out=None, trace=None):
    """Inflate one raw DEFLATE stream from a BitReader into `out`.

    If `trace` is a list, a dict describing each block gets appended to it
    (that's what `flate inspect` prints)."""
    if out is None:
        out = bytearray()
    while True:
        start_bit = br.pos * 8 - br.bitcnt
        start_out = len(out)
        final = br.bits(1)
        btype = br.bits(2)
        info = {"type": BLOCK_NAMES.get(btype), "final": bool(final)} if trace is not None else None

        if btype == 0:
            br.align()
            header = br.read_bytes(4)
            length = header[0] | header[1] << 8
            nlength = header[2] | header[3] << 8
            if length != (~nlength & 0xFFFF):
                raise DeflateError("stored block length check failed")
            out += br.read_bytes(length)
        elif btype == 1:
            lit_dec, dist_dec = _fixed_decoders()
            _inflate_huffman(br, out, lit_dec, dist_dec, info)
        elif btype == 2:
            lit_dec, dist_dec = _read_dynamic_tables(br, info)
            _inflate_huffman(br, out, lit_dec, dist_dec, info)
        else:
            raise DeflateError("invalid block type 3")

        if info is not None:
            end_bit = br.pos * 8 - br.bitcnt
            info["in_bits"] = end_bit - start_bit
            info["out_bytes"] = len(out) - start_out
            trace.append(info)
        if final:
            return out


def inflate(data, trace=None):
    """Raw DEFLATE -> bytes. Returns (output, bytes_consumed)."""
    br = BitReader(data)
    out = inflate_reader(br, trace=trace)
    return bytes(out), br.byte_pos()


def decompress_raw(data):
    return inflate(data)[0]
