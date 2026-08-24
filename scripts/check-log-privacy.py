#!/usr/bin/env python3
"""Reject log calls that look like they may include private user input.

This is a conservative static guard. It does not prove privacy, but it
catches the easy mistakes: interpolating or concatenating transcript
text, correction sources/replacements, whole correction arrays, or audio
buffers into Swift `log(...)` and Windows Python `_log(...)` calls. Raw
global keycodes are also forbidden because they can reveal typed characters.
Counts and other bounded metadata are allowed.

The whole argument expression of each `log(...)` call is scanned —
string-literal prose is stripped first so only code (interpolations,
concatenation operands, direct arguments, `String(format:)` arguments)
is checked for forbidden identifiers.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = [
    ROOT / "swift" / "Sources" / "Presspeech" / "main.swift",
    *sorted((ROOT / "windows").glob("*.py")),
]

LOG_CALL_RE = re.compile(r"(?<![A-Za-z0-9_.])log\s*\(")

FORBIDDEN_IDENTIFIER_RE = re.compile(
    r"""
    \b(
        text
      | transcript
      | rawTranscript
      | trimmed
      | cleaned
      | corrected
      | stripped
      | correction
      | corrections
      | replacement
      | source
      | sourceField
      | replacementField
      | body
      | history
      | keycode
      | keyCode
      | keyboardEventKeycode
      | s
    )\b
    """,
    re.VERBOSE,
)

# A member-access chain ending in a metadata-only suffix is safe even when
# it starts from a forbidden identifier (e.g. `cleaned.count`,
# `cleaned.utf8.count`, `mode.rawValue`). Matches are masked out before the
# forbidden-identifier scan, so each occurrence is judged individually.
SAFE_MEMBER_ACCESS_RE = re.compile(
    r"""
    \b[A-Za-z_][A-Za-z0-9_]*
    (?: \s* [?!]? \. \s* [A-Za-z_][A-Za-z0-9_]* )*
    \s* [?!]? \. \s*
    (
        count
      | isEmpty
      | appliedCount
      | removedCount
      | rawValue
    )
    \b
    """,
    re.VERBOSE,
)

PYTHON_PRIVATE_IDENTIFIERS = {
    "audio",
    "body",
    "cleaned",
    "corrected",
    "correction",
    "corrections",
    "dictionary",
    "history",
    "pcm",
    "raw_transcript",
    "raw_text",
    "replacement",
    "replacement_field",
    "samples",
    "source",
    "source_field",
    "spoken",
    "stripped",
    "text",
    "transcript",
    "trimmed",
}

# Reading these properties exposes only bounded metadata, not the private value.
PYTHON_SAFE_METADATA_ATTRIBUTES = {"count", "is_empty", "ndim", "shape", "size"}


class Finding(Exception):
    pass


def line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_log_calls(text: str) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []
    search_from = 0
    while True:
        match = LOG_CALL_RE.search(text, search_from)
        if match is None:
            return calls
        start = match.start()
        i = match.end()
        depth = 1
        in_string = False
        escaped = False
        while i < len(text):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        calls.append((start, text[start : i + 1]))
                        search_from = i + 1
                        break
            i += 1
        else:
            raise Finding(f"unterminated log call near line {line_number_for_offset(text, start)}")


def log_call_arguments(call: str) -> str:
    return call[call.index("(") + 1 : -1]


def code_only(expr: str) -> str:
    """Strip string-literal prose from a Swift expression, keeping code.

    Literal text is replaced with whitespace so identifiers in prose
    (e.g. log("history cleared")) are not scanned, while code embedded
    in interpolations (e.g. "\\(cleaned)") is kept and scanned, at any
    nesting depth.
    """
    out: list[str] = []
    # Stack of [kind, paren_depth] contexts; the base code context never pops.
    stack: list[list] = [["code", 0]]
    escaped = False
    i = 0
    while i < len(expr):
        ch = expr[i]
        if stack[-1][0] == "string":
            if escaped:
                escaped = False
            elif ch == "\\":
                if expr.startswith("\\(", i):
                    stack.append(["code", 1])
                    out.append(" (")
                    i += 2
                    continue
                escaped = True
            elif ch == '"':
                stack.pop()
                out.append(" ")
            i += 1
            continue
        if ch == '"':
            stack.append(["string", 0])
            out.append(" ")
        elif ch == "(":
            stack[-1][1] += 1
            out.append(ch)
        elif ch == ")":
            stack[-1][1] -= 1
            out.append(ch)
            if stack[-1][1] == 0 and len(stack) > 1:
                stack.pop()  # end of an interpolation; back inside the literal
                out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def forbidden_identifiers(code: str) -> list[str]:
    masked = SAFE_MEMBER_ACCESS_RE.sub(" ", code)
    return sorted({match.group(1) for match in FORBIDDEN_IDENTIFIER_RE.finditer(masked)})


def scan_text(path: Path, text: str) -> list[str]:
    findings: list[str] = []
    for offset, call in extract_log_calls(text):
        line = line_number_for_offset(text, offset)
        identifiers = forbidden_identifiers(code_only(log_call_arguments(call)))
        if identifiers:
            findings.append(
                f"{path}:{line}: suspicious log argument references {', '.join(identifiers)}"
            )
    return findings


def is_python_log_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id == "_log"
    return isinstance(node.func, ast.Attribute) and node.func.attr == "_log"


def python_private_identifiers(node: ast.AST) -> list[str]:
    identifiers: set[str] = set()

    def visit(current: ast.AST) -> None:
        # Length/count metadata is safe even when derived from transcript text,
        # correction collections, or audio buffers.
        if (isinstance(current, ast.Call)
                and isinstance(current.func, ast.Name)
                and current.func.id == "len"
                and len(current.args) == 1
                and not current.keywords):
            return
        if (isinstance(current, ast.Attribute)
                and current.attr in PYTHON_SAFE_METADATA_ATTRIBUTES):
            return

        if isinstance(current, ast.Name) and current.id in PYTHON_PRIVATE_IDENTIFIERS:
            identifiers.add(current.id)
        elif (isinstance(current, ast.Attribute)
              and current.attr in PYTHON_PRIVATE_IDENTIFIERS):
            identifiers.add(current.attr)
        elif isinstance(current, ast.Subscript):
            index = current.slice
            if (isinstance(index, ast.Constant)
                    and isinstance(index.value, str)
                    and index.value in PYTHON_PRIVATE_IDENTIFIERS):
                identifiers.add(index.value)

        for child in ast.iter_child_nodes(current):
            visit(child)

    visit(node)
    return sorted(identifiers)


def scan_python_text(path: Path, text: str) -> list[str]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno or 1}: could not parse Python log calls"]

    findings: list[str] = []
    log_calls = sorted(
        (
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and is_python_log_call(node)
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in log_calls:
        identifiers: set[str] = set()
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            identifiers.update(python_private_identifiers(argument))
        if identifiers:
            findings.append(
                f"{path}:{node.lineno}: suspicious log argument references "
                f"{', '.join(sorted(identifiers))}"
            )
    return findings


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            findings.extend(scan_python_text(path, text))
        else:
            findings.extend(scan_text(path, text))
    return findings


def run_self_test() -> None:
    clean = """
    log("release: \\(String(format: "%.2f", dur)) s -> \\(cleaned.count) chars")
    log("corrections: \\(settings.transcriptCorrections.count) configured")
    log("history copied to clipboard (\\(s.count) chars)")
    log("recent transcript history trimmed by \\(removed) entr\\(removed == 1 ? "y" : "ies")")
    log("trigger mode -> " + mode.rawValue)
    log("request body empty: \\(payload.isEmpty)")
    """
    dirty = """
    log("transcript: \\(cleaned)")
    log("correction: \\(correction.replacement)")
    log("inserted: \\(String(format: "%@", cleaned))")
    log("inserting: " + cleaned)
    log(transcript)
    log("request body: \\(body)")
    log("history: \\(history.joined(separator: ", "))")
    log("first key: \\(event.keycode)")
    """
    non_log_calls = """
    catalog("transcript: \\(cleaned)")
    logger.log("transcript: \\(cleaned)")
    """
    python_clean = """
