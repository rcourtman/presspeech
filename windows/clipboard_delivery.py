"""History-excluded Unicode clipboard writes with a receipt proven under the lock.

A matching sequence only proves that Windows has not reported another change.
It cannot acknowledge consumption or make a later simulated paste atomic.
Successful writes opt out of Windows Clipboard History and Cloud Clipboard.
Importing this module does not open the clipboard or create a window.
"""
import ctypes
import time
import secrets
from typing import NamedTuple


_RECEIPT_FORMAT = "Presspeech.WriteReceipt.v1"
# Windows recognises this registered format and excludes every format in the
# same clipboard item from Clipboard History, Cloud Clipboard, and clipboard
# monitor processing.  The transcript remains on the current clipboard for
# Ctrl+V and explicit recovery; third-party readers are a separate boundary.
_PRIVATE_CLIPBOARD_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"
_PRIVATE_CLIPBOARD_MARKER = (0).to_bytes(4, "little")


class WriteReceipt(NamedTuple):
    sequence: int


class ClipboardError(OSError):
    """Clipboard operation failed; messages never contain clipboard contents."""


class _WindowsAPI:
    def __init__(self):
        from ctypes import wintypes as w
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = (
            (self.user32, "CreateWindowExW", w.HWND,
             [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU,
              w.HINSTANCE, w.LPVOID]),
            (self.user32, "DestroyWindow", w.BOOL, [w.HWND]),
            (self.user32, "OpenClipboard", w.BOOL, [w.HWND]),
            (self.user32, "CloseClipboard", w.BOOL, []),
            (self.user32, "EmptyClipboard", w.BOOL, []),
            (self.user32, "SetClipboardData", w.HANDLE, [w.UINT, w.HANDLE]),
            (self.user32, "GetClipboardSequenceNumber", w.DWORD, []),
            (self.user32, "GetClipboardOwner", w.HWND, []),
            (self.user32, "RegisterClipboardFormatW", w.UINT, [w.LPCWSTR]),
            (self.user32, "GetClipboardData", w.HANDLE, [w.UINT]),
            (self.kernel32, "GlobalAlloc", w.HGLOBAL, [w.UINT, ctypes.c_size_t]),
            (self.kernel32, "GlobalLock", w.LPVOID, [w.HGLOBAL]),
            (self.kernel32, "GlobalUnlock", w.BOOL, [w.HGLOBAL]),
            (self.kernel32, "GlobalFree", w.HGLOBAL, [w.HGLOBAL]),
            (self.kernel32, "GlobalSize", ctypes.c_size_t, [w.HGLOBAL]),
        )
        for library, name, result, arguments in signatures:
            function = getattr(library, name)
            function.restype = result
            function.argtypes = arguments
            setattr(self, name, function)


def is_current(receipt, *, api=None):
    """Fail closed when sequence access is unavailable, zero, or changed."""
    if not isinstance(receipt, WriteReceipt) or not receipt.sequence:
        return False
    try:
        api = _WindowsAPI() if api is None else api
        return int(api.GetClipboardSequenceNumber()) == receipt.sequence
    except Exception:
        return False


