class DeflateError(ValueError):
    """Raised for anything malformed: bad block types, broken Huffman tables,
    distances pointing before the start of output, checksum mismatches..."""
