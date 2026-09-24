"""Tkinter windows: setup, settings, delivery recovery, and status UI."""

import ctypes
import math
import os
import queue
import re
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import winreg
except ImportError:
    # Keep the model-free UI helpers importable on non-Windows development
    # hosts. The packaged application always has the standard winreg module.
    winreg = None

import config as cfg
import clipboard_delivery
import live_region
import updates


try:
    import tk_uia
except ImportError:
    class _UnavailableTkUia:
        """Keep source installs usable when the optional bridge is absent."""

        @staticmethod
        def _unavailable(*_args, **_kwargs):
            raise RuntimeError("tk-uia is not installed")

        enable = _unavailable
        add_acc_object = _unavailable
        label_for = _unavailable
        set_acc_name = _unavailable
        set_acc_description = _unavailable

    tk_uia = _UnavailableTkUia()


class _WindowHost:
    """Own every interactive Tk window on one accessible UI thread."""

    def __init__(self):
        self.commands = queue.Queue()
        self.root = None
        self.failure = None
        self.accessibility = "not initialized"
        self.ready = threading.Event()
        threading.Thread(
            target=self._run, name="presspeech-ui", daemon=True).start()

    def submit(self, command):
        self.ready.wait()
        if self.failure is not None:
            raise RuntimeError("Presspeech could not start its window system") \
                from self.failure
        self.commands.put(command)

    def _run(self):
        try:
            root = tk.Tk()
            self.root = root
            root.withdraw()
            _watch_windows_text_scale(root)
            # Tk 8.6's Windows accessibility proxy leaves most ttk controls
            # anonymous or inert. One installation follows every later
            # Toplevel created by this interpreter.
            try:
                strategy = tk_uia.enable(root)
                self.accessibility = strategy.name.lower()
            except Exception:
                # An assistive-technology integration failure must not take
                # dictation's setup and recovery windows away from the user.
                self.accessibility = "unavailable"
            self.ready.set()

            def poll():
                try:
                    while True:
                        command = self.commands.get_nowait()
                        try:
                            command()
                        except Exception as exc:
                            root.report_callback_exception(
                                type(exc), exc, exc.__traceback__)
                except queue.Empty:
                    pass
                root.after(25, poll)

            root.after(0, poll)
            root.mainloop()
        except Exception as exc:
            self.failure = exc
            self.ready.set()


_WINDOW_HOST = None
_WINDOW_HOST_LOCK = threading.Lock()
_LIVE_REGIONS = live_region.LiveRegions()

ALTGR_HOTKEY_GUIDANCE = (
    "If Right Alt types @, €, or accented letters, Windows is using AltGr and "
    "that key will not start dictation. Choose F8 or another key."
)
PASTE_SUFFIX_GUIDANCE = (
    "A newline can submit dictated text in shells and terminals. Dictate "
    "commands into Try Dictation or a plain-text editor and review before "
    "pasting into a shell. Space is the default, not a safety barrier."
)
PASTE_SUFFIX_ACCESSIBLE_NAME = "After pasting. " + PASTE_SUFFIX_GUIDANCE

MODEL_DOWNLOAD_PRIVACY_NOTICE = (
    "Hugging Face receives a request for the selected model and revision. "
    "Presspeech disables Hugging Face model-library telemetry and sends no "
    "account token; audio and transcripts stay on this PC. Presspeech honors "
    "configured HTTP proxy and custom CA settings; a TLS-inspecting proxy "
    "trusted by that CA configuration can also see the model request."
)
MODEL_DOWNLOAD_ACCESSIBLE_DESCRIPTION = (
    "Model requests target Hugging Face. Model-library telemetry and account "
    "tokens are disabled; dictation audio and transcripts stay on this PC. "
    "Configured HTTP proxy and custom CA settings still apply; a trusted "
    "TLS-inspecting proxy can read the request."
)


def _window_host():
    global _WINDOW_HOST
    with _WINDOW_HOST_LOCK:
        if _WINDOW_HOST is None:
            _WINDOW_HOST = _WindowHost()
        return _WINDOW_HOST


class _WindowCallbacks:
    """Cancel one dialog's Tcl callbacks before its widget commands disappear.

    Tk deletes Python callback commands on destroy, but its interpreter-wide
    after queue survives while the shared host runs. Keep this lifetime local
    to each Toplevel; the host and sibling windows retain their own callbacks.
    """

    def __init__(self, root):
        self._after = root.after
        self._cancel = root.after_cancel
        self._destroy = root.destroy
        self._pending = set()
        self._closed = False
        root.after = self.after
        root.after_cancel = self.cancel
        root.destroy = self.destroy

    def after(self, ms, func=None, *args):
        if self._closed:
            raise tk.TclError("Cannot schedule a closed dialog callback")
        if func is None:
            return self._after(ms)

        def run(*values):
            self._pending.discard(identifier)
            if not self._closed:
                return func(*values)

        # Tk's after_idle delegates to after("idle", ...), so it shares the
        # same ownership without changing idle scheduling or callback arguments.
        identifier = self._after(ms, run, *args)
        self._pending.add(identifier)
        return identifier

    def cancel(self, id):
        self._cancel(id)
        self._pending.discard(id)

    def destroy(self):
        self._closed = True
        for identifier in tuple(self._pending):
            try:
                self._cancel(identifier)
            except tk.TclError:
                # Tcl may already have removed a completed/cancelled command.
                pass
        self._pending.clear()
        return self._destroy()


def _interactive_window(title):
    root = tk.Toplevel(_window_host().root)
    _WindowCallbacks(root)
    root.title(title)
    return root


def present_window(window):
    """Restore and foreground an existing interactive window on its UI thread."""
    def present():
        root = getattr(window, "root", None)
        if root is None:
            return
        try:
            root.deiconify()
            root.lift()
            # A launch from the Start Menu is an explicit request to see the
            # running app. Brief topmost placement also recovers a window that
            # was covered by other application windows.
            root.attributes("-topmost", True)

            def clear_topmost():
                try:
                    root.attributes("-topmost", False)
                except tk.TclError:
                    pass

            root.after(250, clear_topmost)
            root.focus_force()
        except tk.TclError:
            # The close callback can run before this queued presentation.
            return

    _window_host().submit(present)


def accessibility_status():
    """Return a privacy-safe summary for Copy Diagnostics."""
    if _WINDOW_HOST is None:
        return "not initialized"
    return _WINDOW_HOST.accessibility


def _accessibility_failed():
    if _WINDOW_HOST is not None:
        _WINDOW_HOST.accessibility = "degraded"


def _label_control(label, control):
    """Expose an explicit accessible name for a captioned form control."""
    try:
        tk_uia.label_for(label, control)
    except Exception:
        _accessibility_failed()


def _name_control(control, name):
    try:
        tk_uia.set_acc_name(control, name)
    except Exception:
        _accessibility_failed()


def _describe_control(control, description):
    """Expose supplementary instructions without overloading a control name."""
    try:
        tk_uia.set_acc_description(control, description)
    except Exception:
        _accessibility_failed()


def _label_trigger_choices(label, hold, toggle):
    """Give both trigger choices context in UI Automation, not just visually."""
    _label_control(label, hold)
    _name_control(hold, "Dictation style: Hold to talk")
    _name_control(toggle, "Dictation style: Press to toggle")


def _set_accessible_text(widget, text, announce=None):
    """Keep visible/UIA text aligned and announce marked status changes."""
    try:
        changed = str(widget.cget("text")) != text
    except Exception:
        # Test doubles and a widget racing destruction may not expose cget.
        changed = True
    options = {"text": text}
    access_key = widget.__dict__.get("_presspeech_access_key")
    if access_key is not None:
        # Dynamic command labels (notably Dictate -> Stop Dictation) must keep
        # underlining the key that the window binding actually invokes.
        options["underline"] = _access_key_index(text, access_key)
    widget.config(**options)
    try:
        tk_uia.add_acc_object(widget)
    except Exception:
        _accessibility_failed()
    if announce is None:
        announce = getattr(widget, "_presspeech_live_region", False) is True
    if changed and announce:
        try:
            _LIVE_REGIONS.announce(widget.winfo_id())
        except Exception:
            _accessibility_failed()


def _mark_live_region(widget, priority=live_region.POLITE):
    """Make important future status changes available without moving focus."""
    try:
        hwnd = widget.winfo_id()
        if not _LIVE_REGIONS.mark(hwnd, priority):
            return
        widget._presspeech_live_region = True

        def clear(event):
            if getattr(event, "widget", None) is not widget:
                return
            try:
                _LIVE_REGIONS.clear(hwnd)
            except Exception:
                _accessibility_failed()

        widget.bind("<Destroy>", clear, add="+")
    except Exception:
        _accessibility_failed()


def _access_key_index(text, key):
    """Return the visible mnemonic position, rejecting misleading bindings."""
    if len(key) != 1:
        raise ValueError("an access key must be one character")
    index = text.casefold().find(key.casefold())
    if index < 0:
        raise ValueError("access key is not present in the command label")
    return index


def _bind_window_command(root, sequence, command):
    """Bind one window-local keyboard command without leaking its Tk event."""
    def invoke(_event=None):
        command()
        return "break"

    root.bind(sequence, invoke, add="+")
    return invoke


def _hotkey_readiness(app):
    """Read optional app hotkey health without breaking lightweight test fakes."""
    checker = getattr(app, "hotkey_listener_status", None)
    if checker is None or not callable(checker):
        return "ready", "Global hotkey ready"
    try:
        result = checker()
    except Exception:
        return "error", "Global hotkey status could not be checked"
    if (not isinstance(result, tuple) or len(result) != 2 or
            result[0] not in ("not started", "starting", "ready", "error") or
            not isinstance(result[1], str)):
        # unittest.mock objects and older embedders do not implement the new
        # readiness contract. Treat those compatibility shims as ready.
        return "ready", "Global hotkey ready"
    return result


def _download_byte_count(value):
    """Return a finite, nonnegative progress value; Hub progress is untrusted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        amount = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    return amount


def _format_byte_quantity(value):
    """Format a byte count without implying a percentage or download ETA."""
    amount = _download_byte_count(value)
    if amount is None:
        return ""
    if amount < 1024:
        integer = int(amount)
        return "%d byte%s" % (integer, "" if integer == 1 else "s")
    units = ("KiB", "MiB", "GiB", "TiB")
    for unit in units:
        amount /= 1024
        if amount < 1024:
            return "%.1f %s" % (amount, unit)
    return "%.1f TiB" % amount


def _format_download_progress(progress):
    """Describe the active Hub file transfer, not the whole model download."""
    if not isinstance(progress, (tuple, list)) or len(progress) < 2:
        return ""
    done = _download_byte_count(progress[0])
    if done is None or done <= 0:
        return ""
    done_text = _format_byte_quantity(done)
    total = _download_byte_count(progress[1])
    if total is not None and total > 0:
        return " — %s of %s transferred in current file" % (
            done_text, _format_byte_quantity(total))
    return " — %s transferred in current file" % done_text


def _model_loading_feedback(app):
    """Return a truthful loading phase and per-file transfer amount."""
    detail = str(getattr(app, "model_status_detail", ""))
    if detail.startswith("Checking local model files"):
        label, phase = "Checking local model files…", "checking"
    elif detail.startswith("Downloading model files"):
        label, phase = "Downloading model files…", "downloading"
        label += _format_download_progress(
            getattr(app, "model_download_progress", None))
    elif detail.startswith("Verifying model files"):
        label, phase = "Verifying model files…", "verifying"
    elif detail.startswith("Loading speech model"):
        label, phase = "Loading speech model…", "loading"
    elif detail.startswith("Warming speech model"):
        label, phase = "Warming speech model…", "warming"
    else:
        label, phase = (
            "Preparing speech model — downloading or loading…", "preparing")
    return label, phase


def _settings_save_block_reason(app):
    """Explain why mutable settings cannot be committed at this instant."""
    # The UI poll is advisory. SettingsWindow._save repeats this check while
    # holding the app lock so Ctrl+S cannot race a recording start or the
    # transition from capture to transcription and delivery.
    if getattr(app, "_starting_recording", False) is True:
        return "Wait for the current dictation to finish starting before saving settings."
    if getattr(app, "recording", False) is True:
        return "Finish or cancel the current dictation before saving settings."
    if getattr(app, "_canceling_recording", False) is True:
        return "Wait for the canceled dictation to finish closing before saving settings."
    if getattr(app, "transcribing", False) is True:
        return "Wait for the current dictation to finish before saving settings."
    return ""


def _add_access_key(root, widget, key):
    """Give a command its conventional Windows Alt mnemonic."""
    key = key.casefold()
    widget._presspeech_access_key = key
    widget.config(underline=_access_key_index(str(widget.cget("text")), key))
    _bind_window_command(
        root, "<Alt-KeyPress-%s>" % key, widget.invoke)


def _set_control_state(root, control, state, fallback=None):
    """Change a control's state without leaving focus on a disabled widget."""
    if state == "disabled" and fallback is not None:
        try:
            if root.focus_get() is control:
                fallback.focus_set()
        except (AttributeError, tk.TclError):
            # The control can race its window's destruction or a lightweight
            # test host may not implement Tk's complete focus contract.
            pass
    control.config(state=state)