def write_text(text, *, api=None, sleep=time.sleep, monotonic=time.monotonic):
    """Copy once, then prove the settled sequence still belongs to this write.

    CloseClipboard synthesizes text formats and advances the sequence. Keep the
    owner window alive while reopening, and validate owner, nonce AND text under
    the reacquired lock before capturing that settled sequence. Never adopt an
    external writer's serial merely because it happened after our own write.
    """
    if not isinstance(text, str) or "\0" in text:
        raise ClipboardError("clipboard text is not a supported Unicode string")
    # CF_UNICODETEXT requires CRLF. Bare LF (including the Newline suffix)
    # does not preserve trailing blank lines in standard Windows edit controls.
    # Normalize only at this boundary: retained dictation and Tk text stay intact.
    clipboard_text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    payload = clipboard_text.encode("utf-16-le") + b"\0\0"
    nonce = secrets.token_bytes(32)
    api = _WindowsAPI() if api is None else api
    token_format = api.RegisterClipboardFormatW(_RECEIPT_FORMAT)
    private_format = api.RegisterClipboardFormatW(_PRIVATE_CLIPBOARD_FORMAT)
    if not token_format or not private_format:
        raise ClipboardError("required clipboard formats unavailable")
    window = api.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0,
                                 ctypes.c_void_p(-3), None, None, None)
    if not window:
        raise ClipboardError("clipboard owner window unavailable")
    allocations = []
    opened = False

    def allocate(data):
        memory = api.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not memory:
            raise ClipboardError("clipboard allocation failed")
        allocations.append(memory)
        pointer = api.GlobalLock(memory)
        if not pointer:
            raise ClipboardError("clipboard memory lock failed")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            # A zero result also means the lock count reached zero.
            api.GlobalUnlock(memory)
        return memory

    def matches(format_id, expected):
        memory = api.GetClipboardData(format_id)
        if not memory or api.GlobalSize(memory) < len(expected):
            return False
        pointer = api.GlobalLock(memory)
        if not pointer:
            return False
        try:
            return ctypes.string_at(pointer, len(expected)) == expected
        finally:
            api.GlobalUnlock(memory)

    def acquire(deadline):
        nonlocal opened
        while not api.OpenClipboard(window):
            if monotonic() >= deadline:
                raise ClipboardError("clipboard is unavailable")
            sleep(0.01)
        opened = True

    def close():
        nonlocal opened
        if not api.CloseClipboard():
            raise ClipboardError("clipboard close failed")
        opened = False

    try:
        # Allocate every block before changing any existing clipboard contents.
        # Publish the privacy marker first: any later partial write may leave a
        # clipboard item behind, but it must never leave transcript text without
        # the Windows history/cloud exclusion that this function promises.
        private_memory = allocate(_PRIVATE_CLIPBOARD_MARKER)
        text_memory = allocate(payload)
        token_memory = allocate(nonce)
        deadline = monotonic() + 0.5
        acquire(deadline)
        if not api.EmptyClipboard():
            raise ClipboardError("clipboard could not be emptied")
        try:
            for format_id, memory in (
                    (private_format, private_memory),
                    (13, text_memory),
                    (token_format, token_memory)):
                if not api.SetClipboardData(format_id, memory):
                    raise ClipboardError("clipboard write failed")
                allocations.remove(memory)  # Windows owns this block now.
        except Exception:
            # A failed receipt write can otherwise leave unverified transcript
            # text on the current clipboard. We still hold the clipboard lock:
            # clear only our own partial item, never a newer owner's copy.
            # The previous item was already discarded by EmptyClipboard and
            # cannot be restored here. If cleanup itself fails, the privacy
            # marker was published before any transcript text.
            try:
                if api.GetClipboardOwner() == window:
                    api.EmptyClipboard()
            except Exception:
                pass
            raise
        close()  # Windows finalizes text formats and advances the serial here.
        acquire(deadline)
        if (api.GetClipboardOwner() != window or
                not matches(private_format, _PRIVATE_CLIPBOARD_MARKER) or
                not matches(token_format, nonce) or not matches(13, payload)):
            raise ClipboardError("clipboard changed before receipt validation")
        sequence = int(api.GetClipboardSequenceNumber())
        if not sequence:
            raise ClipboardError("clipboard ownership could not be verified")
        close()
        if not api.DestroyWindow(window):
            raise ClipboardError("clipboard owner cleanup failed")
        window = None
        if int(api.GetClipboardSequenceNumber()) != sequence:
            raise ClipboardError("clipboard changed after receipt validation")
        return WriteReceipt(sequence)
    finally:
        try:
            if opened and not api.CloseClipboard():
                raise ClipboardError("clipboard close failed")
        finally:
            for memory in allocations:
                api.GlobalFree(memory)
            if window:
                api.DestroyWindow(window)
