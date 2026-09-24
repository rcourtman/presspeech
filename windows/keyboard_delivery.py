"""Checked Win32 keyboard events for paste-shortcut delivery.

A complete shortcut is submitted in one SendInput call so Windows cannot
interleave physical or separately injected input between its events. The
current modifier state is checked immediately before submission; a later key
press can still race it. The caller can attempt key-up cleanup when Windows
does not accept the whole batch. Importing this module does not load a Windows
DLL or inject input.
"""
import ctypes


VK_V = 0x56
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_MODIFIER_KEYS = (
    VK_V, VK_SHIFT, VK_CONTROL, VK_MENU,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
)

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_MAPVK_VK_TO_VSC = 0


class KeyboardDeliveryError(OSError):
    """Windows did not confirm that a requested keyboard event was inserted."""


class ModifierHeldError(KeyboardDeliveryError):
    """A held paste key would change the intended shortcut."""


class ModifierStateError(KeyboardDeliveryError):
    """The physical modifier snapshot could not be read."""


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
        self.GetAsyncKeyState = self.user32.GetAsyncKeyState
        self.GetAsyncKeyState.restype = ctypes.c_int16
        self.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.SendInput = self.user32.SendInput
        self.SendInput.restype = ctypes.c_uint32
        self.SendInput.argtypes = (
            ctypes.c_uint32,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        )


def paste_keys_held(*, api=None):
    """Snapshot keys that could alter a paste chord, without injecting input.

    Use before replacing the clipboard as well as immediately before SendInput.
    A zero result can also mean Win32 denied the query, so this remains a
    point-in-time guard rather than proof that every key is up.
    """
    try:
        api = _WindowsAPI() if api is None else api
        return any(
            int(api.GetAsyncKeyState(key)) & 0x8000
            for key in _MODIFIER_KEYS
        )
    except Exception:
        raise ModifierStateError(
            "Windows could not check held modifier keys") from None


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

    def _input(self, virtual_key, flags):
        self._validate_virtual_key(virtual_key)
        scan_code = int(self._api.MapVirtualKeyW(
            virtual_key, _MAPVK_VK_TO_VSC))
        return _INPUT(
            type=_INPUT_KEYBOARD,
            value=_INPUT_VALUE(ki=_KEYBDINPUT(
                wVk=virtual_key,
                wScan=scan_code,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )),
        )

    def _modifiers_down(self):
        return paste_keys_held(api=self._api)

    def _send(self, events, *, check_modifiers=False):
        events = tuple(events)
        for virtual_key, _flags in events:
            self._validate_virtual_key(virtual_key)
        try:
            inputs = (_INPUT * len(events))(*(
                self._input(virtual_key, flags)
                for virtual_key, flags in events
            ))
            # SendInput does not reset physical keyboard state. A held paste
            # key can change the chord or be released by our synthetic key-up.
            # Snapshot as close as possible to the single SendInput call;
            # this is a guard, not an atomic guarantee against a later press.
            if check_modifiers and self._modifiers_down():
                raise ModifierHeldError(
                    "a paste key is held; paste was not attempted")
            inserted = int(self._api.SendInput(
                len(inputs), inputs, ctypes.sizeof(_INPUT)))
        except (ModifierHeldError, ModifierStateError):
            raise
        except Exception:
            raise KeyboardDeliveryError(
                "Windows did not accept the keyboard event") from None
        if inserted != len(inputs):
            raise KeyboardDeliveryError(
                "Windows did not accept the keyboard event")

    def press(self, virtual_key):
        self._send(((virtual_key, 0),))

    def release(self, virtual_key):
        self._send(((virtual_key, _KEYEVENTF_KEYUP),))

    def shortcut(self, modifiers, virtual_key):
        """Insert one non-interleavable modifier/key shortcut transaction."""
        modifiers = tuple(modifiers)
        if (not modifiers or virtual_key in modifiers or
                len(set(modifiers)) != len(modifiers)):
            raise ValueError("shortcut requires distinct modifier keys")
        events = [*(
            (modifier, 0) for modifier in modifiers
        ), (virtual_key, 0), (virtual_key, _KEYEVENTF_KEYUP), *(
            (modifier, _KEYEVENTF_KEYUP) for modifier in reversed(modifiers)
        )]
        self._send(events, check_modifiers=True)
