"""Checked Win32 keyboard events for paste-shortcut delivery.

Each event is sent separately so the caller can retain dictation and attempt
key-up cleanup whenever Windows does not accept the complete event. Importing
this module does not load a Windows DLL or inject input.
"""
import ctypes


VK_V = 0x56
VK_LSHIFT = 0xA0
VK_LCONTROL = 0xA2
VK_LMENU = 0xA4

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_MAPVK_VK_TO_VSC = 0


class KeyboardDeliveryError(OSError):
    """Windows did not confirm that a requested keyboard event was inserted."""


# Win32 LONG and DWORD remain 32-bit when Python itself is 64-bit. Fixed-width
# fields keep this ABI correct on both Windows architectures and make the
# structure safely testable on other hosts.
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    )


class _INPUT_VALUE(ctypes.Union):
    _fields_ = (
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    )


class _INPUT(ctypes.Structure):
    _fields_ = (
        ("type", ctypes.c_uint32),
        ("value", _INPUT_VALUE),
    )


class _WindowsAPI:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.MapVirtualKeyW = self.user32.MapVirtualKeyW
        self.MapVirtualKeyW.restype = ctypes.c_uint32
        self.MapVirtualKeyW.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
        self.SendInput = self.user32.SendInput
        self.SendInput.restype = ctypes.c_uint32
        self.SendInput.argtypes = (
            ctypes.c_uint32,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        )


class Controller:
    """Send virtual-key events and require Win32 to accept every event."""

    def __init__(self, *, api=None):
        self._api = _WindowsAPI() if api is None else api

    @staticmethod
    def _validate_virtual_key(virtual_key):
        if (isinstance(virtual_key, bool) or
                not isinstance(virtual_key, int) or
                not 1 <= virtual_key <= 0xFE):
            raise ValueError("virtual key must be an integer from 1 through 254")

    def _send(self, virtual_key, flags):
        self._validate_virtual_key(virtual_key)
        try:
            scan_code = int(self._api.MapVirtualKeyW(
                virtual_key, _MAPVK_VK_TO_VSC))
            event = _INPUT(
                type=_INPUT_KEYBOARD,
                value=_INPUT_VALUE(ki=_KEYBDINPUT(
                    wVk=virtual_key,
                    wScan=scan_code,
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=0,
                )),
            )
            inserted = int(self._api.SendInput(
                1, ctypes.byref(event), ctypes.sizeof(_INPUT)))
        except Exception:
            raise KeyboardDeliveryError(
                "Windows did not accept the keyboard event") from None
        if inserted != 1:
            raise KeyboardDeliveryError(
                "Windows did not accept the keyboard event")

    def press(self, virtual_key):
        self._send(virtual_key, 0)

    def release(self, virtual_key):
        self._send(virtual_key, _KEYEVENTF_KEYUP)