def _bounded_viewport(content_size, screen_size, margin, minimum):
    """Keep a scrollable dialog on screen without inventing a fixed size."""
    available = max(1, screen_size - margin)
    if available < minimum:
        # Keep a small border even on an unusually constrained remote desktop;
        # a nominal minimum must never make the window larger than the screen.
        available = max(1, screen_size - min(32, margin))
    return max(1, min(content_size, available))


def _scaled_pixels(value, pixels_per_inch):
    """Scale a 96-DPI layout value using Tk's effective display density."""
    try:
        scale = float(pixels_per_inch) / 96.0
    except (TypeError, ValueError, ZeroDivisionError):
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    return max(1, round(value * scale))


def _windows_text_scale(registry=None):
    """Return Windows' independent accessibility text-size multiplier."""
    registry = winreg if registry is None else registry
    if registry is None:
        return 1.0
    try:
        with registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                r"Software\Microsoft\Accessibility") as key:
            percentage, value_type = registry.QueryValueEx(
                key, "TextScaleFactor")
        if value_type != registry.REG_DWORD or type(percentage) is not int:
            return 1.0
    except (AttributeError, OSError, TypeError, ValueError):
        return 1.0
    # UISettings.TextScaleFactor documents the same system value as 1–2.25.
    # Treat an out-of-contract registry value as unavailable rather than
    # making the always-on-top indicator unusably large or small.
    if not 100 <= percentage <= 225:
        return 1.0
    return percentage / 100.0


class _TkTextScaling:
    """Apply Windows' independent text-size setting to point-sized Tk fonts."""

    def __init__(self, root):
        self.tk = root.tk
        try:
            baseline = float(self.tk.call("tk", "scaling"))
        except Exception:
            baseline = 1.0
        self.baseline = (
            baseline if math.isfinite(baseline) and baseline > 0 else 1.0)
        self.current = 1.0

    def update(self, text_scale):
        try:
            text_scale = float(text_scale)
        except (TypeError, ValueError):
            text_scale = 1.0
        if not 1.0 <= text_scale <= 2.25:
            text_scale = 1.0
        if text_scale == self.current:
            return False
        try:
            # Preserve Tk's DPI-derived baseline and scale only point units.
            self.tk.call("tk", "scaling", self.baseline * text_scale)
        except Exception:
            # Accessibility scaling is best-effort; never prevent setup from
            # opening if Tcl/Tk cannot change its scaling at runtime.
            return False
        self.current = text_scale
        return True


def _watch_windows_text_scale(root):
    """Keep shared dialog fonts aligned with Windows Text size changes."""
    scaling = _TkTextScaling(root)

    def refresh():
        scaling.update(_windows_text_scale())
        root.after(250, refresh)

    refresh()
    return scaling


def _scaled_font_points(points, text_scale):
    """Scale one small custom font without changing display-DPI geometry."""
    try:
        text_scale = float(text_scale)
    except (TypeError, ValueError):
        text_scale = 1.0
    if not 1.0 <= text_scale <= 2.25:
        text_scale = 1.0
    return max(1, int(points * text_scale + 0.5))


def _bounded_window_size(
        width, height, pixels_per_inch, screen_width, screen_height):
    """Scale a compact window while retaining a usable desktop border."""
    return (
        _bounded_viewport(
            _scaled_pixels(width, pixels_per_inch), screen_width, 64, 320),
        _bounded_viewport(
            _scaled_pixels(height, pixels_per_inch), screen_height, 96, 240),
    )


def _colourref_hex(value):
    """Convert a Win32 COLORREF (0x00bbggrr) to a Tk colour string."""
    value = int(value)
    red = value & 0xff
    green = (value >> 8) & 0xff
    blue = (value >> 16) & 0xff
    return "#%02x%02x%02x" % (red, green, blue)


class _HIGHCONTRAST(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("lpszDefaultScheme", ctypes.c_void_p),
    ]


def _indicator_system_palette(user32):
    """Return the active Windows contrast palette, or None in normal mode."""
    settings = _HIGHCONTRAST()
    settings.cbSize = ctypes.sizeof(settings)
    try:
        available = user32.SystemParametersInfoW(
            0x0042, settings.cbSize, ctypes.byref(settings), 0)
    except (AttributeError, OSError):
        return None
    if not available or not settings.dwFlags & 0x00000001:
        return None
    try:
        # A dictation state is transient, selected/in-progress UI. Windows'
        # highlight pair remains legible across built-in and custom contrast
        # themes; state text means colour is never the only distinction.
        background = _colourref_hex(user32.GetSysColor(13))  # COLOR_HIGHLIGHT
        foreground = _colourref_hex(user32.GetSysColor(14))  # COLOR_HIGHLIGHTTEXT
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return background, foreground


