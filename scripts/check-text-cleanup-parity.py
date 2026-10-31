#!/usr/bin/env python3
"""Check that macOS and Windows share the same English-only cleanup lists.

Both apps decide whether "um" and "er" may be removed from the same filler
patterns, marker words and punctuation sets. Each sits between
`text-cleanup-parity: begin <name>` and `... end <name>` comments in
`swift/Sources/Presspeech/main.swift` and `windows/text_cleanup.py`. This
check compares the string literals in each block, in order and after
resolving escapes, and rejects duplicate markers and words listed as both
English and non-English.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "swift" / "Sources" / "Presspeech" / "main.swift"
PYTHON = ROOT / "windows" / "text_cleanup.py"
PYTHON_TESTS = ROOT / "windows" / "tests" / "test_text_cleanup.py"
BLOCKS = ("neutral-fillers", "english-only-fillers", "punctuation",
          "english-markers", "non-english-markers")
MARKER_BLOCKS = ("english-markers", "non-english-markers")
# Shared regression inputs: the Swift self-tests and the Windows unit tests
# must exercise the same dictation.
TEST_BLOCKS = ("non-english-dictation", "preserved-with-english-declared",
               "english-dictation")
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "'": "'", "\\": "\\"}


def _literals(line: str) -> list[str]:
    """String literals on one source line, escapes resolved, comments skipped.

    Handles the escapes both files use: Python \\uXXXX, Swift \\u{X...}, and
    \\n \\r \\t \\" \\' \\\\. A `#` or `//` outside a string starts a comment.
    """
    out, index = [], 0
    while index < len(line):
        char = line[index]
        if char == "#" or line.startswith("//", index):
            break
        if char != '"':
            index += 1
            continue
        index += 1
        value = []
        while index < len(line) and line[index] != '"':
            if line[index] == "\\" and index + 1 < len(line):
                code = line[index + 1]
                if code == "u" and line.startswith("{", index + 2):
                    close = line.index("}", index + 3)
                    value.append(chr(int(line[index + 3:close], 16)))
                    index = close + 1
                    continue
                if code == "u":
                    value.append(chr(int(line[index + 2:index + 6], 16)))
                    index += 6
                    continue
                value.append(_SIMPLE_ESCAPES.get(code, code))
                index += 2
                continue
            value.append(line[index])
            index += 1
        out.append("".join(value))
        index += 1
    return out


def block_words(source: str, name: str) -> list[str] | None:
    match = re.search(
        r"text-cleanup-parity: begin %s\n(.*?)text-cleanup-parity: end %s\b"
        % (re.escape(name), re.escape(name)), source, re.S)
    if match is None:
        return None
    words = []
    for line in match.group(1).splitlines():
        words.extend(_literals(line))
    return words


def compare(swift_source: str, python_source: str,
            blocks: tuple[str, ...] = BLOCKS) -> list[str]:
    errors = []
    lists = {}
    for name in blocks:
        swift_words = block_words(swift_source, name)
        python_words = block_words(python_source, name)
        if swift_words is None or python_words is None:
            errors.append(f"{name}: block missing in "
                          f"{'Swift' if swift_words is None else 'Python'}")
            continue
        if swift_words != python_words:
            only_swift = sorted(set(swift_words) - set(python_words))
            only_python = sorted(set(python_words) - set(swift_words))
            errors.append(f"{name}: lists differ (Swift only {only_swift}, "
                          f"Python only {only_python}, or order differs)")
        duplicates = sorted({w for w in python_words if python_words.count(w) > 1})
        if name in MARKER_BLOCKS and duplicates:
            errors.append(f"{name}: duplicate entries {duplicates}")
        lists[name] = set(python_words)
    if "english-markers" in lists and "non-english-markers" in lists:
        overlap = sorted(lists["english-markers"] & lists["non-english-markers"])
        if overlap:
            errors.append(f"marker words listed as both English and not: {overlap}")
    return errors


def self_test() -> None:
    swift = '''
    // text-cleanup-parity: begin neutral-fillers
    static let a = ["uh+", "hm+"]
    // text-cleanup-parity: end neutral-fillers
    // text-cleanup-parity: begin english-only-fillers
    static let b = ["um"]
    // text-cleanup-parity: end english-only-fillers
    // text-cleanup-parity: begin punctuation
    // After an "um"/"er", one of these.
    static let delimiters = ",.\\u{2026}"
    static let breaks = ".\\n"
    // text-cleanup-parity: end punctuation
    // text-cleanup-parity: begin english-markers
    static let c: Set<String> = [
        "the", "and",  // "ignored" in a comment
    ]
    // text-cleanup-parity: end english-markers
    // text-cleanup-parity: begin non-english-markers
    static let d: Set<String> = [
        // German "nicht"
        "und", "ist",
    ]
    // text-cleanup-parity: end non-english-markers
    '''
    python = '''
# text-cleanup-parity: begin neutral-fillers
A = ("uh+", "hm+")
# text-cleanup-parity: end neutral-fillers
# text-cleanup-parity: begin english-only-fillers
B = ("um",)
# text-cleanup-parity: end english-only-fillers
# text-cleanup-parity: begin punctuation
# After an "um"/"er", one of these.
DELIMITERS = ",.\\u2026"
BREAKS = ".\\n"
# text-cleanup-parity: end punctuation
# text-cleanup-parity: begin english-markers
C = frozenset((
    "the", "and",  # "ignored"
))
# text-cleanup-parity: end english-markers
# text-cleanup-parity: begin non-english-markers
D = frozenset((
    # German
    "und", "ist",
))
# text-cleanup-parity: end non-english-markers
'''
    assert compare(swift, python) == [], compare(swift, python)
    assert compare(swift.replace('"ist"', '"oder"'), python)
    assert compare(swift, python.replace('"ist",', '"ist", "und",'))
    assert compare(swift.replace('"und"', '"the"'),
                   python.replace('"und"', '"the"'))
    assert compare(swift.replace("begin english-markers", "begin other"), python)
    # Punctuation sets must match too, after escapes are resolved.
    assert compare(swift.replace('"."', '"!"').replace('".', '"!'), python)
    assert compare(swift, python.replace('",.', '",;'))
    swift_tests = 'x\n// text-cleanup-parity: begin english-dictation\n[("a", "b")]\n// text-cleanup-parity: end english-dictation\n'
    python_tests = '# text-cleanup-parity: begin english-dictation\nD = {\n    "a": "b",\n}\n# text-cleanup-parity: end english-dictation\n'
    assert compare(swift_tests, python_tests, ("english-dictation",)) == []
    assert compare(swift_tests.replace('"b"', '"c"'), python_tests, ("english-dictation",))
    print("check-text-cleanup-parity self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    swift = SWIFT.read_text(encoding="utf-8")
    errors = compare(swift, PYTHON.read_text(encoding="utf-8"))
    errors += compare(swift, PYTHON_TESTS.read_text(encoding="utf-8"), TEST_BLOCKS)
    for error in errors:
        print(f"text cleanup parity: {error}", file=sys.stderr)
    if errors:
        return 1
    print("text cleanup parity: macOS and Windows lists and test inputs match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
