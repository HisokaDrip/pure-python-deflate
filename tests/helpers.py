import random


def sample_inputs(seed=1234):
    """A grab bag of inputs that tend to break deflate implementations."""
    rng = random.Random(seed)
    text = b"the quick brown fox jumps over the lazy dog. " * 50
    window_edge = bytes(rng.getrandbits(8) for _ in range(300))
    return {
        "empty": b"",
        "one_byte": b"\x00",
        "two_bytes": b"ab",
        "three_same": b"zzz",
        "all_zero_100k": bytes(100_000),
        "single_run_259": b"q" * 259,          # one byte longer than MAX_MATCH
        "run_1000": b"x" * 1000,
        "period_2": b"ab" * 5000,
        "period_7": b"abcdefg" * 3000,
        "text": text,
        "random_small": bytes(rng.getrandbits(8) for _ in range(1000)),
        "random_80k": bytes(rng.getrandbits(8) for _ in range(80_000)),
        "all_bytes": bytes(range(256)) * 4,
        "low_entropy": bytes(rng.choice(b"ACGT") for _ in range(20_000)),
        # same chunk repeated exactly WINDOW_SIZE apart: distance 32768 is the
        # farthest legal match, 32769 is just out of reach
        "window_32768": window_edge + bytes(rng.getrandbits(8) for _ in range(32768 - 300)) + window_edge,
        "window_32769": window_edge + bytes(rng.getrandbits(8) for _ in range(32769 - 300)) + window_edge,
        "mixed": text + bytes(rng.getrandbits(8) for _ in range(5000)) + text,
    }