class _ScrollableDialogBody:
    """A dialog body that preserves access at large text/display scales."""

    _SCREEN_WIDTH_MARGIN = 96
    _SCREEN_HEIGHT_MARGIN = 128

    def __init__(self, root, padding):
        self.root = root
        self.outer = ttk.Frame(root)
        self.outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            self.outer, highlightthickness=0, borderwidth=0,
            background=root.cget("background"), takefocus=False)
        self.vertical = ttk.Scrollbar(
            self.outer, orient="vertical", command=self.canvas.yview,
            takefocus=True)
        self.horizontal = ttk.Scrollbar(
            self.outer, orient="horizontal", command=self.canvas.xview,
            takefocus=True)
        self.canvas.configure(
            yscrollcommand=self._set_vertical,
            xscrollcommand=self._set_horizontal)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical.grid(row=0, column=1, sticky="ns")
        self.horizontal.grid(row=1, column=0, sticky="ew")
        self.outer.rowconfigure(0, weight=1)
        self.outer.columnconfigure(0, weight=1)

        self.content = ttk.Frame(self.canvas, padding=padding)
        self.content_window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        root.bind("<MouseWheel>", self._mouse_wheel, add="+")
        # FocusIn reaches a toplevel through each child widget's bind tags.
        # Keeping the focused control visible makes Tab navigation useful even
        # when text scaling pushes controls outside the initial viewport.
        root.bind("<FocusIn>", self._focus_changed, add="+")

    def fit_to_screen(self):
        """Size the initial viewport to content, capped below the desktop."""
        self.content.update_idletasks()
        width = _bounded_viewport(
            self.content.winfo_reqwidth(), self.root.winfo_screenwidth(),
            self._SCREEN_WIDTH_MARGIN, 320)
        height = _bounded_viewport(
            self.content.winfo_reqheight(), self.root.winfo_screenheight(),
            self._SCREEN_HEIGHT_MARGIN, 240)
        self.canvas.configure(width=width, height=height)
        self.root.minsize(min(width, 420), min(height, 320))

    def _set_vertical(self, first, last):
        self.vertical.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vertical.grid_remove()
        else:
            self.vertical.grid()

    def _set_horizontal(self, first, last):
        self.horizontal.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.horizontal.grid_remove()
        else:
            self.horizontal.grid()

    def _content_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_changed(self, event):
        # Fill spare width while preserving the content's requested width when
        # the viewport is narrower, where horizontal scrolling is required.
        width = max(event.width, self.content.winfo_reqwidth())
        self.canvas.itemconfigure(self.content_window, width=width)

    def _mouse_wheel(self, event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return None
        units = -max(1, abs(delta) // 120) if delta > 0 else max(
            1, abs(delta) // 120)
        if getattr(event, "state", 0) & 0x0001:
            self.canvas.xview_scroll(units, "units")
        else:
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _focus_changed(self, event):
        self.root.after_idle(lambda: self._show_widget(event.widget))

    def _show_widget(self, widget):
        """Scroll just enough to reveal a focused descendant control."""
        x = y = 0
        current = widget
        try:
            while current is not self.content:
                x += current.winfo_x()
                y += current.winfo_y()
                current = current.master
                if current is None:
                    return
            width = widget.winfo_width()
            height = widget.winfo_height()
            content_width = max(1, self.content.winfo_width())
            content_height = max(1, self.content.winfo_height())
            left = self.canvas.canvasx(0)
            top = self.canvas.canvasy(0)
            right = left + self.canvas.winfo_width()
            bottom = top + self.canvas.winfo_height()
            if x < left:
                self.canvas.xview_moveto(x / content_width)
            elif x + width > right:
                self.canvas.xview_moveto(
                    max(0, x + width - self.canvas.winfo_width()) /
                    content_width)
            if y < top:
                self.canvas.yview_moveto(y / content_height)
            elif y + height > bottom:
                self.canvas.yview_moveto(
                    max(0, y + height - self.canvas.winfo_height()) /
                    content_height)
        except (AttributeError, tk.TclError):
            # Focus can move while a dialog is being destroyed.
            return


class DictationIndicator:
    """Small click-through overlay driven safely from any application thread."""

    _STATES = {
        "loading": ("Preparing speech model\u2026", "#6aa9ff"),
        "connecting": ("Connecting microphone\u2026", "#6aa9ff"),
        "listening": ("Listening\u2026", "#ff5a5f"),
        "transcribing": ("Transcribing\u2026", "#ffb340"),
        "no_speech": ("No speech detected \u2014 try again", "#ffb340"),
        "no_text": ("No text recognized \u2014 try again", "#ffb340"),
        "not_ready": ("Microphone was not ready \u2014 try again", "#ffb340"),
    }

    def __init__(self):
        self._commands = queue.Queue()
        self._thread = None
        self._thread_lock = threading.Lock()
        self._command_generation = 0
        self._closed = False

    def show(self, state):
        if state not in self._STATES or self._closed:
            return
        self._ensure_thread()
        with self._thread_lock:
            if self._closed:
                return
            self._command_generation += 1
            self._commands.put(state)

    def show_temporary(self, state, seconds):
        """Show a result briefly without letting its timer hide a newer state."""
        if state not in self._STATES or self._closed or seconds <= 0:
            return
        self._ensure_thread()
        with self._thread_lock:
            if self._closed:
                return
            self._command_generation += 1
            generation = self._command_generation
            self._commands.put(state)
        timer = threading.Timer(
            seconds, self._hide_if_current, args=(generation,))
        timer.daemon = True
        timer.start()

    def _hide_if_current(self, generation):
        with self._thread_lock:
            if self._closed or generation != self._command_generation:
                return
            self._command_generation += 1
            self._commands.put("hide")

    def hide(self):
        with self._thread_lock:
            if self._thread is None or self._closed:
                return
            self._command_generation += 1
            self._commands.put("hide")

    def close(self):
        with self._thread_lock:
            self._closed = True
            self._command_generation += 1
            running = self._thread is not None
            if running:
                self._commands.put("close")

    def _ensure_thread(self):
        with self._thread_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="presspeech-indicator", daemon=True)
                self._thread.start()

    @staticmethod
    def _work_area():
        """Return the work area of the monitor containing the active window."""
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        user32.MonitorFromWindow.restype = ctypes.c_void_p

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        hwnd = user32.GetForegroundWindow()
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.rcWork
        return RECT(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))

    def _run(self):
        try:
            root = tk.Tk()
            root.withdraw()
            root.title("Presspeech Indicator")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            # The indicator is a separate Tk interpreter from the interactive
            # window host. Install its accessibility proxy as well so its
            # changing status label has a useful UI Automation name.
            try:
                tk_uia.enable(root)
            except Exception:
                _accessibility_failed()

            pixels_per_inch = root.winfo_fpixels("1i")
            horizontal_padding = _scaled_pixels(12, pixels_per_inch)
            vertical_padding = _scaled_pixels(7, pixels_per_inch)

            frame = tk.Frame(
                root, bg="#202124", padx=horizontal_padding,
                pady=vertical_padding)
            frame.pack(fill="both", expand=True)
            dot = tk.Label(frame, text="\u25cf", bg="#202124", fg="#ff5a5f",
                           font=("Segoe UI", 11))
            dot.pack(side="left")
            label = tk.Label(frame, text="Listening\u2026", bg="#202124", fg="#ffffff",
                             font=("Segoe UI", 10, "bold"),
                             padx=_scaled_pixels(7, pixels_per_inch))
            label.pack(side="left")
            root.update_idletasks()
            _mark_live_region(label)

            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            client_hwnd = root.winfo_id()
            hwnd = user32.GetParent(client_hwnd) or client_hwnd
            get_window_long = user32.GetWindowLongPtrW
            set_window_long = user32.SetWindowLongPtrW
            get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            get_window_long.restype = ctypes.c_ssize_t
            set_window_long.restype = ctypes.c_ssize_t
            ex_style = get_window_long(hwnd, -20)  # GWL_EXSTYLE
            ex_style |= 0x00000080  # WS_EX_TOOLWINDOW
            ex_style |= 0x00000020  # WS_EX_TRANSPARENT (click-through)
            ex_style |= 0x08000000  # WS_EX_NOACTIVATE
            set_window_long(hwnd, -20, ex_style)
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_uint,
            ]

            minimum_width = _scaled_pixels(224, pixels_per_inch)
            minimum_height = _scaled_pixels(38, pixels_per_inch)
            bottom_offset = _scaled_pixels(42, pixels_per_inch)
            visible_state = None
            current_palette = None
            current_text_scale = None

            def apply_palette(state, force=False):
                nonlocal current_palette
                system_palette = _indicator_system_palette(user32)
                if system_palette is None:
                    background = "#202124"
                    foreground = "#ffffff"
                    accent = self._STATES[state][1]
                    opacity = 0.94
                else:
                    background, foreground = system_palette
                    accent = foreground
                    # Transparency blends user-selected colours with arbitrary
                    # content and can destroy their intended contrast ratio.
                    opacity = 1.0
                palette = (background, foreground, accent, opacity)
                if not force and palette == current_palette:
                    return
                current_palette = palette
                root.configure(bg=background)
                frame.configure(bg=background)
                dot.configure(bg=background, fg=accent)
                label.configure(bg=background, fg=foreground)
                root.attributes("-alpha", opacity)

            def apply_text_scale(force=False):
                nonlocal current_text_scale
                text_scale = _windows_text_scale()
                if not force and text_scale == current_text_scale:
                    return False
                current_text_scale = text_scale
                dot.configure(font=(
                    "Segoe UI", _scaled_font_points(11, text_scale)))
                label.configure(font=(
                    "Segoe UI", _scaled_font_points(10, text_scale), "bold"))
                return True

            def position_visible_indicator():
                root.update_idletasks()
                width = max(minimum_width, frame.winfo_reqwidth())
                height = max(minimum_height, frame.winfo_reqheight())
                area = self._work_area()
                x = area.left + ((area.right - area.left - width) // 2)
                y = area.bottom - height - bottom_offset
                user32.SetWindowPos(
                    hwnd, ctypes.c_void_p(-1), x, y, width, height,
                    0x0010 | 0x0040,  # SWP_NOACTIVATE | SWP_SHOWWINDOW
                )

            def apply_command(command):
                nonlocal visible_state
                if command == "close":
                    root.destroy()
                    return False
                if command == "hide":
                    visible_state = None
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    return True
                changed = visible_state != command
                # Notify after the overlay is visible; screen readers may
                # discard live-region events from a hidden window.
                _set_accessible_text(
                    label, self._STATES[command][0], announce=False)
                # Refresh even if the colours are otherwise unchanged because
                # the default accent is specific to the current state.
                apply_palette(command, force=True)
                apply_text_scale(force=True)
                visible_state = command
                position_visible_indicator()
                if changed and getattr(label, "_presspeech_live_region", False):
                    try:
                        _LIVE_REGIONS.announce(label.winfo_id())
                    except Exception:
                        _accessibility_failed()
                return True

            def poll():
                command = None
                try:
                    while True:
                        command = self._commands.get_nowait()
                except queue.Empty:
                    pass
                if command is not None and not apply_command(command):
                    return
                root.after(25, poll)

            def refresh_accessibility_settings():
                if visible_state is not None:
                    # Contrast themes can be toggled while a two-minute
                    # recording is active, and Text size is independent from
                    # display DPI. Keep both in sync without requiring another
                    # dictation state transition or an app restart.
                    apply_palette(visible_state)
                    if apply_text_scale():
                        position_visible_indicator()
                root.after(250, refresh_accessibility_settings)

            root.after(0, poll)
            root.after(250, refresh_accessibility_settings)
            root.mainloop()
        except Exception:
            # Dictation must remain usable even if Windows refuses the overlay.
            return


class SetupWindow:
    """Small first-run readiness screen; all processing remains local."""

    def __init__(self, app):
        self.app = app
        self.root = None
        self.microphone_events = queue.Queue()
        self.microphone_checking = False
        _window_host().submit(self._build)

    def _build(self):
        root = _interactive_window("Welcome to Presspeech")
        self.root = root
        self._setup_interacted = False
        self._initial_focus_pending = True
        self._microphone_busy_feedback = False
        root.resizable(True, True)
        root.lift()
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))
        # If the model cache check finishes while Setup is open, do not steal
        # focus after the user has started navigating or configuring devices.
        root.bind("<KeyPress>", self._mark_setup_interacted, add="+")
        root.bind("<ButtonPress>", self._mark_setup_interacted, add="+")
        root.bind("<MouseWheel>", self._mark_setup_interacted, add="+")
        root.bind("<FocusIn>", self._mark_setup_focus_interacted, add="+")
        self.scrollable_body = _ScrollableDialogBody(root, padding=20)
        frame = self.scrollable_body.content

        ttk.Label(frame, text="Presspeech is almost ready",
                  font=("Segoe UI", 16, "bold")).grid(
                      row=0, column=0, columnspan=2, sticky="w")
        self.instructions = ttk.Label(
            frame,
            text=self._dictation_instructions(),
            justify="left",
            wraplength=560,
        )
        self.instructions.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))

        ttk.Label(frame, text="Speech model").grid(row=2, column=0, sticky="w")
        self.model_label = ttk.Label(frame, text="Preparing…")
        self.model_label.grid(row=2, column=1, sticky="w", padx=(12, 0))
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=260)
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 13))
        self.progress.start(12)
        self._progress_active = True
        ttk.Label(
            frame,
            text=("Presspeech fetches pinned speech-model files from "
                  "Hugging Face. Setup asks before downloading missing "
                  "first-run model files, including the English-only CPU "
                  "default. Stay online while "
                  "it prepares; speech is processed on this PC. Without "
                  "usable NVIDIA CUDA, a fresh install uses English-only "
                  "Whisper base.en on CPU (~141 MiB); there is currently no "
                  "multilingual CPU model. Multilingual Parakeet and Whisper "
                  "turbo require a supported NVIDIA GPU."),
            justify="left",
            wraplength=560,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))

        # A first-run model choice gates dictation and may approve a large
        # download, so keep it beside model status and ahead of optional
        # microphone setup in both visual and keyboard traversal order.
        self.model_consent_frame = ttk.Frame(frame)
        self.model_consent_frame.grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        self.model_consent_label = ttk.Label(
            self.model_consent_frame,
            text=("A full multilingual Parakeet model download is about 2.5 GB "
                  "from huggingface.co; a partial local cache may need less. "
                  + MODEL_DOWNLOAD_PRIVACY_NOTICE + " Or choose English-only "
                  "Whisper base.en on CPU (~141 MiB)."),
            justify="left",
            wraplength=560,
        )
        self.model_consent_label.pack(anchor="w")
        self.download_model_button = ttk.Button(
            self.model_consent_frame,
            text="Download Parakeet model (up to ~2.5 GB)",
            command=self.app.confirm_initial_model_download,
            state="disabled",
        )
        self.download_model_button.pack(anchor="w", pady=(6, 0))
        self.cpu_model_button = ttk.Button(
            self.model_consent_frame,
            text="Select and download English-only CPU model (~141 MiB)",
            command=self.app.select_cpu_model_after_download_declined,
            state="disabled",
        )
        self.cpu_model_button.pack(anchor="w", pady=(4, 0))
        self.other_model_button = ttk.Button(
            self.model_consent_frame,
            text="Choose another model in Settings…",
            command=self.app.open_settings,
            state="disabled",
        )
        self.other_model_button.pack(anchor="w", pady=(4, 0))
        self.model_consent_frame.grid_remove()

        microphone_label = ttk.Label(frame, text="Microphone")
        microphone_label.grid(row=6, column=0, sticky="w")
        options = self.app.input_device_options()
        self.device_values = {label: value for label, value in options}
        current = self.app.settings.get("input_device", cfg.DEFAULTS["input_device"])
        selected = next((label for label, value in options if value == current),
                        options[0][0])
        self.device = ttk.Combobox(
            frame, values=[label for label, _value in options],
            state="readonly", width=48)
        self.device.set(selected)
        self.device.grid(row=6, column=1, sticky="w", padx=(12, 0), pady=3)
        self.device.bind(
            "<KeyPress>", self._mark_setup_interacted, add="+")
        self.device.bind("<<ComboboxSelected>>", self._microphone_changed)

        microphone_check_label = ttk.Label(frame, text="Microphone check")
        microphone_check_label.grid(row=7, column=0, sticky="w", pady=(5, 3))
        microphone_check = ttk.Frame(frame)
        microphone_check.grid(row=7, column=1, sticky="ew", padx=(12, 0), pady=(5, 3))
        self.microphone_status = ttk.Label(microphone_check, text="Not checked")
        self.microphone_status.pack(side="left")
        self.check_microphone_button = ttk.Button(
            microphone_check,
            text="Check Microphone",
            command=self._check_microphone,
        )
        self.check_microphone_button.pack(side="right", padx=(12, 0))

        ttk.Label(
            frame,
            text=("The microphone check is optional: you can finish Setup "
                  "without running it; select or connect a microphone in "
                  "Settings later. The local microphone check opens the "
                  "selected input only when you choose Check Microphone; "
                  "Windows may show its microphone-use "
                  "indicator. On some Windows 11 builds, the first check may "
                  "also show a Windows microphone-permission prompt; approve "
                  "it only if you want to run the check. Audio samples are "
                  "used only to measure input "
                  "level in memory, then discarded — they are not saved, "
                  "sent, or transcribed. Speak while the check runs. If it "
                  "fails, enable Microphone "
                  "access, Let apps access your microphone, and Let desktop "
                  "apps access your microphone. Some Windows 11 builds also "
                  "offer per-app microphone access for desktop apps; if that "
                  "control appears, allow Presspeech there too. If Windows "
                  "says these settings "
                  "are managed by your organization, contact your administrator; "
                  "Presspeech cannot override that policy. Inputs with the "
                  "same name under one audio system cannot be selected "
                  "individually; disconnect one to choose the other."),
            justify="left",
            wraplength=560,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 3))
        microphone_actions = ttk.Frame(frame)
        microphone_actions.grid(row=9, column=0, columnspan=2, sticky="w")
        privacy_button = ttk.Button(
            microphone_actions, text="Open Microphone Privacy Settings",
            command=self.app.open_microphone_privacy_settings,
        )
        privacy_button.pack(side="left")
        sound_button = ttk.Button(
            microphone_actions, text="Open Sound Input Settings",
            command=self.app.open_default_input_settings,
        )
        sound_button.pack(side="left", padx=(8, 0))

        hotkey_label = ttk.Label(frame, text="Dictation hotkey")
        hotkey_label.grid(row=10, column=0, sticky="w")
        self.hotkey = ttk.Combobox(
            frame, values=cfg.HOTKEYS, state="readonly", width=18)
        self.hotkey.set(self.app.settings.get("hotkey", cfg.DEFAULTS["hotkey"]))
        self.hotkey.grid(row=10, column=1, sticky="w", padx=(12, 0), pady=3)
        self.hotkey.bind(
            "<KeyPress>", self._mark_setup_interacted, add="+")
        self.hotkey.bind("<<ComboboxSelected>>", self._hotkey_changed)
        ttk.Label(
            frame,
            text=ALTGR_HOTKEY_GUIDANCE,
            justify="left",
            wraplength=560,
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(3, 8))

        trigger_label = ttk.Label(frame, text="Dictation style")
        trigger_label.grid(row=12, column=0, sticky="w", pady=(0, 6))
        trigger_options = ttk.Frame(frame)
        trigger_options.grid(
            row=12, column=1, sticky="w", padx=(12, 0), pady=(0, 6))
        self.trigger = tk.StringVar(
            value=self.app.settings.get("trigger", cfg.DEFAULTS["trigger"]))
        hold_trigger = ttk.Radiobutton(
            trigger_options, text="Hold to talk", value="hold",
            variable=self.trigger, command=self._trigger_changed)
        hold_trigger.pack(side="left")
        toggle_trigger = ttk.Radiobutton(
            trigger_options, text="Press to toggle", value="toggle",
            variable=self.trigger, command=self._trigger_changed)
        toggle_trigger.pack(side="left", padx=(12, 0))

        hotkey_status_label = ttk.Label(frame, text="Global hotkey status")
        hotkey_status_label.grid(row=13, column=0, sticky="w", pady=(0, 6))
        hotkey_actions = ttk.Frame(frame)
        hotkey_actions.grid(row=13, column=1, sticky="ew", padx=(12, 0), pady=(0, 6))
        self.hotkey_status = ttk.Label(hotkey_actions, text="Starting\u2026")
        self.hotkey_status.pack(side="left")
        self.repair_hotkey_button = ttk.Button(
            hotkey_actions, text="Repair Global Hotkey",
            command=self.app.repair_hotkey)
        self.repair_hotkey_button.pack(side="right", padx=(12, 0))

        self.autostart = tk.BooleanVar(
            value=self.app.settings.get("autostart", cfg.DEFAULTS["autostart"]))
        ttk.Checkbutton(frame, text="Start Presspeech with Windows",
                        variable=self.autostart).grid(
                            row=14, column=0, columnspan=2, sticky="w", pady=(2, 6))

        startup_actions = ttk.Frame(frame)
        startup_actions.grid(row=15, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        self.autostart_status = ttk.Label(startup_actions, text="")
        self.autostart_status.pack(side="left")
        startup_button = ttk.Button(
            startup_actions, text="Open Startup Settings",
            command=self.app.open_startup_settings,
        )
        startup_button.pack(side="right")

        buttons = ttk.Frame(frame)
        buttons.grid(row=16, column=0, columnspan=2, sticky="ew")
        self.try_button = ttk.Button(
            buttons, text="Try Dictation", command=self.app.open_scratchpad,
            state="disabled")
        self.try_button.pack(side="left")
        self.retry_button = ttk.Button(
            buttons, text="Retry Speech Model", command=self._retry_model,
            state="disabled")
        self.retry_button.pack(side="left", padx=(8, 0))
        # Tk traverses controls in creation order. Create the secondary action
        # before the primary action so Tab follows the visible left-to-right
        # order (Try, Retry, Later, Finish). Pack Finish first only because
        # successive side="right" widgets are laid out right-to-left.
        self.later_button = ttk.Button(
            buttons, text="Set Up Later", command=self._defer)
        self.finish_button = ttk.Button(
            buttons, text="Finish Setup", command=self._finish,
            state="disabled")
        self.finish_button.pack(side="right")
        self.later_button.pack(side="right", padx=(0, 8))

        root.protocol("WM_DELETE_WINDOW", self._defer)
        for button, key in (
                (self.check_microphone_button, "c"),
                (self.repair_hotkey_button, "h"),
                (privacy_button, "p"),
                (sound_button, "s"),
                (startup_button, "o"),
                (self.try_button, "t"),
                (self.retry_button, "r"),
                (self.download_model_button, "d"),
                (self.cpu_model_button, "u"),
                (self.other_model_button, "m"),
                (self.finish_button, "f"),
                (self.later_button, "l")):
            root.bind(
                "<Alt-KeyPress-%s>" % key,
                self._mark_setup_interacted,
                add="+",
            )
            _add_access_key(root, button, key)
        _bind_window_command(root, "<Escape>", self._defer)
        root.update_idletasks()
        _label_control(microphone_label, self.device)
        _label_control(hotkey_label, self.hotkey)
        _label_trigger_choices(trigger_label, hold_trigger, toggle_trigger)
        # Keep the changing status text as its accessible name. Linking this
        # live region to the static caption would mask its updates.
        _name_control(
            self.hotkey, "Dictation hotkey. " + ALTGR_HOTKEY_GUIDANCE)
        _describe_control(
            self.download_model_button, MODEL_DOWNLOAD_ACCESSIBLE_DESCRIPTION)
        _describe_control(
            self.cpu_model_button, MODEL_DOWNLOAD_ACCESSIBLE_DESCRIPTION)
        for status in (
                self.model_label, self.microphone_status,
                self.hotkey_status, self.autostart_status):
            _mark_live_region(status)
        self.scrollable_body.fit_to_screen()
        self._poll_model()
        root.after_idle(self._focus_initial_setup_control)

    def _mark_setup_interacted(self, _event=None):
        """Remember user navigation before asynchronous readiness changes."""
        self._setup_interacted = True

    def _mark_setup_focus_interacted(self, event=None):
        """Count focus movement as input, except the chosen initial target."""
        if getattr(self, "_initial_focus_pending", False):
            return
        widget = getattr(event, "widget", None)
        if (widget is None or widget is self.root or
                widget is getattr(self, "_initial_focus_widget", None)):
            return
        try:
            if self.root.focus_get() is not widget:
                return
        except (AttributeError, tk.TclError):
            return
        self._mark_setup_interacted(event)

    def _focus_initial_setup_control(self):
        """Start at the required model choice when first-run consent is needed."""
        if self.root is None:
            return
        if self._setup_interacted:
            self._initial_focus_pending = False
            return
        if (getattr(self.app, "model_status", "pending") ==
                "awaiting_download_consent"):
            target = self.download_model_button
        else:
            target = self.device
        self._initial_focus_widget = target
        self._initial_focus_pending = False
        target.focus_set()

    def _poll_model(self):
        if self.root is None:
            return
        self._poll_microphone_events()
        status = getattr(self.app, "model_status", "pending")
        previous_status = getattr(self, "_last_model_status", None)
        detail = getattr(self.app, "model_status_detail", "")
        model_name = self.app.settings.get("model", cfg.DEFAULTS["model"])
        consent_detail = detail or (
            "English-only Whisper base.en model download is about 141 MiB"
            if model_name == "base.en" else
            "Full Parakeet model download is about 2.5 GB")
        labels = {
            "pending": "Waiting to start…",
            "awaiting_download_consent": (
                "Needs your choice — " + consent_detail),
            "ready": "Ready" + ((" — " + detail) if detail else ""),
            "error": "Needs attention" + ((" — " + detail) if detail else ""),
        }
        if status == "loading":
            model_text, model_phase = _model_loading_feedback(self.app)
        else:
            model_text = labels.get(status, detail or status)
            model_phase = status
        previous_phase = getattr(self, "_last_model_phase", None)
        if previous_phase == model_phase:
            _set_accessible_text(self.model_label, model_text, announce=False)
        else:
            _set_accessible_text(self.model_label, model_text)
            self._last_model_phase = model_phase
        consent_required = status == "awaiting_download_consent"
        self._last_model_status = status
        # If readiness changes make a choice unavailable while focused, move
        # to the first live follow-on control instead of leaving focus on a
        # disabled button.
        for button in (
                self.download_model_button, self.cpu_model_button,
                self.other_model_button):
            _set_control_state(
                self.root, button,
                ("normal" if consent_required and
                 (button is not self.cpu_model_button or
                  model_name != "base.en") else "disabled"), self.device)
        if consent_required:
            if model_name == "base.en":
                _set_accessible_text(
                    self.model_consent_label,
                    "The English-only Whisper base.en speech model is about "
                    "141 MiB. Choose Download to fetch its pinned files from "
                    "huggingface.co. " + MODEL_DOWNLOAD_PRIVACY_NOTICE,
                    announce=False)
                _set_accessible_text(
                    self.download_model_button,
                    "Download English-only CPU model (~141 MiB)",
                    announce=False)
                _name_control(
                    self.download_model_button,
                    "Download the English-only CPU Whisper base.en model, "
                    "about 141 MiB. This requests pinned model files from "
                    "huggingface.co.")
                self.cpu_model_button.pack_forget()
            else:
                _set_accessible_text(
                    self.model_consent_label,
                    "A full multilingual Parakeet model download is about "
                    "2.5 GB from huggingface.co; a partial local cache may "
                    "need less. " + MODEL_DOWNLOAD_PRIVACY_NOTICE + " Or choose "
                    "English-only Whisper base.en on CPU (~141 MiB).",
                    announce=False)
                _set_accessible_text(
                    self.download_model_button,
                    "Download Parakeet model (up to ~2.5 GB)",
                    announce=False)
                _name_control(
                    self.download_model_button,
                    "Download the multilingual Parakeet model, up to about "
                    "2.5 GB.")
                if self.cpu_model_button.winfo_manager() != "pack":
                    self.cpu_model_button.pack(
                        before=self.other_model_button,
                        anchor="w", pady=(4, 0))
            self.model_consent_frame.grid()
        else:
            self.model_consent_frame.grid_remove()
        if (consent_required and previous_status != status and
                not getattr(self, "_initial_focus_pending", False) and
                not getattr(self, "_setup_interacted", False)):
            try:
                focused = self.root.focus_get()
            except (AttributeError, tk.TclError):
                focused = None
            if focused is None or focused is getattr(self, "device", None):
                # A slow local-cache check can reach consent after Setup opens.
                # Bring the required choice into focus only if the user has
                # not begun navigating the microphone or other controls.
                self.download_model_button.focus_set()
        hotkey_state, hotkey_detail = _hotkey_readiness(self.app)
        _set_accessible_text(self.hotkey_status, hotkey_detail)
        self.repair_hotkey_button.config(state="normal")
        capture_busy = bool(_settings_save_block_reason(self.app))
        if (not capture_busy and
                getattr(self, "_microphone_busy_feedback", False)):
            self._microphone_busy_feedback = False
            _set_accessible_text(
                self.microphone_status,
                "You can now choose Check Microphone to test the selected input.")
        _set_control_state(
            self.root, self.device,
            "disabled" if capture_busy else "readonly", self.later_button)
        _set_control_state(
            self.root, self.check_microphone_button,
            ("disabled" if capture_busy or self.microphone_checking else
             "normal"), self.later_button)
        _set_control_state(
            self.root, self.retry_button,
            "normal" if status == "error" else "disabled", self.later_button)
        _set_control_state(
            self.root, self.try_button,
            ("normal" if status == "ready" and not self.microphone_checking
             and getattr(self.app, "_microphone_check_in_progress", False) is not True
             else "disabled"), self.later_button)
        _set_control_state(
            self.root, self.finish_button,
            ("normal" if status == "ready" and
             hotkey_state == "ready" else "disabled"), self.later_button)
        if status in ("ready", "error", "awaiting_download_consent"):
            if getattr(self, "_progress_active", False):
                self.progress.stop()
                self._progress_active = False
            self.progress.config(mode="determinate", value=100 if status == "ready" else 0)
        else:
            # Hub reports progress for individual file transfers, while the
            # number and sizes of missing files vary with the local cache.
            # Keep the overall model indicator indeterminate rather than
            # showing a per-file value as whole-model completion.
            self.progress.config(mode="indeterminate")
            if not getattr(self, "_progress_active", False):
                # A failed load stops the animation above. Retry publishes
                # loading before its worker runs, so visibly resume preparation
                # as soon as the next poll observes that transition.
                self.progress.start(12)
                self._progress_active = True
        self.root.after(300, self._poll_model)

    def _microphone_changed(self, _event=None):
        self._mark_setup_interacted(_event)
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        settings = self.app.settings
        with self.app.lock:
            configured = settings.get("input_device", cfg.DEFAULTS["input_device"])
            if selected == configured:
                return
            if _settings_save_block_reason(self.app):
                # A picker event can race the poll that disables it.
                label = next(
                    (label for label, value in self.device_values.items()
                     if value == configured), None)
                if label is not None:
                    self.device.set(label)
                blocked = True
            else:
                settings["input_device"] = selected
                self.app.input_device = None
                self.app._cached_input_selector = None
                self.app._cached_input_topology = None
                cfg.save(settings)
                blocked = False
        self._microphone_busy_feedback = blocked
        _set_accessible_text(
            self.microphone_status,
            ("Finish or cancel the current dictation before changing microphones."
             if blocked else "Not checked"))

    def _dictation_instructions(self):
        hotkey = self.app.settings.get("hotkey", cfg.DEFAULTS["hotkey"]).title()
        ready = ("wait for the start cue (if enabled) to finish and the "
                 "Listening… status (if shown)")
        if self.app.settings.get("trigger", cfg.DEFAULTS["trigger"]) == "toggle":
            action = (
                "Press %s to start, %s, speak, then press it again to type "
                "at the cursor." % (hotkey, ready)
            )
        else:
            action = ("Hold %s, %s, speak, then release to type at the cursor."
                      % (hotkey, ready))
        return (action + "\nFirst setup may take time while the speech model "
                "downloads and loads. Speech stays on this PC; no audio or "
                "transcripts are uploaded.\nWindows 11 also includes Voice "
                "Access for on-device, offline voice control and dictation. "
                "Presspeech focuses on hotkey-driven dictation that inserts "
                "text at your cursor.")

    def _hotkey_changed(self, _event=None):
        self._mark_setup_interacted(_event)
        selected = self.hotkey.get()
        if selected not in cfg.HOTKEYS:
            self.hotkey.set(
                self.app.settings.get("hotkey", cfg.DEFAULTS["hotkey"]))
            return
        if selected == self.app.settings.get("hotkey", cfg.DEFAULTS["hotkey"]):
            return
        # Persist immediately so Set Up Later, a model download failure, or a
        # restart cannot strand an AltGr-layout user on the unusable default.
        self.app.settings["hotkey"] = selected
        cfg.save(self.app.settings)
        _set_accessible_text(self.instructions, self._dictation_instructions())

    def _trigger_changed(self):
        self._mark_setup_interacted()
        selected = self.trigger.get()
        if selected not in ("hold", "toggle"):
            self.trigger.set(
                self.app.settings.get("trigger", cfg.DEFAULTS["trigger"]))
            return
        if selected == self.app.settings.get("trigger", cfg.DEFAULTS["trigger"]):
            return
        # Apply before Try Dictation and persist before Set Up Later. The
        # listener reads this setting for each new hotkey transaction, while
        # an already-held key keeps the mode captured when it was pressed.
        self.app.settings["trigger"] = selected
        cfg.save(self.app.settings)
        _set_accessible_text(self.instructions, self._dictation_instructions())

    def _check_microphone(self):
        if self.microphone_checking or self.root is None:
            return
        with self.app.lock:
            if _settings_save_block_reason(self.app):
                self._microphone_busy_feedback = True
                _set_accessible_text(
                    self.microphone_status,
                    "Finish or cancel the current dictation before checking the microphone.")
                return
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        self._microphone_busy_feedback = False
        self.microphone_checking = True
        _set_control_state(
            self.root, self.check_microphone_button, "disabled", self.device)
        _set_accessible_text(
            self.microphone_status,
            "Connecting microphone… Wait for Listening before speaking.")
        threading.Thread(
            target=self._check_microphone_worker,
            args=(selected,),
            name="presspeech-microphone-check",
            daemon=True,
        ).start()

    def _check_microphone_worker(self, selected):
        def listening():
            self.microphone_events.put((selected, "listening", None))

        try:
            result = self.app.check_input_device(
                selected, on_listening=listening)
        except Exception:
            # The app normally converts audio-backend failures to "unavailable".
            # Keep the asynchronous UI state recoverable if an unexpected
            # exception escapes that boundary; never surface driver details.
            result = "check_error"
        # Query again after the check: it may have refreshed PortAudio after a
        # reconnect. Do this on the worker so a slow driver never blocks Tk.
        options = None
        if result != "busy":
            try:
                options = self.app.input_device_options()
            except Exception:
                pass
        self.microphone_events.put((selected, result, options))

    def _refresh_microphone_options(self, options, selected):
        """Replace picker choices while retaining the user's stable selector."""
        if not options:
            return
        values = {label: value for label, value in options}
        selected_label = next(
            (label for label, value in options if value == selected),
            None,
        )
        # A concurrent selection change can beat this worker result. Retain the
        # current picker rather than visually falling back to Automatic; the
        # queued follow-up check will provide choices for the new selection.
        if selected_label is None:
            return
        self.device_values = values
        self.device.config(values=list(values))
        self.device.set(selected_label)

    def _poll_microphone_events(self):
        latest = None
        try:
            while True:
                latest = self.microphone_events.get_nowait()
        except queue.Empty:
            pass
        if latest is None:
            return
        selected, result, options = latest
        if result == "listening":
            current = self.device_values.get(
                self.device.get(), cfg.DEFAULTS["input_device"])
            if self.microphone_checking and selected == current:
                _set_accessible_text(
                    self.microphone_status, "Listening — speak a few words…")
            return
        self.microphone_checking = False
        _set_control_state(
            self.root, self.check_microphone_button,
            ("disabled" if _settings_save_block_reason(self.app) else
             "normal"), self.later_button)
        current = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        self._refresh_microphone_options(options, current)
        current = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        if selected != current:
            # The user selected a different device while the previous,
            # explicitly requested check was running. Do not open the new
            # device until they choose Check Microphone for it.
            _set_accessible_text(self.microphone_status, "Not checked")
            return
        if result == "level":
            text = "Ready — input level detected"
        elif result == "silent":
            text = (
                "Connected, but no input level detected — unmute and "
                "choose Check Microphone again")
        elif result == "check_error":
            text = "Microphone check failed — choose Check Microphone to retry"
        elif result == "busy":
            self._microphone_busy_feedback = True
            text = (
                "Microphone check postponed — finish or cancel dictation, "
                "then choose Check Microphone")
        else:
            text = "Needs attention — microphone could not be opened"
        if result != "busy":
            self._microphone_busy_feedback = False
        _set_accessible_text(self.microphone_status, text)

    def _retry_model(self):
        self.app.retry_model()

    def _finish(self):
        # setup_complete means the app has reached a usable speech-model state.
        # A microphone may deliberately be connected later, but dismissing a
        # pending or failed model would hide the guided retry path on restart.
        hotkey_state, _hotkey_detail = _hotkey_readiness(self.app)
        if (getattr(self.app, "model_status", "pending") != "ready" or
                hotkey_state != "ready"):
            _set_control_state(
                self.root, self.finish_button, "disabled", self.device)
            return
        with self.app.lock:
            settings = self.app.settings
            selected = self.device_values.get(
                self.device.get(), cfg.DEFAULTS["input_device"])
            if (selected != settings.get("input_device", cfg.DEFAULTS["input_device"])
                    and _settings_save_block_reason(self.app)):
                self._microphone_busy_feedback = True
                _set_accessible_text(
                    self.microphone_status,
                    "Finish or cancel the current dictation before changing microphones.")
                return
            if selected != settings.get("input_device", cfg.DEFAULTS["input_device"]):
                self.app.input_device = None
                self.app._cached_input_selector = None
                self.app._cached_input_topology = None
            settings["input_device"] = selected
            settings["autostart"] = bool(self.autostart.get())
            settings["setup_complete"] = True
            cfg.save(settings)
        if self.app.apply_autostart():
            self._close()
        else:
            _set_accessible_text(
                self.autostart_status,
                "Setup is complete, but Start with Windows was not updated.")

    def _defer(self):
        """Keep first-run choices without claiming setup is complete."""
        with self.app.lock:
            settings = self.app.settings
            selected = self.device_values.get(
                self.device.get(), cfg.DEFAULTS["input_device"])
            if (selected != settings.get("input_device", cfg.DEFAULTS["input_device"])
                    and _settings_save_block_reason(self.app)):
                self._microphone_busy_feedback = True
                _set_accessible_text(
                    self.microphone_status,
                    "Finish or cancel the current dictation before changing microphones.")
                return
            if selected != settings.get("input_device", cfg.DEFAULTS["input_device"]):
                self.app.input_device = None
                self.app._cached_input_selector = None
                self.app._cached_input_topology = None
            settings["input_device"] = selected
            settings["autostart"] = bool(self.autostart.get())
            cfg.save(settings)
        if self.app.apply_autostart():
            self._close()
        else:
            _set_accessible_text(
                self.autostart_status,
                "Setup is still open, but Start with Windows was not updated. "
                "Open Startup Settings or turn it off, then choose Set Up "
                "Later again.",
            )

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app.setup_window = None


def _release_notes_for_display(body):
    """Render bounded GitHub Markdown as inert, readable update text."""
    if not isinstance(body, str):
        body = ""
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in body.split("\n"):
        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        # Keep link labels but not active URLs. The update dialog is a plain
        # text surface; users can consult the release page separately.
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\*\*(.*?)\*\*|__(.*?)__",
                      lambda match: match.group(1) or match.group(2), line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        lines.append(line)
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(No release notes available.)"
    max_chars = 8000
    if len(rendered) > max_chars:
        suffix = "\n\n[Release notes shortened]"
        rendered = rendered[:max_chars - len(suffix)].rstrip() + suffix
    return rendered


class UpdateWindow:
    """Explicit, verified Windows update download and install prompt."""

    def __init__(self, app, update):
        self.app = app
        self.update = update
        self.root = None
        self.events = queue.Queue()
        self.cancel_download = threading.Event()
        self.download_lock = threading.Lock()
        self.downloaded_installer = None
        self.active_download_directory = None
        self.active_staging_path = None
        self.download_finished = threading.Event()
        self.download_finished.set()
        _window_host().submit(self._build)

    def _build(self):
        root = _interactive_window("Presspeech Update")
        self.root = root
        root.resizable(True, True)
        self.scrollable_body = _ScrollableDialogBody(root, padding=18)
        frame = self.scrollable_body.content
        ttk.Label(frame, text="Presspeech %s is available" % self.update["version"],
                  font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text=("Review the release notes before deciding. The installer is "
                  "downloaded only if you approve it; its size and SHA-256 "
                  "checksum are verified before it runs."),
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(6, 12))
        ttk.Label(
            frame, text="Release notes", font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            frame,
            text=_release_notes_for_display(self.update.get("body", "")),
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(0, 12))
        self.status = ttk.Label(frame, text="Ready to download")
        self.status.pack(anchor="w")
        self.progress = ttk.Progressbar(frame, mode="determinate", length=370)
        self.progress.pack(fill="x", pady=(5, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.later_button = ttk.Button(
            buttons, text="Later", command=self._close)
        self.later_button.pack(side="left")
        self.download_button = ttk.Button(
            buttons, text="Download Update", command=self._download,
            default="active")
        self.download_button.pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, self.later_button, "l")
        _add_access_key(root, self.download_button, "d")
        _bind_window_command(root, "<Escape>", self._close)
        root.update_idletasks()
        _mark_live_region(self.status)
        self.scrollable_body.fit_to_screen()
        root.after_idle(self.download_button.focus_set)
        root.after(100, self._poll)

    def _download(self):
        self._discard_completed_download()
        self.cancel_download.clear()
        self.download_finished.clear()
        _set_control_state(
            self.root, self.download_button, "disabled", self.later_button)
        _set_accessible_text(self.status, "Downloading…")
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        destination = None
        path = None
        try:
            # Isolate each window so a late cleanup from a closed window can
            # never remove a newer window's installer with the same asset name.
            destination = tempfile.mkdtemp(
                prefix=updates.UPDATE_DIRECTORY_PREFIX)
            with self.download_lock:
                self.active_download_directory = destination

            def remember_staging(staging_path):
                with self.download_lock:
                    self.active_staging_path = staging_path

            path = updates.download_update(
                self.update, destination,
                lambda done, total: self.events.put(("progress", done, total)),
                cancelled=self.cancel_download.is_set,
                staging=remember_staging)
            # Closing can race with the final cancellation check inside the
            # downloader. Transfer ownership under a lock so either the open
            # window receives the verified path or the closing window removes
            # it; a completed multi-gigabyte installer must not be orphaned.
            with self.download_lock:
                if self.cancel_download.is_set():
                    discard = True
                else:
                    self.downloaded_installer = path
                    discard = False
            if discard:
                self._remove_downloaded_installer(path)
            else:
                self.events.put(("ready", path))
        except Exception as exc:
            if path is not None:
                self._remove_downloaded_installer(path)
            elif destination is not None:
                try:
                    os.rmdir(destination)
                except OSError:
                    pass
            self.events.put((
                "error",
                updates.user_facing_error(
                    exc, "Could not prepare or download the update.")))
        finally:
            with self.download_lock:
                self.active_download_directory = None
                self.active_staging_path = None
            self.download_finished.set()

    @staticmethod
    def _remove_downloaded_installer(path):
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass

    def _discard_completed_download(self):
        with self.download_lock:
            path = self.downloaded_installer
            self.downloaded_installer = None
        if path is not None:
            self._remove_downloaded_installer(path)

    def cancel_and_cleanup(self):
        """Cancel updater work and hand off cleanup if it stays blocked."""
        self.cancel_download.set()
        self._discard_completed_download()
        if self.download_finished.wait(timeout=1.0):
            return
        with self.download_lock:
            destination = self.active_download_directory
            staging_path = self.active_staging_path
        if staging_path is not None:
            try:
                updates.schedule_abandoned_download_cleanup(staging_path)
            except Exception as exc:
                self.app._log(
                    "could not schedule interrupted update cleanup: %s" %
                    type(exc).__name__)
        elif destination is not None:
            # Before the random staging file is created the private directory
            # is empty, so it can be removed synchronously without recursion.
            try:
                os.rmdir(destination)
            except OSError:
                pass

    def _poll(self):
        if self.root is None:
            return
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    _kind, done, total = event
                    if total:
                        self.progress.config(maximum=total, value=done)
                        _set_accessible_text(
                            self.status, "Downloaded %.1f of %.1f GB" %
                            (done / 1073741824, total / 1073741824),
                            announce=False)
                    else:
                        _set_accessible_text(
                            self.status, "Downloaded %.1f MB" %
                            (done / 1048576), announce=False)
                elif event[0] == "error":
                    _set_accessible_text(self.status, "Download failed")
                    self.download_button.config(state="normal")
                    messagebox.showerror("Update failed", event[1], parent=self.root)
                elif event[0] == "ready":
                    self.progress.config(value=self.progress["maximum"])
                    _set_accessible_text(
                        self.status, "Verified and ready to install")
                    if messagebox.askyesno(
                            "Install update",
                            "Close Presspeech and run the verified installer now?",
                            parent=self.root):
                        try:
                            self.app.launch_update(event[1], self.update)
                        except Exception as exc:
                            self._discard_completed_download()
                            _set_accessible_text(self.status, "Install failed")
                            self.progress.config(value=0)
                            self.download_button.config(state="normal")
                            messagebox.showerror(
                                "Update failed",
                                updates.user_facing_error(
                                    exc, "Could not start the verified installer."),
                                parent=self.root)
                    else:
                        self._discard_completed_download()
                        _set_accessible_text(self.status, "Ready to download")
                        self.progress.config(value=0)
                        self.download_button.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _close(self):
        self.cancel_and_cleanup()
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app.update_window = None


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.root = None
        _window_host().submit(self._build)

    def _build(self):
        s = self.app.settings
        root = _interactive_window("Presspeech Settings")
        self.root = root
        root.resizable(True, True)
        self.scrollable_body = _ScrollableDialogBody(root, padding=12)
        f = self.scrollable_body.content

        row = 0

        hotkey_label = ttk.Label(f, text="Dictation hotkey")
        hotkey_label.grid(row=row, column=0, sticky="w", pady=2)
        self.var_hotkey = ttk.Combobox(f, values=cfg.HOTKEYS, state="readonly", width=14)
        self.var_hotkey.set(s["hotkey"])
        self.var_hotkey.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        self.try_button = ttk.Button(
            f, text="Try Dictation…", command=self.app.open_scratchpad)
        self.try_button.grid(row=row, column=2, sticky="w", padx=(10, 0), pady=2)
        row += 1

        ttk.Label(
            f, text=ALTGR_HOTKEY_GUIDANCE, justify="left", wraplength=620,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 5))
        row += 1

        self.hotkey_status = ttk.Label(f, text="Global hotkey status: Starting\u2026")
        self.hotkey_status.grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.repair_hotkey_button = ttk.Button(
            f, text="Repair Global Hotkey", command=self.app.repair_hotkey)
        self.repair_hotkey_button.grid(row=row, column=2, sticky="w", pady=(0, 5))
        row += 1

        trigger_label = ttk.Label(f, text="Trigger")
        trigger_label.grid(row=row, column=0, sticky="w", pady=2)
        self.var_trigger = tk.StringVar(value=s["trigger"])
        hold_trigger = ttk.Radiobutton(
            f, text="Hold to talk", value="hold", variable=self.var_trigger)
        hold_trigger.grid(
            row=row, column=1, sticky="w", padx=10)
        toggle_trigger = ttk.Radiobutton(
            f, text="Press to toggle", value="toggle", variable=self.var_trigger)
        toggle_trigger.grid(
            row=row, column=2, sticky="w")
        row += 1

        recording_length_label = ttk.Label(f, text="Maximum recording length")
        recording_length_label.grid(row=row, column=0, sticky="w", pady=2)
        self.recording_length_values = {
            label: seconds for seconds, label in cfg.RECORDING_LENGTHS.items()
        }
        self.var_recording_length = ttk.Combobox(
            f, values=list(self.recording_length_values), state="readonly", width=20)
        recording_seconds = cfg.recording_length_seconds(
            s.get("max_recording_seconds"))
        self.var_recording_length.set(cfg.RECORDING_LENGTHS[recording_seconds])
        self.var_recording_length.grid(
            row=row, column=1, sticky="w", padx=10, pady=2)
        ttk.Label(f, text="Automatically stops and transcribes").grid(
            row=row, column=2, sticky="w")
        row += 1

        microphone_label = ttk.Label(f, text="Microphone")
        microphone_label.grid(row=row, column=0, sticky="w", pady=2)
        device_options = self.app.input_device_options()
        self.device_values = {label: value for label, value in device_options}
        selected_device = s.get("input_device", cfg.DEFAULTS["input_device"])
        selected_label = next(
            (label for label, value in device_options if value == selected_device),
            device_options[0][0],
        )
        self.var_device = ttk.Combobox(
            f, values=[label for label, _value in device_options],
            state="readonly", width=46,
        )
        self.var_device.set(selected_label)
        self.var_device.grid(row=row, column=1, columnspan=2, sticky="w", padx=10, pady=2)
        row += 1

        ttk.Label(
            f,
            text=("Inputs with the same name under one audio system cannot "
                  "be selected individually; disconnect one to choose the other."),
            justify="left", wraplength=620,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 5))
        row += 1

        model_label = ttk.Label(f, text="Speech model")
        model_label.grid(row=row, column=0, sticky="w", pady=2)
        self.var_model = ttk.Combobox(f, values=[cfg.MODEL_LABELS[m] for m in cfg.MODELS],
                                      state="readonly", width=42)
        self.var_model.set(cfg.MODEL_LABELS.get(s["model"], cfg.MODEL_LABELS[cfg.MODELS[0]]))
        self.var_model.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        row += 1

        ttk.Label(
            f,
            text=(
                "Saving a different model starts preparation immediately. "
                "If its files are missing, Presspeech downloads them from "
                "huggingface.co; a first Parakeet download can be about "
                "2.5 GB. " + MODEL_DOWNLOAD_PRIVACY_NOTICE + " "
                "Dictation is unavailable until the model is ready. "
                "Without usable NVIDIA CUDA, Whisper base.en is the "
                "English-only CPU option (~141 MiB)."),
            justify="left", wraplength=620,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 5))
        row += 1

        self.model_status = ttk.Label(
            f, text="", justify="left", wraplength=620)
        self.model_status.grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.retry_model_button = ttk.Button(
            f, text="Retry Speech Model", command=self.app.retry_model)
        self.retry_model_button.grid(row=row, column=2, sticky="w", pady=(0, 5))
        row += 1

        suffix_label = ttk.Label(f, text="After pasting")
        suffix_label.grid(row=row, column=0, sticky="w", pady=2)
        self.var_suffix = ttk.Combobox(
            f, values=["space", "newline", "none"], state="readonly", width=14)
        self.var_suffix.set(s["suffix"])
        self.var_suffix.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        row += 1
        ttk.Label(
            f, text=PASTE_SUFFIX_GUIDANCE, justify="left", wraplength=620,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 5))
        row += 1

        self.var_fillers = tk.BooleanVar(value=s["remove_fillers"])
        ttk.Checkbutton(f, text="Remove filler words (um, uh, er\u2026)",
                        variable=self.var_fillers).grid(row=row, column=0, columnspan=3,
                                                        sticky="w", pady=2)
        row += 1

        self.var_british = tk.BooleanVar(value=s.get("british", True))
        ttk.Checkbutton(f, text="British English spelling (color \u2192 colour)",
                        variable=self.var_british).grid(row=row, column=0, columnspan=3,
                                                        sticky="w", pady=2)
        row += 1

        self.var_audio_cues = tk.BooleanVar(value=s.get("audio_cues", True))
        ttk.Checkbutton(f, text="Audio cues when dictation starts and stops",
                        variable=self.var_audio_cues).grid(
                            row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1

        self.var_mute_playback = tk.BooleanVar(
            value=s.get("mute_playback_while_recording", True))
        ttk.Checkbutton(f, text="Mute speaker playback while dictating",
                        variable=self.var_mute_playback).grid(
                            row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1

        self.var_visual_indicator = tk.BooleanVar(value=s.get("visual_indicator", True))
        ttk.Checkbutton(f, text="Show listening and transcribing indicator",
                        variable=self.var_visual_indicator).grid(
                            row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1

        self.var_check_updates = tk.BooleanVar(value=s.get("check_updates", True))
        ttk.Checkbutton(f, text="Check GitHub for updates once a day",
                        variable=self.var_check_updates).grid(
                            row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1

        self.var_autostart = tk.BooleanVar(value=s["autostart"])
        ttk.Checkbutton(f, text="Start with Windows",
                        variable=self.var_autostart).grid(row=row, column=0, columnspan=2,
                                                          sticky="w", pady=2)
        startup_button = ttk.Button(
            f, text="Open Startup Settings",
            command=self.app.open_startup_settings,
        )
        startup_button.grid(row=row, column=2, sticky="w", pady=2)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3,
                                                   sticky="ew", pady=8)
        row += 1

        ttk.Label(f, text="Dictionary (fix mishearings / spoken shortcuts):").grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1

        self.var_spoken = tk.StringVar()
        self.var_replace = tk.StringVar()
        spoken_label = ttk.Label(f, text="Spoken phrase")
        spoken_label.grid(row=row, column=0, sticky="w")
        replacement_label = ttk.Label(f, text="Replacement")
        replacement_label.grid(row=row, column=2, sticky="w", padx=10)
        row += 1
        self.spoken_entry = ttk.Entry(
            f, textvariable=self.var_spoken, width=18)
        self.spoken_entry.grid(row=row, column=0, sticky="w", pady=2)
        ttk.Label(f, text="\u2192").grid(row=row, column=1)
        self.replacement_entry = ttk.Entry(
            f, textvariable=self.var_replace, width=18)
        self.replacement_entry.grid(
            row=row, column=2, sticky="w", padx=10, pady=2)
        row += 1

        add_button = ttk.Button(f, text="Add rule", command=self._add_rule)
        add_button.grid(row=row, column=0, sticky="w", pady=2)
        remove_button = ttk.Button(
            f, text="Remove selected", command=self._remove_rule)
        remove_button.grid(row=row, column=1, columnspan=2, sticky="w")
        row += 1

        self.listbox = tk.Listbox(f, width=52, height=6)
        self.listbox.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
        # The listbox text is only a presentation. Re-parsing its arrow
        # separator would corrupt rules that legitimately contain that
        # character and would strip exact shortcut whitespace on every save.
        self.dictionary_rules = [list(rule) for rule in s["dictionary"]]
        for spoken, replacement in self.dictionary_rules:
            self.listbox.insert("end", "%s \u2192 %s" % (spoken, replacement))
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3,
                                                   sticky="ew", pady=8)
        row += 1

        self.save_button = ttk.Button(f, text="Save", command=self._save)
        self.save_button.grid(row=row, column=0, sticky="w")
        self.status = ttk.Label(f, text="")
        self.status.grid(row=row, column=1, columnspan=2, sticky="w", padx=10)

        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, add_button, "a")
        _add_access_key(root, remove_button, "r")
        _add_access_key(root, self.try_button, "t")
        _add_access_key(root, self.repair_hotkey_button, "h")
        _add_access_key(root, startup_button, "o")
        _add_access_key(root, self.retry_model_button, "m")
        _add_access_key(root, self.save_button, "s")
        _bind_window_command(root, "<Control-s>", self._save)
        _bind_window_command(root, "<Escape>", self._close)
        root.update_idletasks()
        _label_trigger_choices(trigger_label, hold_trigger, toggle_trigger)
        for label, control in (
                (hotkey_label, self.var_hotkey),
                (recording_length_label, self.var_recording_length),
                (microphone_label, self.var_device),
                (model_label, self.var_model),
                (suffix_label, self.var_suffix),
                (spoken_label, self.spoken_entry),
                (replacement_label, self.replacement_entry)):
            _label_control(label, control)
        _name_control(
            self.var_hotkey, "Dictation hotkey. " + ALTGR_HOTKEY_GUIDANCE)
        _name_control(self.var_suffix, PASTE_SUFFIX_ACCESSIBLE_NAME)
        _name_control(self.listbox, "Dictionary rules")
        _mark_live_region(self.model_status)
        _mark_live_region(self.hotkey_status)
        _mark_live_region(self.status)
        self._poll_model()
        self.scrollable_body.fit_to_screen()
        root.after_idle(self.var_hotkey.focus_set)

    def _poll_model(self):
        """Keep selected-model readiness visible while Settings stays open."""
        if self.root is None:
            return
        selected = self.app.settings.get("model", cfg.DEFAULTS["model"])
        status = getattr(self.app, "model_status", "pending")
        detail = getattr(self.app, "model_status_detail", "")
        phase = status
        if status == "ready" and self.app.transcriber.loaded(selected):
            text = "Speech model ready" + ((" — " + detail) if detail else "")
        elif status == "error":
            text = "Speech model needs attention" + (
                (" — " + detail) if detail else "")
        elif status == "awaiting_download_consent":
            model_name = self.app.settings.get("model", cfg.DEFAULTS["model"])
            if model_name == "base.en":
                text = (
                    "English-only Whisper base.en files aren't fully cached "
                    "(~141 MiB). Choose whether to download them in Setup.")
            else:
                text = (
                    "Parakeet model files aren't fully cached. Choose whether "
                    "to start the full download (~2.5 GB) in Setup.")
        elif status == "loading":
            text, phase = _model_loading_feedback(self.app)
            text += " Dictation is unavailable until it is ready."
        else:
            text = (
                "Preparing selected speech model… Dictation is unavailable "
                "until it is ready.")
            phase = status
        previous_phase = getattr(self, "_last_model_phase", None)
        if previous_phase == phase:
            _set_accessible_text(self.model_status, text, announce=False)
        else:
            _set_accessible_text(self.model_status, text)
            self._last_model_phase = phase
        hotkey_state, hotkey_detail = _hotkey_readiness(self.app)
        _set_accessible_text(
            self.hotkey_status, "Global hotkey status: " + hotkey_detail)
        self.repair_hotkey_button.config(state="normal")
        _set_control_state(
            self.root, self.retry_model_button,
            "normal" if status == "error" else "disabled", self.var_model)
        self._refresh_save_state()
        self.root.after(300, self._poll_model)

    def _refresh_save_state(self):
        """Keep Save truthful while an in-flight dictation owns app settings."""
        reason = _settings_save_block_reason(self.app)
        previous = getattr(self, "_save_block_reason", "")
        self._save_block_reason = reason
        # A recording may start while Save has keyboard focus. Leave focus on
        # the adjacent dictionary list before disabling Save, so Tab remains
        # usable and a background poll never steals focus from another control.
        _set_control_state(
            self.root, self.save_button,
            "disabled" if reason else "normal", self.listbox)
        if reason and reason != previous:
            _set_accessible_text(self.status, reason)
        elif not reason and previous:
            _set_accessible_text(
                self.status, "Dictation finished. Settings can now be saved.")

    def _add_rule(self):
        spoken = self.var_spoken.get().strip()
        replacement = self.var_replace.get()
        if not spoken:
            return
        candidate = cfg.validated_dictionary(
            self.dictionary_rules + [[spoken, replacement]])
        if candidate is None or len(candidate) != len(self.dictionary_rules) + 1:
            _set_accessible_text(
                self.status,
                "Rule not added. Check the text length or remove an existing rule.")
            return
        self.dictionary_rules = candidate
        self.listbox.insert("end", "%s \u2192 %s" % (spoken, replacement))
        self.var_spoken.set("")
        self.var_replace.set("")
        _set_accessible_text(self.status, "")

    def _remove_rule(self):
        selection = self.listbox.curselection()
        if selection:
            index = selection[0]
            del self.dictionary_rules[index]
            self.listbox.delete(index)

    def _save(self):
        # Save is disabled while busy, but Ctrl+S and a recording that starts
        # between UI polls can still invoke this path. Serialize the definitive
        # check and settings mutation with the app's recording lifecycle.
        with self.app.lock:
            reason = _settings_save_block_reason(self.app)
            if reason:
                self._save_block_reason = reason
                # Ctrl+S can race the poll above; apply the same focus rule at
                # the definitive save boundary.
                _set_control_state(
                    self.root, self.save_button, "disabled", self.listbox)
                _set_accessible_text(self.status, reason)
                return False

            s = self.app.settings
            label_to_value = {v: k for k, v in cfg.MODEL_LABELS.items()}
            old_model = s.get("model", cfg.DEFAULTS["model"])
            s["hotkey"] = self.var_hotkey.get() or cfg.DEFAULTS["hotkey"]
            s["trigger"] = self.var_trigger.get()
            s["max_recording_seconds"] = self.recording_length_values.get(
                self.var_recording_length.get(),
                cfg.DEFAULTS["max_recording_seconds"],
            )
            old_input_device = s.get("input_device", cfg.DEFAULTS["input_device"])
            s["input_device"] = self.device_values.get(
                self.var_device.get(), cfg.DEFAULTS["input_device"])
            if s["input_device"] != old_input_device:
                self.app.input_device = None
                self.app._cached_input_selector = None
                self.app._cached_input_topology = None
            s["model"] = label_to_value.get(
                self.var_model.get(), cfg.DEFAULTS["model"])
            s["model_explicit"] = True
            s["suffix"] = self.var_suffix.get() or cfg.DEFAULTS["suffix"]
            s["remove_fillers"] = bool(self.var_fillers.get())
            s["british"] = bool(self.var_british.get())
            s["audio_cues"] = bool(self.var_audio_cues.get())
            s["mute_playback_while_recording"] = bool(self.var_mute_playback.get())
            s["visual_indicator"] = bool(self.var_visual_indicator.get())
            if not s["visual_indicator"]:
                self.app._set_indicator(None)
            s["check_updates"] = bool(self.var_check_updates.get())
            s["autostart"] = bool(self.var_autostart.get())
            s["dictionary"] = cfg.validated_dictionary(self.dictionary_rules) or []
            cfg.save(s)
            # Publish the new model lifecycle before recording start can take
            # the app lock. Readiness must not pass against the old model after
            # Settings has committed the new selection.
            if s["model"] != old_model:
                self.app.prepare_configured_model()
        if self.app.apply_autostart():
            status = "Saved. Changes apply immediately."
        else:
            status = (
                "Saved, but Start with Windows was not updated. "
                "Open Startup Settings to review it.")
        _set_accessible_text(self.status, status)
        return True

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app.settings_window = None


class DeliveryRecoveryWindow:
    """Visible controls for private text whose delivery was not confirmed."""

    def __init__(self, app):
        self.app = app
        self.root = None
        self._waiting = None
        self._build_failed = False
        # Register before queueing the asynchronous build. If Tk rejects the
        # window immediately, the UI-thread failure path can clear this exact
        # object without racing a later assignment from the caller.
        self.app.delivery_recovery_window = self
        _window_host().submit(self._build)

    def _build(self):
        try:
            self._build_window()
        except Exception as exc:
            self._build_failed = True
            failed_root = self.root
            self.root = None
            if failed_root is not None:
                try:
                    failed_root.destroy()
                except Exception:
                    pass
            self.app._report_delivery_recovery_window_failure(exc, self)
            raise

    def _build_window(self):
        root = _interactive_window("Presspeech - Delivery Recovery")
        self.root = root
        root.resizable(True, True)
        root.lift()
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))
        self.scrollable_body = _ScrollableDialogBody(root, padding=18)
        frame = self.scrollable_body.content

        ttk.Label(
            frame, text="Dictation needs review",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Presspeech could not confirm delivery of one or more recent "
                "dictations. Check the original field or fields first: some "
                "or all of the text may already be there or on the clipboard.\n\n"
                "Presspeech keeps recovery copies in process memory; their "
                "words are not shown in this window or written to a recovery "
                "file. Copy for deliberate manual paste, or discard. When "
                "multiple dictations are waiting, each action handles the "
                "oldest one first. Resolve them one at a time; recording "
                "remains paused until all are resolved."
            ),
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(6, 12))
        self.status = ttk.Label(
            frame, text="", justify="left", wraplength=560)
        self.status.pack(anchor="w", pady=(0, 12))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.leave_button = ttk.Button(
            buttons, text="Leave Waiting", command=self._close)
        self.leave_button.pack(side="left")
        self.copy_button = ttk.Button(
            buttons, text="Copy for Manual Paste", command=self._copy)
        # Construct Copy before Discard so keyboard traversal reaches the
        # recoverable action before the destructive one. Pack the right-side
        # buttons in reverse so their visual order matches that tab sequence.
        self.discard_button = ttk.Button(
            buttons, text="Discard Dictation", command=self._discard)
        self.discard_button.pack(side="right", padx=(0, 8))
        self.copy_button.pack(side="right")

        ttk.Label(
            frame,
            text=(
                "Leaving this window keeps the dictation only while "
                "Presspeech is running. Exiting Presspeech discards it. "
                "Discard forgets only the recovery copy; it does not change "
                "the current clipboard or text already in a field. "
                "Windows is asked to exclude this copy from Clipboard History "
                "and Cloud Clipboard. Other software with clipboard access "
                "may still read or retain it."
            ),
            justify="left",
            wraplength=560,
        ).pack(anchor="w", pady=(12, 0))

        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, self.leave_button, "l")
        _add_access_key(root, self.discard_button, "d")
        _add_access_key(root, self.copy_button, "c")
        _bind_window_command(root, "<Escape>", self._close)
        root.update_idletasks()
        _mark_live_region(self.status)
        self._refresh_waiting_state(
            "A dictation is waiting. Check its original field before copying.")
        self.scrollable_body.fit_to_screen()
        # Delivery completes asynchronously, so the window can appear while a
        # user is already pressing Enter in the target. Put focus on the
        # non-destructive command and require deliberate navigation to Copy or
        # Discard; neither privacy-affecting action is the default button.
        root.after_idle(self.leave_button.focus_set)
        root.after(250, self._poll)

    def _refresh_waiting_state(self, status=None):
        waiting = bool(self.app.has_undelivered_dictation())
        state = "normal" if waiting else "disabled"
        # A tray action can resolve the last item while this window is open.
        # Move focus only when disabling the focused action; otherwise a
        # background status refresh would unexpectedly steal focus from the
        # user's current control or another Presspeech window.
        _set_control_state(
            self.root, self.copy_button, state, self.leave_button)
        _set_control_state(
            self.root, self.discard_button, state, self.leave_button)
        if status is None:
            status = (
                "A dictation is waiting. Check its original field before copying."
                if waiting else
                "No undelivered dictation remains. You can record again."
            )
        _set_accessible_text(self.status, status)
        self._waiting = waiting

    def _copy(self):
        if not self.app.copy_undelivered_dictation():
            if self.app.has_undelivered_dictation():
                status = (
                    "Copy did not complete. The dictation is still kept in "
                    "memory; try again or discard it."
                )
            else:
                status = "No undelivered dictation remains. You can record again."
            self._refresh_waiting_state(status)
            return
        if self.app.has_undelivered_dictation():
            status = (
                "Dictation copied. Check the intended field before pasting "
                "manually. Another dictation is still waiting."
            )
        else:
            status = (
                "Dictation copied. Check the intended field before pasting "
                "manually. You can record again."
            )
        self._refresh_waiting_state(status)

    def _discard(self):
        discarded = self.app.discard_undelivered_dictation()
        if self.app.has_undelivered_dictation():
            status = (
                "Another undelivered dictation is still waiting."
                if discarded else
                "Discard did not complete. The dictation is still kept in memory."
            )
        elif discarded:
            status = "Dictation discarded. You can record again."
        else:
            # A tray command or worker may have resolved the same entry before
            # this queued button command ran. Never claim that this command
            # discarded text when there was no recovery item left to change.
            status = "No undelivered dictation remains. You can record again."
        self._refresh_waiting_state(status)

    def _poll(self):
        if self.root is None:
            return
        waiting = bool(self.app.has_undelivered_dictation())
        if waiting != self._waiting:
            self._refresh_waiting_state()
        self.root.after(250, self._poll)

    def _close(self):
        root = self.root
        self.root = None
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass
        if getattr(self.app, "delivery_recovery_window", None) is self:
            self.app.delivery_recovery_window = None


