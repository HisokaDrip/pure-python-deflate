import gzip
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "flate", *args], cwd=ROOT, input=stdin,
                          capture_output=True, check=False)


def test_compress_decompress_files(tmp_path):
    src = tmp_path / "words.txt"
    payload = b"".join(b"line %d of some text\n" % i for i in range(3000))
    src.write_bytes(payload)
    assert run("compress", "-l", "9", str(src)).returncode == 0
    gz = tmp_path / "words.txt.gz"
    assert gzip.decompress(gz.read_bytes()) == payload
    src.unlink()
    assert run("decompress", str(gz)).returncode == 0
    assert src.read_bytes() == payload


def test_stdin_stdout_zlib():
    payload = b"pipe me " * 500
    r = run("compress", "-F", "zlib", "-c", stdin=payload)
    assert zlib.decompress(r.stdout) == payload
    back = run("decompress", "-c", stdin=r.stdout)
    assert back.stdout == payload


def test_inspect_reports_blocks(tmp_path):
    path = tmp_path / "x.gz"
    path.write_bytes(gzip.compress(b"abc" * 10000 + bytes(range(256)) * 20))
    r = run("inspect", str(path))
    out = r.stdout.decode()
    assert r.returncode == 0
    assert "dynamic" in out
    assert "crc ok" in out
    assert "ratio" in out


def test_corrupt_input_exit_code(tmp_path):
    path = tmp_path / "bad.gz"
    data = bytearray(gzip.compress(b"hello world" * 100))
    data[-8] ^= 0xFF
    path.write_bytes(bytes(data))
    r = run("decompress", "-c", str(path))
    assert r.returncode == 1
    assert b"corrupt input" in r.stderr
