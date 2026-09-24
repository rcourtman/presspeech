#!/usr/bin/env python3
"""Reject logs and Windows notifications that may include private user input.

This is a conservative static guard. It does not prove privacy, but it
catches the easy mistakes: interpolating or concatenating transcript
text, correction sources/replacements, whole correction arrays, or audio
buffers into Swift `log(...)` and Windows Python `_log(...)`/`notify(...)`
calls. Exact microphone/device names, selectors, and focused executable names are also
private because they can contain personal or workplace labels. Raw global
keycodes are forbidden because they can reveal typed characters.
Swift error objects and localized descriptions are forbidden because NSError
domains/userInfo and upstream errors can contain private paths or input. Only
the reviewed privacySafeErrorLogDetail(error) category wrapper is allowed.
Python exception bindings are treated the same way regardless of the name
chosen after `except ... as`; exception class names remain safe categories.
Counts and other bounded metadata are allowed. Paths and benchmark-session
labels are private too: a sanitized filename can still identify its user.

Windows notifications can persist in Notification Center and are checked with
the same Python rules as logs. The reviewed updater error wrapper is allowed.
The Python check follows simple local aliases into sinks, including aliases
assigned on conditional, loop, and exception paths, and checks literal-key
`get`/`pop`/`setdefault` and `getattr` reads of private fields. It does not
prove that arbitrary helper calls or containers are free of private data.

The whole argument expression of each Swift `log(...)` call is scanned —
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
      | error
      | err
      | exception
      | errorDescription
      | localizedDescription
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

# A deliberately narrow exception to the error-object ban: this Swift helper
# renders only the static error type and numeric code. Do not mask arbitrary
# wrappers, where a future formatter could print localizedDescription.
SAFE_ERROR_DETAIL_RE = re.compile(
    r"\bprivacySafeErrorLogDetail\s*\(\s*(?:error|err|exception)\s*\)"
)
RAW_LOG_DESCRIPTION_RE = re.compile(r"(?<!\.)\blogDescription\b")

PYTHON_PRIVATE_IDENTIFIERS = {
    "audio",
    "body",
    "cleaned",
    "corrected",
    "correction",
    "corrections",
    "dictionary",
    "device_label",
    "device_name",
    "device_uid",
    "err",
    "error",
    "exception",
    "exc",
    "filename",
    "history",
    "input_device",
    "input_device_name",
    "input_device_uid",
    "label",
    "mic_label",
    "mic_name",
    "microphone_label",
    "microphone_name",
    "microphone_uid",
    "name",
    "pcm",
    "process_name",
    "raw_transcript",
    "raw_text",
    "replacement",
    "replacement_field",
    "samples",
    "safe_session",
    "session",
    "source",
    "source_field",
    "spoken",
    "stripped",
    "selector",
    "text",
    "transcript",
    "trimmed",
    "uid",
    "_undelivered_dictations",
}

# Reading these properties exposes only bounded metadata, not the private value.
PYTHON_SAFE_METADATA_ATTRIBUTES = {"count", "is_empty", "ndim", "shape", "size"}
PYTHON_EXCEPTION_IDENTIFIERS = {"exc", "exception", "error", "err"}


def python_private_name(name: str) -> bool:
    """Recognize private values, including new local path variable names."""
    return (
        name in PYTHON_PRIVATE_IDENTIFIERS
        or name == "path"
        or name.endswith(("_path", "_dir", "_directory", "_filename", "_session"))
    )


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
    masked = SAFE_ERROR_DETAIL_RE.sub(" ", masked)
    found = {match.group(1) for match in FORBIDDEN_IDENTIFIER_RE.finditer(masked)}
    if RAW_LOG_DESCRIPTION_RE.search(masked):
        found.add("logDescription")
    return sorted(found)


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


def is_python_private_sink_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in {"_log", "notify"}
    return (isinstance(node.func, ast.Attribute)
            and node.func.attr in {"_log", "notify"})


def contains_raw_exception_details(
    node: ast.AST, exception_names: frozenset[str] = frozenset()
) -> bool:
    """Reject common unbounded exception renderings in persistent output."""
    exception_identifiers = PYTHON_EXCEPTION_IDENTIFIERS | exception_names
    for current in ast.walk(node):
        if (isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute)
                and current.func.attr in {"format_exc", "format_exception",
                                          "format_exception_only"}):
            return True
        if (isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute)
                and isinstance(current.func.value, ast.Name)
                and current.func.value.id == "sys"
                and current.func.attr in {"exception", "exc_info"}):
            return True
        if (isinstance(current, ast.Call) and isinstance(current.func, ast.Name)
                and current.func.id == "str" and current.args and
                isinstance(current.args[0], ast.Name) and
                current.args[0].id in exception_identifiers):
            return True
    return False


def python_private_identifiers(
    node: ast.AST, exception_names: frozenset[str] = frozenset(),
    private_aliases: frozenset[str] = frozenset(),
) -> list[str]:
    identifiers: set[str] = set()
    exception_identifiers = PYTHON_EXCEPTION_IDENTIFIERS | exception_names

    def visit(current: ast.AST) -> None:
        # A private predicate chooses between values but does not put the
        # predicate itself into the rendered message. Inspect both possible
        # outputs instead of tainting a fixed-label status by its condition.
        if isinstance(current, ast.IfExp):
            visit(current.body)
            visit(current.orelse)
            return

        # The updater maps arbitrary exceptions to a fixed fallback and only
        # returns deliberately authored UpdateError messages. Keep this one
        # reviewed UI wrapper available without allowing generic formatters.
        if (isinstance(current, ast.Call)
                and isinstance(current.func, ast.Attribute)
                and isinstance(current.func.value, ast.Name)
                and current.func.value.id == "updates"
                and current.func.attr == "user_facing_error"
                and len(current.args) == 2
                and isinstance(current.args[0], ast.Name)
                and current.args[0].id in exception_identifiers
                and isinstance(current.args[1], ast.Constant)
                and isinstance(current.args[1].value, str)
                and not current.keywords):
            return

        # Exception class names are bounded diagnostic categories; the
        # exception object itself can contain machine-specific paths or
        # upstream/private content and must never be interpolated into logs.
        if (isinstance(current, ast.Attribute) and current.attr == "__name__"
                and isinstance(current.value, ast.Call)
                and isinstance(current.value.func, ast.Name)
                and current.value.func.id == "type"
                and len(current.value.args) == 1
                and isinstance(current.value.args[0], ast.Name)
                and not current.value.keywords):
            return

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

        # A keyed lookup can expose the same private value as `mapping["name"]`.
        # In particular, `settings.get("dictionary")` used to bypass the
        # subscript check, including when assigned to a harmless-looking alias
        # before a notification. `getattr(obj, "name")` is the attribute form.
        if isinstance(current, ast.Call):
            key = None
            if (isinstance(current.func, ast.Attribute)
                    and current.func.attr in {"get", "pop", "setdefault"}
                    and current.args):
                key = current.args[0]
            elif (isinstance(current.func, ast.Name)
                    and current.func.id == "getattr"
                    and len(current.args) >= 2):
                key = current.args[1]
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and python_private_name(key.value)):
                identifiers.add(key.value)

        if (isinstance(current, ast.Name)
                and (current.id in exception_names or
                     current.id in private_aliases or
                     python_private_name(current.id))):
            identifiers.add(current.id)
        elif (isinstance(current, ast.Attribute)
              and python_private_name(current.attr)
              and not (current.attr == "path" and
                       isinstance(current.value, ast.Name) and
                       current.value.id == "os")):
            identifiers.add(current.attr)
        elif isinstance(current, ast.Subscript):
            index = current.slice
            if (isinstance(index, ast.Constant)
                    and isinstance(index.value, str)
                    and python_private_name(index.value)):
                identifiers.add(index.value)

        for child in ast.iter_child_nodes(current):
            visit(child)

    visit(node)
    return sorted(identifiers)


class PythonPrivateSinkCallCollector(ast.NodeVisitor):
    """Track local private aliases as well as exception bindings at each sink.

    `except ... as e` exposes the same unbounded error details as `exc`; a
    name-based denylist cannot cover it. Simple assignments can similarly hide
    a transcript or error behind a harmless-looking `message` variable. Keep
    binding state local to a scope and in source order; merge both sides of a
    conditional because either can reach a subsequent log. This is still a
    conservative guard, not whole-program information-flow analysis.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[ast.Call, frozenset[str], frozenset[str]]] = []
        self.exception_names: frozenset[str] = frozenset()
        self.private_aliases: frozenset[str] = frozenset()
        self._try_body_history: list[set[str]] = []

    def _bind(self, target: ast.AST, private: bool) -> None:
        if isinstance(target, ast.Name):
            aliases = set(self.private_aliases)
            if private:
                aliases.add(target.id)
                # A try handler can run before a later clean reassignment.
                for history in self._try_body_history:
                    history.add(target.id)
            else:
                aliases.discard(target.id)
            self.private_aliases = frozenset(aliases)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind(element, private)

    def _is_private(self, expression: ast.AST) -> bool:
        return bool(python_private_identifiers(
            expression, self.exception_names, self.private_aliases)
            or contains_raw_exception_details(expression, self.exception_names))

    def _visit_block(self, statements: list[ast.stmt],
                     aliases: frozenset[str]) -> frozenset[str]:
        self.private_aliases = aliases
        for statement in statements:
            self.visit(statement)
        return self.private_aliases

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        private = self._is_private(node.value)
        for target in node.targets:
            self._bind(target, private)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind(node.target, self._is_private(node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._bind(node.target, self._is_private(node.target)
                   or self._is_private(node.value))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind(node.target, self._is_private(node.value))

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        before = self.private_aliases
        from_body = self._visit_block(node.body, before)
        from_else = self._visit_block(node.orelse, before)
        self.private_aliases = from_body | from_else

    def visit_Try(self, node: ast.Try) -> None:
        before = self.private_aliases
        body_history: set[str] = set()
        self._try_body_history.append(body_history)
        try:
            after_body = self._visit_block(node.body, before)
        finally:
            self._try_body_history.pop()
        outcomes = self._visit_block(node.orelse, after_body)
        for handler in node.handlers:
            # A handler can run after any statement in the try body. Inspect
            # both prior and body bindings rather than letting a later sibling
            # handler's clean assignment erase an earlier private one.
            self.private_aliases = before | after_body | body_history
            self.visit(handler)
            outcomes |= self.private_aliases
        self.private_aliases = self._visit_block(node.finalbody, outcomes)

    visit_TryStar = visit_Try

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        before = self.private_aliases
        self._bind(node.target, self._is_private(node.iter))
        after_body = self._visit_block(node.body, self.private_aliases)
        # The iterable might be empty. The else suite can run after either
        # zero or more iterations, including a break-free private iteration.
        self.private_aliases = self._visit_block(node.orelse, before | after_body)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        before = self.private_aliases
        after_body = self._visit_block(node.body, before)
        self.private_aliases = self._visit_block(node.orelse, before | after_body)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous_aliases = self.private_aliases
        previous_exceptions = self.exception_names
        previous_try_history = self._try_body_history
        try:
            # A closure can read outer aliases, but assignments in one
            # function must never taint an unrelated sibling function.
            arguments = (*node.args.posonlyargs, *node.args.args,
                         *node.args.kwonlyargs)
            shadowed = {argument.arg for argument in arguments}
            if node.args.vararg:
                shadowed.add(node.args.vararg.arg)
            if node.args.kwarg:
                shadowed.add(node.args.kwarg.arg)
            self.private_aliases -= shadowed
            # Defining a nested function does not execute its assignments in
            # the enclosing try body.
            self._try_body_history = []
            for statement in node.body:
                self.visit(statement)
        finally:
            self.private_aliases = previous_aliases
            self.exception_names = previous_exceptions
            self._try_body_history = previous_try_history

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        previous = self.exception_names
        if node.name:
            self.exception_names = previous | {node.name}
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.exception_names = previous

    def visit_Call(self, node: ast.Call) -> None:
        if is_python_private_sink_call(node):
            self.calls.append((node, self.exception_names, self.private_aliases))
        self.generic_visit(node)


def scan_python_text(path: Path, text: str) -> list[str]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno or 1}: could not parse Python privacy sinks"]

    findings: list[str] = []
    collector = PythonPrivateSinkCallCollector()
    collector.visit(tree)
    for node, exception_names, private_aliases in sorted(
        collector.calls, key=lambda item: (item[0].lineno, item[0].col_offset)
    ):
        identifiers: set[str] = set()
        raw_exception_details = False
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            identifiers.update(python_private_identifiers(
                argument, exception_names, private_aliases))
            raw_exception_details |= contains_raw_exception_details(argument, exception_names)
        if identifiers or raw_exception_details:
            if raw_exception_details:
                identifiers.add("raw exception details")
            findings.append(
                f"{path}:{node.lineno}: suspicious log/notification argument "
                "references "
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
    log("sync failed: \\(privacySafeErrorLogDetail(error))")
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
    log("failed: \\(error)")
    log("failed: \\(error.localizedDescription)")
    log("failed: \\(String(describing: error))")
    log("failed: \\(error.map { privacySafeErrorLogDetail($0) } ?? "unknown")")
    log("failed: \\(errorDescription)")
    log("failed: \\(logDescription)")
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
self._log("operation failed: %s" % type(exc).__name__)
self._log("static filename: %s" % os.path.basename("public-fixture.txt"))
"""
    python_dirty = """
self._log(text)
self._log("transcript: %s" % transcript)
self._log(f"corrected: {corrected}")
self._log("dictionary: %s" % self.settings["dictionary"])
self._log(audio)
self._log(text.upper())
self._log(traceback.format_exc())
self._log("failed: %s" % str(exc))
self._log("failed: %s" % traceback.format_exception(*sys.exc_info()))
self._log("microphone: %s" % device["name"])
self._log("configured input: %s" % selector)
self._log("input device ID: %s" % input_device_uid)
self._log("failed: %s" % exc)
self._log(f"failed: {error}")
self._log("failed: %s" % err)
self._log("failed: %s" % exception)
self._log("paste target: %s" % paste_target.process_name)
self._log("saved benchmark: %s" % output_path)
self._log("basename: %s" % os.path.basename(output_path))
self._log("session: %s" % safe_session)
self._log("session setting: %s" % self.settings["capture_benchmark_session"])
self._log("directory: %s" % capture_dir)
self._log("file: %s" % filename)
"""
    python_exception_alias_clean = """
try:
    work()
except Exception as e:
    self._log("failure category: %s" % type(e).__name__)
e = "safe status after the handler"
self._log(e)
try:
    work()
except* ValueError as fault:
    self._log(type(fault).__name__)
"""
    python_exception_alias_dirty = """
try:
    work()
except Exception as e:
    self._log(f"failed: {e}")
    self._log(str(e))
    self._log(e.args)
    try:
        work()
    except RuntimeError as cause:
        self._log(cause)
        self._log(type(e).__name__)
try:
    work()
except* ValueError as fault:
    self._log(f"failed: {fault}")
self._log(sys.exception())
self._log(sys.exc_info())
self._log(traceback.format_exception_only(ValueError("private path")))
"""
    python_notification_clean = """
self.notify("Model", "Loading local model.")
notify("Benchmark", "%d clips remaining" % len(audio))
self.notify("Update failed", updates.user_facing_error(exc, "Could not check updates."))
"""
    python_notification_dirty = """
try:
    work()
except RuntimeError as fault:
    self.notify("Model failed", str(fault))
    notify("Model failed", f"{fault}")
self.notify("Clip saved", os.path.basename(output_path))
self.notify("Clip saved", filename)
self.notify("Update failed", updates.user_facing_error(updates.UpdateError(text), "fallback"))
"""
    python_alias_dirty = """
def report(text):
    message = f"dictated: {text}"
    self._log(message)
    notice = message
    self.notify("Delivery", notice)
    if failed:
        detail = text
    else:
        detail = "safe status"
    self._log(detail)
    self._log(self._undelivered_dictations[0])
    try:
        work()
    except OSError as failure:
        detail = str(failure)
        self.notify("Failure", detail)
"""
    python_alias_clean = """
def report(text):
    message = "dictation length: %d" % len(text)
    self._log(message)
    message = text
    message = "fixed status"
    self.notify("Delivery", message)
    self._log(type(message).__name__)
def unrelated():
    self._log(message)
"""
    python_control_dirty = """
def report(text):
    for piece in text:
        self._log(piece)
    try:
        detail = "fixed status"
    except OSError:
        detail = text
    except ValueError:
        detail = "other fixed status"
    self.notify("Failure", detail)
    try:
        message = text
        work()
        message = "fixed status"
    except OSError:
        self._log(message)
"""
    python_control_clean = """
def outer(text):
    message = text
    def inner(message):
        self._log(message)
    try:
        status = "fixed status"
    except OSError:
        status = "other fixed status"
    self._log(status)
    for number in range(len(text)):
        self._log(number)
    message = "fixed status"
    try:
        def inner():
            message = text
        work()
    except OSError:
        self._log(message)
"""
    python_keyed_clean = """
self._log("model: %s" % settings.get("model"))
self._log("dictionary rules: %d" % len(settings.get("dictionary", [])))
self.notify("Status", getattr(info, "status", "unknown"))
"""
    python_keyed_dirty = """
self.notify("Support", settings.get("dictionary"))
message = settings.get("dictionary", [])
self._log(message)
self._log(device.get("name"))
self.notify("Target", getattr(paste_target, "process_name", ""))
self._log(request.pop("session"))
self.notify("History", options.setdefault("history", []))
"""
    with tempfile.TemporaryDirectory() as tmp:
        clean_path = Path(tmp) / "clean.swift"
        dirty_path = Path(tmp) / "dirty.swift"
        non_log_path = Path(tmp) / "non-log.swift"
        python_clean_path = Path(tmp) / "clean.py"
        python_dirty_path = Path(tmp) / "dirty.py"
        python_exception_alias_clean_path = Path(tmp) / "exception-alias-clean.py"
        python_exception_alias_dirty_path = Path(tmp) / "exception-alias-dirty.py"
        python_notification_clean_path = Path(tmp) / "notification-clean.py"
        python_notification_dirty_path = Path(tmp) / "notification-dirty.py"
        python_alias_clean_path = Path(tmp) / "alias-clean.py"
        python_alias_dirty_path = Path(tmp) / "alias-dirty.py"
        python_control_clean_path = Path(tmp) / "control-clean.py"
        python_control_dirty_path = Path(tmp) / "control-dirty.py"
        python_keyed_clean_path = Path(tmp) / "keyed-clean.py"
        python_keyed_dirty_path = Path(tmp) / "keyed-dirty.py"
        clean_path.write_text(clean, encoding="utf-8")
        dirty_path.write_text(dirty, encoding="utf-8")
        non_log_path.write_text(non_log_calls, encoding="utf-8")
        python_clean_path.write_text(python_clean, encoding="utf-8")
        python_dirty_path.write_text(python_dirty, encoding="utf-8")
        python_exception_alias_clean_path.write_text(
            python_exception_alias_clean, encoding="utf-8")
        python_exception_alias_dirty_path.write_text(
            python_exception_alias_dirty, encoding="utf-8")
        python_notification_clean_path.write_text(
            python_notification_clean, encoding="utf-8")
        python_notification_dirty_path.write_text(
            python_notification_dirty, encoding="utf-8")
        python_alias_clean_path.write_text(python_alias_clean, encoding="utf-8")
        python_alias_dirty_path.write_text(python_alias_dirty, encoding="utf-8")
        python_control_clean_path.write_text(python_control_clean, encoding="utf-8")
        python_control_dirty_path.write_text(python_control_dirty, encoding="utf-8")
        python_keyed_clean_path.write_text(python_keyed_clean, encoding="utf-8")
        python_keyed_dirty_path.write_text(python_keyed_dirty, encoding="utf-8")
        findings = scan_paths([clean_path])
        if findings:
            raise SystemExit(f"self-test rejected clean log calls: {findings}")
        if scan_paths([non_log_path]):
            raise SystemExit("self-test treated non-log calls as log calls")
        findings = scan_paths([dirty_path])
        if len(findings) != 14:
            raise SystemExit(f"self-test expected 14 dirty findings, got {len(findings)}: {findings}")
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
        if len(findings) != 23:
            raise SystemExit(
                f"self-test expected 23 dirty Python findings, got {len(findings)}: {findings}"
            )
        for identifier in (
            "audio", "corrected", "dictionary", "text", "transcript",
            "name", "selector", "input_device_uid",
            "process_name", "output_path", "safe_session", "capture_benchmark_session",
            "capture_dir", "filename",
            "exc", "error", "err", "exception",
        ):
            if not any(identifier in finding for finding in findings):
                raise SystemExit(
                    f"self-test did not catch Python private identifier {identifier!r}"
                )
        findings = scan_paths([python_exception_alias_clean_path])
        if findings:
            raise SystemExit(f"self-test rejected safe exception-category logs: {findings}")
        findings = scan_paths([python_exception_alias_dirty_path])
        if len(findings) != 8:
            raise SystemExit(
                f"self-test expected 8 alias/exception findings, got {len(findings)}: {findings}"
            )
        for identifier in ("e", "cause", "fault", "raw exception details"):
            if not any(identifier in finding for finding in findings):
                raise SystemExit(
                    f"self-test did not catch exception alias/detail {identifier!r}"
                )
        findings = scan_paths([python_notification_clean_path])
        if findings:
            raise SystemExit(f"self-test rejected safe notifications: {findings}")
        findings = scan_paths([python_notification_dirty_path])
        if len(findings) != 5:
            raise SystemExit(
                f"self-test expected 5 unsafe notifications, got {len(findings)}: {findings}"
            )
        for identifier in (
                "fault", "raw exception details", "output_path", "filename", "text"):
            if not any(identifier in finding for finding in findings):
                raise SystemExit(
                    f"self-test did not catch notification leak {identifier!r}"
                )
        findings = scan_paths([python_alias_clean_path])
        if findings:
            raise SystemExit(f"self-test rejected safe local aliases: {findings}")
        findings = scan_paths([python_alias_dirty_path])
        if len(findings) != 5:
            raise SystemExit(
                f"self-test expected 5 unsafe alias sinks, got {len(findings)}: {findings}"
            )
        for identifier in ("message", "notice", "detail", "_undelivered_dictations"):
            if not any(identifier in finding for finding in findings):
                raise SystemExit(
                    f"self-test did not catch private alias {identifier!r}"
                )
        findings = scan_paths([python_control_clean_path])
        if findings:
            raise SystemExit(f"self-test rejected safe control-flow aliases: {findings}")
        findings = scan_paths([python_control_dirty_path])
        if (len(findings) != 3 or not any("piece" in finding for finding in findings)
                or not any("message" in finding for finding in findings)):
            raise SystemExit(
                f"self-test did not catch loop/handler aliases: {findings}"
            )
        findings = scan_paths([python_keyed_clean_path])
        if findings:
            raise SystemExit(f"self-test rejected safe keyed lookups: {findings}")
        findings = scan_paths([python_keyed_dirty_path])
        if len(findings) != 6:
            raise SystemExit(
                f"self-test expected 6 unsafe keyed lookups, got {len(findings)}: {findings}"
            )
        for identifier in (
                "dictionary", "message", "name", "process_name", "session", "history"):
            if not any(identifier in finding for finding in findings):
                raise SystemExit(
                    f"self-test did not catch keyed private value {identifier!r}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        print("log/notification privacy self-test passed")
        return 0

    paths = args.paths or DEFAULT_PATHS
    findings = scan_paths(paths)
    if findings:
        print("log/notification privacy check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("log/notification privacy check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
