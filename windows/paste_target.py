"""Foreground destination identity for Windows clipboard-paste delivery.

GetForegroundWindow identifies the top-level window, not necessarily its
focused child control. Querying the owning GUI thread adds a useful guard for
classic Win32 edit controls; custom-rendered fields may share one HWND.
"""

import ctypes
from typing import NamedTuple


class PasteTarget(NamedTuple):
    process_name: str
    window_handle: int
    process_identifier: int = 0
    integrity_level: int = 0
    focus_handle: int = 0


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
    incoherent GetGUIThreadInfo query supplies no control-level identity; it
    must never borrow a child HWND from a newly foreground window.
    """
    if not window_handle or not thread_identifier:
        return 0
    try:
        user32.GetGUIThreadInfo.argtypes = (
            ctypes.c_uint32, ctypes.POINTER(_GUIThreadInfo))
        user32.GetGUIThreadInfo.restype = ctypes.c_int
        info = _GUIThreadInfo()
        info.cbSize = ctypes.sizeof(_GUIThreadInfo)
        if not user32.GetGUIThreadInfo(thread_identifier, ctypes.byref(info)):
            return 0
        if (int(info.hwndActive or 0) != window_handle or
                int(user32.GetForegroundWindow() or 0) != window_handle):
            return 0
        return int(info.hwndFocus or 0)
    except Exception:
        return 0


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
    """Match window owner and any child control observed when capture began.

    Some toolkits do not expose a useful focus HWND. In that case the prior
    exact top-level-window policy remains in force, rather than disabling
    automatic paste for those apps. This is not a DOM-field identity check.
    """
    return (same_window(expected, current) and
            (not expected.focus_handle or
             current.focus_handle == expected.focus_handle))
