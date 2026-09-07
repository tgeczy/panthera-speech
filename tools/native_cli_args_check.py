"""Data-free native CLI checks, safe to run in CI."""
import json
import subprocess
import sys

host = sys.argv[1]
for args in (["--help"], ["--render", "--help"]):
    result = subprocess.run([host] + args, capture_output=True, timeout=10)
    assert result.returncode == 0 and b"--output" in result.stdout
cases = [
    (["--render"], b"Hello", b"--tree"),
    (["--render", "--tree"], b"Hello", b"missing value"),
    (["--rate", "0"], b"Hello", b"invalid value"),
    (["--rate", "1201"], b"Hello", b"invalid value"),
    (["--rate", "200oops"], b"Hello", b"invalid value"),
    (["--pitch", "-121"], b"Hello", b"invalid value"),
    (["--volume", "101"], b"Hello", b"invalid value"),
    (["--numbers", "unknown"], b"Hello", b"invalid value"),
    (["--text", "Hello", "--input", "-"], b"", b"choose"),
    ([], b"a\0b", b"NUL"),
    ([], b"", b"text must contain"),
    ([], b"\xc0\x80", b"UTF-8"),
    ([], b"\xe2\x82", b"UTF-8"),
    ([], b"\xed\xa0\x80", b"UTF-8"),
    ([], b"\xf4\x90\x80\x80", b"UTF-8"),
]
for args, data, message in cases:
    if not args or args[0] != "--render":
        args = ["--render", "--tree", "unused-test-tree", "--output", "-"] + args
    result = subprocess.run([host] + args, input=data, capture_output=True, timeout=10)
    assert result.returncode != 0 and not result.stdout, (args, result.stdout)
    assert message in result.stderr, (args, result.stderr)
result = subprocess.run([host, "--capabilities"], capture_output=True, check=True, timeout=10)
capabilities = json.loads(result.stdout)
assert capabilities["render_cli"] and capabilities["number_flags"] == {"fix": 2, "words": 4}
print("Native CLI: help, 15 invalid-input cases, and capability metadata pass")
