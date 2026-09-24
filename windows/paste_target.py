"""Foreground destination identity for Windows clipboard-paste delivery.

GetForegroundWindow identifies the top-level window, not necessarily its
focused child control. Querying the owning GUI thread adds a useful guard for
classic Win32 edit controls; custom-rendered fields may share one HWND. A
private fingerprint of a nonempty window caption catches some same-HWND tab
changes, but neither captions nor HWNDs identify every browser field.
"""

import ctypes
import hashlib
import hmac
import secrets
from typing import NamedTuple


# Captions can contain document names or private page titles. Never retain
# their plaintext in PasteTarget, diagnostics, notifications, or logs.
_CAPTION_KEY = secrets.token_bytes(32)
_MAX_CAPTION_CHARS = 8192


class PasteTarget(NamedTuple):
    process_name: str
    window_handle: int
    process_identifier: int = 0
    integrity_level: int = 0
    # None means the Win32 focus query failed or observed an incoherent window.
    # Zero is reserved for a completed query with no child HWND available.
    focus_handle: int | None = 0
    # None means no usable nonempty caption was available, not proof of a tab.
    caption_fingerprint: bytes | None = None


def input_integrity_blocks_delivery(target_level, source_level):
    """Require two known integrity levels before trusting simulated input.

    SendInput cannot cross upward through UIPI, and its result cannot identify
    UIPI as the cause of a failure. An unreadable target or source label is
    therefore not evidence that copying a transcript before SendInput is safe.
    """
    return (type(target_level) is not int or target_level <= 0 or
            type(source_level) is not int or source_level <= 0 or
            target_level > source_level)


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
    )


class _GUIThreadInfo(ctypes.Structure):
    """Fixed-width Win32 GUITHREADINFO, including pointer-width HWNDs."""

    _fields_ = (
        ("cbSize", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", _RECT),
    )


def focused_child_handle(user32, window_handle, thread_identifier):
    """Return keyboard focus only if it still belongs to the captured window.

    GetFocus reads the caller's queue, not the target app's. A failed or
    incoherent GetGUIThreadInfo query, including a focused HWND outside the
    foreground window's parent tree, is not equivalent to a successful query
    with no focus HWND. Mark it unavailable so two failures cannot authorize
    a window-only paste into a different field of the same window.
    """
    if not window_handle or not thread_identifier:
        return None
    try:
        user32.GetGUIThreadInfo.argtypes = (
            ctypes.c_uint32, ctypes.POINTER(_GUIThreadInfo))
        user32.GetGUIThreadInfo.restype = ctypes.c_int
        info = _GUIThreadInfo()
        info.cbSize = ctypes.sizeof(_GUIThreadInfo)
        if not user32.GetGUIThreadInfo(thread_identifier, ctypes.byref(info)):
            return None
        if (int(info.hwndActive or 0) != window_handle or
                int(user32.GetForegroundWindow() or 0) != window_handle):
            return None
        focus_handle = int(info.hwndFocus or 0)
        if focus_handle:
            # GetGUIThreadInfo can report invalid HWNDs during activation
            # changes. A focus handle from another top-level window must not
            # authorize a paste merely because hwndActive still looks right.
            user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            user32.GetAncestor.restype = ctypes.c_void_p
            if int(user32.GetAncestor(focus_handle, 2) or 0) != window_handle:
                return None  # GA_ROOT walks parents, not owned popups.
        if int(user32.GetForegroundWindow() or 0) != window_handle:
            return None
        return focus_handle
    except Exception:
        return None


def window_caption_fingerprint(user32, window_handle):
    """Fingerprint a complete foreground caption without retaining its text.

    GetWindowTextW reads a different process's *caption*, not its edit-control
    contents. A bounded buffer and a second foreground check avoid adopting a
    truncated or stale title as evidence that the original tab is still open.
    An empty or unavailable caption provides no additional identity signal.
    """
    if not window_handle:
        return None
    try:
        user32.GetWindowTextLengthW.argtypes = (ctypes.c_void_p,)
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = (
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
        user32.GetWindowTextW.restype = ctypes.c_int
        if int(user32.GetForegroundWindow() or 0) != window_handle:
            return None
        length = int(user32.GetWindowTextLengthW(window_handle))
        if not 0 < length <= _MAX_CAPTION_CHARS:
            return None
        # One extra character beyond the reported length distinguishes a
        # title that grew during the two calls from a complete read.
        caption = ctypes.create_unicode_buffer(length + 2)
        copied = int(user32.GetWindowTextW(
            window_handle, caption, len(caption)))
        if (not 0 < copied < len(caption) - 1 or
                int(user32.GetForegroundWindow() or 0) != window_handle):
            return None
        return hmac.new(
            _CAPTION_KEY, caption.value.encode("utf-16-le", "surrogatepass"),
            hashlib.sha256
        ).digest()
    except Exception:
        return None


def same_window(expected, current):
    """Match the top-level window and its owner, regardless of child focus."""
    if (not expected.window_handle or
            current.window_handle != expected.window_handle):
        return False
    if expected.process_identifier:
        return current.process_identifier == expected.process_identifier
    if expected.process_name:
        return current.process_name == expected.process_name
    return False


def matches(expected, current):
    """Match window owner, observed Win32 focus and available caption identity.

    Some toolkits never expose a useful focus HWND. When both *successful*
    observations lack one, retain the exact top-level-window policy. A failed
    or incoherent query is not evidence of no focused child; recover instead
    of treating two failures as a match. Matching captions are not proof that
    the same browser tab or DOM field remains selected.
    """
    return (same_window(expected, current) and
            expected.focus_handle is not None and
            current.focus_handle is not None and
            current.focus_handle == expected.focus_handle and
            current.caption_fingerprint == expected.caption_fingerprint)
