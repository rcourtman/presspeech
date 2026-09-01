"""Tkinter windows: settings, scratchpad, and a focus-safe status overlay."""

import ctypes
import os
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import config as cfg
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


def _window_host():
    global _WINDOW_HOST
    with _WINDOW_HOST_LOCK:
        if _WINDOW_HOST is None:
            _WINDOW_HOST = _WindowHost()
        return _WINDOW_HOST


def _interactive_window(title):
    root = tk.Toplevel(_window_host().root)
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


def _add_access_key(root, widget, key):
    """Give a command its conventional Windows Alt mnemonic."""
    key = key.casefold()
    widget._presspeech_access_key = key
    widget.config(underline=_access_key_index(str(widget.cget("text")), key))
    _bind_window_command(
        root, "<Alt-KeyPress-%s>" % key, widget.invoke)


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
        "listening": ("Listening\u2026", "#ff5a5f"),
        "transcribing": ("Transcribing\u2026", "#ffb340"),
    }

    def __init__(self):
        self._commands = queue.Queue()
        self._thread = None
        self._thread_lock = threading.Lock()
        self._closed = False

    def show(self, state):
        if state not in self._STATES or self._closed:
            return
        self._ensure_thread()
        self._commands.put(state)

    def hide(self):
        if self._thread is not None and not self._closed:
            self._commands.put("hide")

    def close(self):
        self._closed = True
        if self._thread is not None:
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

            def apply_command(command):
                nonlocal visible_state
                if command == "close":
                    root.destroy()
                    return False
                if command == "hide":
                    visible_state = None
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    return True
                label.configure(text=self._STATES[command][0])
                # Refresh even if the colours are otherwise unchanged because
                # the default accent is specific to the current state.
                apply_palette(command, force=True)
                visible_state = command
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

            def refresh_contrast_theme():
                if visible_state is not None:
                    # Contrast themes can be toggled while a two-minute
                    # recording is active. Keep a visible indicator in sync
                    # without requiring another dictation state transition.
                    apply_palette(visible_state)
                root.after(250, refresh_contrast_theme)

            root.after(0, poll)
            root.after(250, refresh_contrast_theme)
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
        root.resizable(True, True)
        root.lift()
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))
        self.scrollable_body = _ScrollableDialogBody(root, padding=20)
        frame = self.scrollable_body.content

        ttk.Label(frame, text="Presspeech is almost ready",
                  font=("Segoe UI", 16, "bold")).grid(
                      row=0, column=0, columnspan=2, sticky="w")
        self.instructions = ttk.Label(
            frame,
            text=self._dictation_instructions(),
            justify="left",
        )
        self.instructions.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))

        ttk.Label(frame, text="Speech model").grid(row=2, column=0, sticky="w")
        self.model_label = ttk.Label(frame, text="Preparing…")
        self.model_label.grid(row=2, column=1, sticky="w", padx=(12, 0))
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=260)
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 13))
        self.progress.start(12)

        microphone_label = ttk.Label(frame, text="Microphone")
        microphone_label.grid(row=4, column=0, sticky="w")
        options = self.app.input_device_options()
        self.device_values = {label: value for label, value in options}
        current = self.app.settings.get("input_device", cfg.DEFAULTS["input_device"])
        selected = next((label for label, value in options if value == current),
                        options[0][0])
        self.device = ttk.Combobox(
            frame, values=[label for label, _value in options],
            state="readonly", width=48)
        self.device.set(selected)
        self.device.grid(row=4, column=1, sticky="w", padx=(12, 0), pady=3)
        self.device.bind("<<ComboboxSelected>>", self._microphone_changed)

        microphone_check_label = ttk.Label(frame, text="Microphone check")
        microphone_check_label.grid(row=5, column=0, sticky="w", pady=(5, 3))
        microphone_check = ttk.Frame(frame)
        microphone_check.grid(row=5, column=1, sticky="ew", padx=(12, 0), pady=(5, 3))
        self.microphone_status = ttk.Label(microphone_check, text="Waiting to check…")
        self.microphone_status.pack(side="left")
        self.check_microphone_button = ttk.Button(
            microphone_check, text="Check Again", command=self._check_microphone)
        self.check_microphone_button.pack(side="right", padx=(12, 0))

        ttk.Label(
            frame,
            text=("Speak while the check runs. If it fails, enable Microphone "
                  "access and Let desktop apps access your microphone."),
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 3))
        microphone_actions = ttk.Frame(frame)
        microphone_actions.grid(row=7, column=0, columnspan=2, sticky="w")
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

        hotkey_label = ttk.Label(frame, text="Push-to-talk key")
        hotkey_label.grid(row=8, column=0, sticky="w")
        self.hotkey = ttk.Combobox(
            frame, values=cfg.HOTKEYS, state="readonly", width=18)
        self.hotkey.set(self.app.settings.get("hotkey", cfg.DEFAULTS["hotkey"]))
        self.hotkey.grid(row=8, column=1, sticky="w", padx=(12, 0), pady=3)
        self.hotkey.bind("<<ComboboxSelected>>", self._hotkey_changed)
        ttk.Label(
            frame,
            text=ALTGR_HOTKEY_GUIDANCE,
            justify="left",
            wraplength=560,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(3, 8))

        self.autostart = tk.BooleanVar(
            value=self.app.settings.get("autostart", True))
        ttk.Checkbutton(frame, text="Start Presspeech with Windows",
                        variable=self.autostart).grid(
                            row=10, column=0, columnspan=2, sticky="w", pady=(2, 6))

        startup_actions = ttk.Frame(frame)
        startup_actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        self.autostart_status = ttk.Label(startup_actions, text="")
        self.autostart_status.pack(side="left")
        startup_button = ttk.Button(
            startup_actions, text="Open Startup Settings",
            command=self.app.open_startup_settings,
        )
        startup_button.pack(side="right")

        buttons = ttk.Frame(frame)
        buttons.grid(row=12, column=0, columnspan=2, sticky="ew")
        self.try_button = ttk.Button(
            buttons, text="Try Dictation", command=self.app.open_scratchpad,
            state="disabled")
        self.try_button.pack(side="left")
        self.retry_button = ttk.Button(
            buttons, text="Retry Speech Model", command=self._retry_model,
            state="disabled")
        self.retry_button.pack(side="left", padx=(8, 0))
        self.finish_button = ttk.Button(
            buttons, text="Finish Setup", command=self._finish,
            state="disabled")
        self.finish_button.pack(side="right")
        later_button = ttk.Button(
            buttons, text="Set Up Later", command=self._defer)
        later_button.pack(side="right", padx=(0, 8))

        root.protocol("WM_DELETE_WINDOW", self._defer)
        for button, key in (
                (self.check_microphone_button, "c"),
                (privacy_button, "p"),
                (sound_button, "s"),
                (startup_button, "o"),
                (self.try_button, "t"),
                (self.retry_button, "r"),
                (self.finish_button, "f"),
                (later_button, "l")):
            _add_access_key(root, button, key)
        _bind_window_command(root, "<Escape>", self._defer)
        root.update_idletasks()
        _label_control(microphone_label, self.device)
        _label_control(microphone_check_label, self.microphone_status)
        _label_control(hotkey_label, self.hotkey)
        _name_control(
            self.hotkey, "Push-to-talk key. " + ALTGR_HOTKEY_GUIDANCE)
        for status in (
                self.model_label, self.microphone_status,
                self.autostart_status):
            _mark_live_region(status)
        self.scrollable_body.fit_to_screen()
        root.after_idle(self.device.focus_set)
        root.after(100, self._poll_model)
        root.after(150, self._check_microphone)

    def _poll_model(self):
        if self.root is None:
            return
        self._poll_microphone_events()
        status = getattr(self.app, "model_status", "pending")
        detail = getattr(self.app, "model_status_detail", "")
        labels = {
            "pending": "Waiting to start…",
            "loading": detail or "Downloading or loading…",
            "ready": "Ready" + ((" — " + detail) if detail else ""),
            "error": "Needs attention" + ((" — " + detail) if detail else ""),
        }
        _set_accessible_text(
            self.model_label, labels.get(status, detail or status))
        self.retry_button.config(
            state="normal" if status == "error" else "disabled")
        self.try_button.config(
            state="normal" if status == "ready" else "disabled")
        self.finish_button.config(
            state="normal" if status == "ready" else "disabled")
        if status in ("ready", "error"):
            self.progress.stop()
            self.progress.config(mode="determinate", value=100 if status == "ready" else 0)
        self.root.after(300, self._poll_model)

    def _microphone_changed(self, _event=None):
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        settings = self.app.settings
        if selected != settings.get("input_device", cfg.DEFAULTS["input_device"]):
            # Try Dictation uses the app's live capture configuration. Apply and
            # persist the checked device now so the test cannot silently record
            # from the previous automatic input and setup can be resumed later.
            settings["input_device"] = selected
            self.app.input_device = None
            cfg.save(settings)
        _set_accessible_text(self.microphone_status, "Waiting to check…")
        self.root.after(0, self._check_microphone)

    def _dictation_instructions(self):
        hotkey = self.app.settings.get("hotkey", cfg.DEFAULTS["hotkey"]).title()
        if self.app.settings.get("trigger", cfg.DEFAULTS["trigger"]) == "toggle":
            action = (
                "Press %s to start, then press it again to type at the cursor."
                % hotkey
            )
        else:
            action = "Hold %s, speak, then release to type at the cursor." % hotkey
        return (action + "\nSpeech stays on this PC; no audio or transcripts "
                "are uploaded.")

    def _hotkey_changed(self, _event=None):
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

    def _check_microphone(self):
        if self.microphone_checking or self.root is None:
            return
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        self.microphone_checking = True
        self.check_microphone_button.config(state="disabled")
        _set_accessible_text(
            self.microphone_status, "Listening — speak a few words…")
        threading.Thread(
            target=self._check_microphone_worker,
            args=(selected,),
            name="presspeech-microphone-check",
            daemon=True,
        ).start()

    def _check_microphone_worker(self, selected):
        result = self.app.check_input_device(selected)
        self.microphone_events.put((selected, result))

    def _poll_microphone_events(self):
        latest = None
        try:
            while True:
                latest = self.microphone_events.get_nowait()
        except queue.Empty:
            pass
        if latest is None:
            return
        selected, result = latest
        self.microphone_checking = False
        self.check_microphone_button.config(state="normal")
        current = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        if selected != current:
            _set_accessible_text(self.microphone_status, "Waiting to check…")
            self.root.after(0, self._check_microphone)
            return
        if result == "level":
            text = "Ready — input level detected"
        elif result == "silent":
            text = "Connected, but no input level detected — unmute and check again"
        else:
            text = "Needs attention — microphone could not be opened"
        _set_accessible_text(self.microphone_status, text)

    def _retry_model(self):
        self.app.retry_model()

    def _finish(self):
        # setup_complete means the app has reached a usable speech-model state.
        # A microphone may deliberately be connected later, but dismissing a
        # pending or failed model would hide the guided retry path on restart.
        if getattr(self.app, "model_status", "pending") != "ready":
            self.finish_button.config(state="disabled")
            return
        settings = self.app.settings
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        if selected != settings.get("input_device", cfg.DEFAULTS["input_device"]):
            self.app.input_device = None
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
        settings = self.app.settings
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        if selected != settings.get("input_device", cfg.DEFAULTS["input_device"]):
            self.app.input_device = None
        settings["input_device"] = selected
        settings["autostart"] = bool(self.autostart.get())
        cfg.save(settings)
        self.app.apply_autostart()
        self._close()

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app.setup_window = None


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
            text=("The installer is downloaded only if you approve it.\n"
                  "Its size and SHA-256 checksum will be verified before it runs."),
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        self.status = ttk.Label(frame, text="Ready to download")
        self.status.pack(anchor="w")
        self.progress = ttk.Progressbar(frame, mode="determinate", length=370)
        self.progress.pack(fill="x", pady=(5, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        later_button = ttk.Button(buttons, text="Later", command=self._close)
        later_button.pack(side="left")
        self.download_button = ttk.Button(
            buttons, text="Download Update", command=self._download,
            default="active")
        self.download_button.pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, later_button, "l")
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
        self.download_button.config(state="disabled")
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
            self.events.put(("error", str(exc)))
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
                    "could not schedule interrupted update cleanup: %s" % exc)
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
                                "Update failed", str(exc), parent=self.root)
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
        row += 1

        ttk.Label(
            f, text=ALTGR_HOTKEY_GUIDANCE, justify="left", wraplength=620,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 5))
        row += 1

        ttk.Label(f, text="Trigger").grid(row=row, column=0, sticky="w", pady=2)
        self.var_trigger = tk.StringVar(value=s["trigger"])
        ttk.Radiobutton(f, text="Hold to talk", value="hold", variable=self.var_trigger).grid(
            row=row, column=1, sticky="w", padx=10)
        ttk.Radiobutton(f, text="Press to toggle", value="toggle", variable=self.var_trigger).grid(
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

        model_label = ttk.Label(f, text="Speech model")
        model_label.grid(row=row, column=0, sticky="w", pady=2)
        self.var_model = ttk.Combobox(f, values=[cfg.MODEL_LABELS[m] for m in cfg.MODELS],
                                      state="readonly", width=42)
        self.var_model.set(cfg.MODEL_LABELS.get(s["model"], cfg.MODEL_LABELS[cfg.MODELS[0]]))
        self.var_model.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        ttk.Label(f, text="No CUDA? First setup uses base.en on CPU").grid(
            row=row, column=2, sticky="w")
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

        save_button = ttk.Button(f, text="Save", command=self._save)
        save_button.grid(row=row, column=0, sticky="w")
        self.status = ttk.Label(f, text="")
        self.status.grid(row=row, column=1, columnspan=2, sticky="w", padx=10)

        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, add_button, "a")
        _add_access_key(root, remove_button, "r")
        _add_access_key(root, startup_button, "o")
        _add_access_key(root, self.retry_model_button, "m")
        _add_access_key(root, save_button, "s")
        _bind_window_command(root, "<Control-s>", self._save)
        _bind_window_command(root, "<Escape>", self._close)
        root.update_idletasks()
        for label, control in (
                (hotkey_label, self.var_hotkey),
                (microphone_label, self.var_device),
                (model_label, self.var_model),
                (suffix_label, self.var_suffix),
                (spoken_label, self.spoken_entry),
                (replacement_label, self.replacement_entry)):
            _label_control(label, control)
        _name_control(
            self.var_hotkey, "Dictation hotkey. " + ALTGR_HOTKEY_GUIDANCE)
        _name_control(self.listbox, "Dictionary rules")
        _mark_live_region(self.model_status)
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
        if status == "ready" and self.app.transcriber.loaded(selected):
            text = "Speech model ready" + ((" — " + detail) if detail else "")
        elif status == "error":
            text = "Speech model needs attention" + (
                (" — " + detail) if detail else "")
        else:
            text = (
                "Preparing selected speech model… Dictation is unavailable "
                "until it is ready.")
        _set_accessible_text(self.model_status, text)
        self.retry_model_button.config(
            state="normal" if status == "error" else "disabled")
        self.root.after(300, self._poll_model)

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
        s = self.app.settings
        label_to_value = {v: k for k, v in cfg.MODEL_LABELS.items()}
        old_model = s.get("model", cfg.DEFAULTS["model"])
        s["hotkey"] = self.var_hotkey.get() or cfg.DEFAULTS["hotkey"]
        s["trigger"] = self.var_trigger.get()
        old_input_device = s.get("input_device", cfg.DEFAULTS["input_device"])
        s["input_device"] = self.device_values.get(
            self.var_device.get(), cfg.DEFAULTS["input_device"])
        if s["input_device"] != old_input_device:
            self.app.input_device = None
        s["model"] = label_to_value.get(self.var_model.get(), cfg.DEFAULTS["model"])
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
        if s["model"] != old_model:
            self.app.prepare_configured_model()
        if self.app.apply_autostart():
            status = "Saved. Changes apply immediately."
        else:
            status = (
                "Saved, but Start with Windows was not updated. "
                "Open Startup Settings to review it.")
        _set_accessible_text(self.status, status)

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app.settings_window = None