class ScratchpadWindow:
    def __init__(self, app):
        self.app = app
        self.root = None
        self.window_handle = 0
        # A model worker schedules editor insertion on Tk's thread. Window
        # destruction cancels pending Tk callbacks, so keep the text until
        # insertion actually succeeds or the close path retains it for review.
        self._append_lock = threading.Lock()
        self._pending_appends = {}
        self._next_append_id = 0
        _window_host().submit(self._build)

    def _build(self):
        root = _interactive_window("Presspeech - Try Dictation")
        self.root = root
        root.resizable(True, True)
        pixels_per_inch = root.winfo_fpixels("1i")
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width, height = _bounded_window_size(
            480, 280, pixels_per_inch, screen_width, screen_height)
        minimum_width, minimum_height = _bounded_window_size(
            320, 240, pixels_per_inch, screen_width, screen_height)
        root.geometry("%dx%d" % (width, height))
        root.minsize(
            min(width, minimum_width), min(height, minimum_height))
        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        scratchpad_label = ttk.Label(frame, text="Private dictation scratchpad")
        scratchpad_label.grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.text = tk.Text(frame, wrap="word", font="TkDefaultFont")
        self.text.grid(row=1, column=0, sticky="nsew")
        self._protect_scratchpad_copy_and_cut()
        transcript_scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.text.yview,
            takefocus=False)
        transcript_scrollbar.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=transcript_scrollbar.set)
        self.status = ttk.Label(
            frame, text="", justify="left", wraplength=max(1, width - 48))
        self.status.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 6))
        self.btn = ttk.Button(
            frame, text="Dictate (or use the hotkey)", command=self.toggle)
        self.btn.grid(row=3, column=0, columnspan=2, pady=(0, 2))
        self.review_button = ttk.Button(
            frame, text="Review Delivery…",
            command=self.app.open_delivery_recovery, state="disabled")
        self.review_button.grid(row=4, column=0, columnspan=2, pady=(0, 2))
        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, self.btn, "d")
        _add_access_key(root, self.review_button, "r")
        _bind_window_command(root, "<Escape>", self._close)

        def resize_status(event):
            if event.widget is root:
                self.status.configure(wraplength=max(1, event.width - 48))

        root.bind("<Configure>", resize_status, add="+")
        root.update_idletasks()
        _label_control(scratchpad_label, self.text)
        _name_control(self.text, "Private dictation scratchpad")
        _describe_control(
            self.review_button,
            "Open Delivery Recovery for a waiting dictation without copying it.")
        self._refresh_controls()
        _mark_live_region(self.status)
        root.after_idle(self.text.focus_set)
        root.after(100, self._poll_controls)
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        client_handle = root.winfo_id()
        self.window_handle = int(user32.GetParent(client_handle) or client_handle)

    def _protect_scratchpad_copy_and_cut(self):
        # Widget bindings run before Tk's Text class bindings. Return "break"
        # even on failure so the default Copy/Cut cannot publish an unmarked
        # dictation through Tk's own clipboard path.
        self.text.bind("<<Copy>>", lambda _event: self._protected_copy_event())
        self.text.bind("<<Cut>>", lambda _event: self._protected_copy_event(cut=True))

    def _protected_copy_event(self, *, cut=False):
        try:
            self._copy_or_cut_selection(cut=cut)
        except Exception:
            # Even an unexpected Tk or notification failure must not let the
            # unprotected Text class binding run after this widget binding.
            pass
        return "break"

    def _copy_or_cut_selection(self, *, cut=False):
        try:
            start = self.text.index("sel.first")
            end = self.text.index("sel.last")
            selected = self.text.get(start, end)
        except tk.TclError:
            return "break"  # No selection; leave the existing clipboard alone.
        if not selected:
            return "break"

        try:
            receipt = clipboard_delivery.write_text(selected)
            copied = clipboard_delivery.is_current(receipt)
        except Exception:
            copied = False
        if not copied:
            self.app.notify(
                "Clipboard copy unavailable",
                "Try Dictation could not confirm the copy. Check the clipboard "
                "before trying again; Presspeech did not remove scratchpad text.")
            return "break"

        if cut:
            try:
                # The native clipboard transaction may wait for another app.
                # Do not delete text if the selection changed while it ran.
                if (self.text.index("sel.first") == start and
                        self.text.index("sel.last") == end and
                        self.text.get(start, end) == selected):
                    self.text.delete(start, end)
            except tk.TclError:
                pass
        return "break"

    def toggle(self):
        if self.app.recording:
            self.app.stop_recording()
        else:
            self.app.start_recording()
        self._refresh_controls()

    def _control_state(self, waiting):
        """Return a truthful command and status for the app lifecycle."""
        if getattr(self.app, "recording", False):
            if not getattr(self.app, "_capture_ready", True):
                return (
                    "Stop Dictation", "normal",
                    "Connecting microphone… Wait for the start cue or "
                    "Listening status before speaking. Press Escape to cancel.",
                )
            return (
                "Stop Dictation", "normal",
                "Recording… Speak, then stop dictation or press Escape to cancel.",
            )
        if getattr(self.app, "transcribing", False):
            return (
                "Dictate (or use the hotkey)", "disabled",
                "Transcribing… Dictation will be available when this finishes.",
            )
        if getattr(self.app, "_canceling_recording", False):
            return (
                "Dictate (or use the hotkey)", "disabled",
                "Canceling dictation… Dictation will be available when cleanup finishes.",
            )
        if waiting:
            return (
                "Dictate (or use the hotkey)", "disabled",
                "An undelivered dictation needs review before recording again. "
                "Choose Review Delivery to copy or discard it.",
            )
        if getattr(self.app, "_microphone_check_in_progress", False) is True:
            return (
                "Dictate (or use the hotkey)", "disabled",
                "Microphone check in progress… Wait for it to finish before dictating.",
            )
        model_status = getattr(self.app, "model_status", "pending")
        if model_status == "error":
            return (
                "Dictate (or use the hotkey)", "disabled",
                "Speech model needs attention. Open Setup or Settings to retry.",
            )
        if model_status == "awaiting_download_consent":
            model_name = self.app.settings.get("model", cfg.DEFAULTS["model"])
            if model_name == "base.en":
                detail = (
                    "English-only Whisper base.en files aren't fully cached "
                    "(~141 MiB). Open Setup to download them or defer.")
            else:
                detail = (
                    "Parakeet model files aren't fully cached. Open Setup to "
                    "start the full download (~2.5 GB), choose the smaller "
                    "English-only CPU model, or defer.")
            return "Dictate (or use the hotkey)", "disabled", detail
        if model_status != "ready":
            return (
                "Dictate (or use the hotkey)", "disabled",
                "Preparing speech model… Dictation will be available when it is ready.",
            )
        return (
            "Dictate (or use the hotkey)", "normal",
            "Ready. Dictation appears in this private scratchpad.",
        )

    def _refresh_controls(self):
        has_undelivered = getattr(
            self.app, "has_undelivered_dictation", None)
        waiting = bool(has_undelivered()) if callable(has_undelivered) else False
        label, state, status = self._control_state(waiting)
        # Do not leave keyboard focus on a control as it becomes disabled.
        # The editor remains a useful, non-destructive focus destination while
        # transcription, cancellation, or recovery blocks another recording.
        try:
            if state == "disabled" and self.root.focus_get() is self.btn:
                self.text.focus_set()
        except (AttributeError, tk.TclError):
            pass
        self.btn.config(state=state)
        _set_control_state(
            self.root, self.review_button,
            "normal" if waiting else "disabled", self.text)
        _set_accessible_text(self.btn, label, announce=False)
        _set_accessible_text(self.status, status)

    def _poll_controls(self):
        if self.root is None:
            return
        self._refresh_controls()
        self.root.after(100, self._poll_controls)

    def append_text(self, text):
        with self._append_lock:
            root = self.root
            if root is None or self.app.scratchpad is not self:
                append_id = None
            else:
                self._next_append_id += 1
                append_id = self._next_append_id
                self._pending_appends[append_id] = text
        if append_id is None:
            self.app._remember_undelivered_dictation(
                text, "scratchpad-unavailable")
            return

        def do():
            with self._append_lock:
                pending_text = self._pending_appends.pop(append_id, None)
                available = self.root is root
            if pending_text is None:
                return  # The close path already retained it.
            if not available:
                self.app._remember_undelivered_dictation(
                    pending_text, "scratchpad-unavailable")
                return
            try:
                self.text.insert("end", pending_text)
            except Exception:
                self.app._remember_undelivered_dictation(
                    pending_text, "scratchpad-unavailable")
                return
            # A close that raced an insertion may have destroyed the editor
            # before anyone could review it. Scrolling is cosmetic and must
            # not turn a successful insert into a duplicate recovery copy.
            with self._append_lock:
                still_open = self.root is root
            if not still_open:
                self.app._remember_undelivered_dictation(
                    pending_text, "scratchpad-unavailable")
                return
            try:
                self.text.see("end")
            except Exception:
                pass

        try:
            root.after(0, do)
        except Exception:
            with self._append_lock:
                failed_text = self._pending_appends.pop(append_id, None)
            if failed_text is not None:
                self.app._remember_undelivered_dictation(
                    failed_text, "scratchpad-unavailable")

    def _close(self):
        # A recording started in Try Dictation has no safe destination after
        # this window disappears. Cancel that capture rather than leaving the
        # microphone active behind a closed private scratchpad and discarding
        # its eventual transcript. A recording owned by another application
        # must remain independent of this utility window.
        if (getattr(self.app, "recording", False) and
                getattr(self.app, "_recording_scratchpad", None) is self):
            self.app.cancel_recording()
        with self._append_lock:
            root = self.root
            self.root = None
            pending = tuple(self._pending_appends.values())
            self._pending_appends.clear()
        try:
            if root is not None:
                root.destroy()
        except Exception:
            pass
        self.window_handle = 0
        if self.app.scratchpad is self:
            self.app.scratchpad = None
        for text in pending:
            self.app._remember_undelivered_dictation(
                text, "scratchpad-unavailable")