self._log("transcription complete: %d chars" % len(text))
self._log("audio samples: %d" % audio.size)
PresspeechApp._log("dictionary rules: %d" % len(self.settings["dictionary"]))
self._log("backend: %s" % backend)
"""
    python_dirty = """
self._log(text)
self._log("transcript: %s" % transcript)
self._log(f"corrected: {corrected}")
self._log("dictionary: %s" % self.settings["dictionary"])
self._log(audio)
self._log(text.upper())
"""
    with tempfile.TemporaryDirectory() as tmp:
        clean_path = Path(tmp) / "clean.swift"
        dirty_path = Path(tmp) / "dirty.swift"
        non_log_path = Path(tmp) / "non-log.swift"
        python_clean_path = Path(tmp) / "clean.py"
        python_dirty_path = Path(tmp) / "dirty.py"
        clean_path.write_text(clean, encoding="utf-8")
        dirty_path.write_text(dirty, encoding="utf-8")
        non_log_path.write_text(non_log_calls, encoding="utf-8")
        python_clean_path.write_text(python_clean, encoding="utf-8")
        python_dirty_path.write_text(python_dirty, encoding="utf-8")
        findings = scan_paths([clean_path])
        if findings:
            raise SystemExit(f"self-test rejected clean log calls: {findings}")
        if scan_paths([non_log_path]):
            raise SystemExit("self-test treated non-log calls as log calls")
        findings = scan_paths([dirty_path])
        if len(findings) != 8:
            raise SystemExit(f"self-test expected 8 dirty findings, got {len(findings)}: {findings}")
        for needle, label in [
            (":4:", "String(format:) argument bypass"),
            (":5:", "string concatenation bypass"),
            (":6:", "direct argument bypass"),
            (":7:", "forbidden identifier 'body'"),
            (":8:", "forbidden identifier 'history'"),
            (":9:", "raw global keycode"),
        ]:
            if not any(needle in finding for finding in findings):
                raise SystemExit(f"self-test did not catch {label}")

        findings = scan_paths([python_clean_path])
        if findings:
            raise SystemExit(f"self-test rejected clean Python log calls: {findings}")
        findings = scan_paths([python_dirty_path])
        if len(findings) != 6:
            raise SystemExit(
                f"self-test expected 6 dirty Python findings, got {len(findings)}: {findings}"
            )
        for identifier in ("audio", "corrected", "dictionary", "text", "transcript"):
            if not any(identifier in finding for finding in findings):
                raise SystemExit(
                    f"self-test did not catch Python private identifier {identifier!r}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        print("log privacy self-test passed")
        return 0

    paths = args.paths or DEFAULT_PATHS
    findings = scan_paths(paths)
    if findings:
        print("log privacy check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("log privacy check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