class ScratchpadWindow:
    def __init__(self, app):
        self.app = app
        self.root = None
        self.window_handle = 0
        _window_host().submit(self._build)

    def _build(self):
        root = _interactive_window("Presspeech - Try Dictation")
        self.root = root
        root.geometry("480x280")
        self.text = tk.Text(root, wrap="word", font=("Segoe UI", 12))
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.btn = ttk.Button(root, text="Dictate (or use the hotkey)", command=self.toggle)
        self.btn.pack(pady=(0, 8))
        root.protocol("WM_DELETE_WINDOW", self._close)
        _add_access_key(root, self.btn, "d")
        _bind_window_command(root, "<Escape>", self._close)
        root.update_idletasks()
        root.after_idle(self.text.focus_set)
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        client_handle = root.winfo_id()
        self.window_handle = int(user32.GetParent(client_handle) or client_handle)

    def toggle(self):
        if self.app.recording:
            self.app.stop_recording()
            _set_accessible_text(self.btn, "Dictate (or use the hotkey)")
        else:
            if self.app.start_recording():
                _set_accessible_text(self.btn, "Stop Dictation")

    def append_text(self, text):
        def do():
            self.text.insert("end", text)
            self.text.see("end")
        try:
            self.root.after(0, do)
        except Exception:
            pass

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.window_handle = 0
        self.app.scratchpad = None
