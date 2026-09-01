"""Small Win32 bridge for screen-reader announcements from Tk status labels.

Tk and tk-uia expose a changed label's current name, but a label outside the
keyboard focus is not automatically spoken. Microsoft documents the missing
contract as a UI Automation LiveSetting property followed by an
EVENT_OBJECT_LIVEREGIONCHANGED event whenever the visible status changes.

This module intentionally reaches Windows only when a live region is marked.
That keeps model-free tests and source inspection portable to non-Windows
workers. Presspeech is x64-only, matching the VARIANT layout below.
"""

import ctypes
import sys
import threading


POLITE = 1
ASSERTIVE = 2

_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = 0x80010106
_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_INPROC_SERVER = 1

_OBJID_CLIENT = 0xFFFFFFFC
_OBJID_CLIENT_SIGNED = -4
_CHILDID_SELF = 0
_VT_I4 = 3
_EVENT_OBJECT_LIVEREGIONCHANGED = 0x8019

_SLOT_SET_HWND_PROP = 6
_SLOT_CLEAR_HWND_PROPS = 9


class _Guid(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _Variant(ctypes.Structure):
    """The 24-byte x64 VARIANT shape used here to carry one signed integer."""

    _fields_ = (
        ("vt", ctypes.c_ushort),
        ("reserved1", ctypes.c_ushort),
        ("reserved2", ctypes.c_ushort),
        ("reserved3", ctypes.c_ushort),
        ("value", ctypes.c_longlong),
        ("padding", ctypes.c_longlong),
    )


def _guid(first, second, third, *rest):
    return _Guid(first, second, third, (ctypes.c_ubyte * 8)(*rest))


# Values transcribed from the Windows SDK headers (oleacc.h and
# UIAutomationCoreApi.h). MSAAPROPID is a GUID passed by value, not by pointer.
_CLSID_ACC_PROP_SERVICES = _guid(
    0xB5F8350B, 0x0548, 0x48B1, 0xA6, 0xEE, 0x88, 0xBD, 0x00, 0xB4, 0xA5, 0xE7)
_IID_IACC_PROP_SERVICES = _guid(
    0x6E26E776, 0x04F0, 0x495D, 0x80, 0xE4, 0x33, 0x30, 0x35, 0x2E, 0x31, 0x69)
_LIVE_SETTING_PROPERTY = _guid(
    0xC12BCD8E, 0x2A8E, 0x4950, 0x8A, 0xE7, 0x36, 0x25, 0x11, 0x1D, 0x58, 0xEB)


class _LiveRegionStore:
    """One apartment-bound IAccPropServices instance."""

    def __init__(self):
        self._services = None

    def mark(self, hwnd, priority):
        holder = _Variant()
        holder.vt = _VT_I4
        holder.value = priority
        services = self._reached()
        call = _method(services, _SLOT_SET_HWND_PROP, _set_hwnd_prop())
        _checked(call(
            services, ctypes.c_void_p(hwnd), _OBJID_CLIENT, _CHILDID_SELF,
            _LIVE_SETTING_PROPERTY, holder), "SetHwndProp(LiveSetting)")

    def clear(self, hwnd):
        props = (_Guid * 1)(_LIVE_SETTING_PROPERTY)
        services = self._reached()
        call = _method(services, _SLOT_CLEAR_HWND_PROPS, _clear_hwnd_props())
        _checked(call(
            services, ctypes.c_void_p(hwnd), _OBJID_CLIENT, _CHILDID_SELF,
            props, 1), "ClearHwndProps(LiveSetting)")

    def _reached(self):
        if self._services is None:
            self._services = _acc_prop_services()
        return self._services


class LiveRegions:
    """Mark and announce HWND-backed status controls on their owning thread."""

    def __init__(self, platform=None):
        self.platform = sys.platform if platform is None else platform
        self._local = threading.local()

    def mark(self, hwnd, priority=POLITE):
        if self.platform != "win32":
            return False
        if priority not in (POLITE, ASSERTIVE):
            raise ValueError("unknown live-region priority")
        self._store().mark(int(hwnd), priority)
        return True

    def announce(self, hwnd):
        if self.platform != "win32":
            return False
        notify = ctypes.windll.user32.NotifyWinEvent
        notify.argtypes = (
            ctypes.c_ulong, ctypes.c_void_p, ctypes.c_long, ctypes.c_long)
        notify.restype = None
        notify(
            _EVENT_OBJECT_LIVEREGIONCHANGED, ctypes.c_void_p(int(hwnd)),
            _OBJID_CLIENT_SIGNED, _CHILDID_SELF)
        return True

    def clear(self, hwnd):
        if self.platform != "win32":
            return False
        self._store().clear(int(hwnd))
        return True

    def _store(self):
        store = getattr(self._local, "store", None)
        if store is None:
            store = _LiveRegionStore()
            self._local.store = store
        return store


def _acc_prop_services():
    ole32 = ctypes.windll.ole32
    started = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if started not in (_S_OK, _S_FALSE) and (started & 0xFFFFFFFF) != \
            _RPC_E_CHANGED_MODE:
        raise OSError(
            "CoInitializeEx failed 0x%08X" % (started & 0xFFFFFFFF))
    services = ctypes.c_void_p()
    _checked(ole32.CoCreateInstance(
        ctypes.byref(_CLSID_ACC_PROP_SERVICES), None,
        _CLSCTX_INPROC_SERVER, ctypes.byref(_IID_IACC_PROP_SERVICES),
        ctypes.byref(services)), "CoCreateInstance(AccPropServices)")
    return services


def _method(services, slot, prototype):
    vtable = ctypes.cast(services, ctypes.POINTER(ctypes.c_void_p))[0]
    return prototype(ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[slot])


def _checked(result, operation):
    if result != _S_OK:
        raise OSError(
            "%s failed 0x%08X" % (operation, result & 0xFFFFFFFF))


def _set_hwnd_prop():
    return ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        _Guid,
        _Variant,
    )


def _clear_hwnd_props():
    return ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(_Guid),
        ctypes.c_int,
    )
