"""Tkinter windows: settings, scratchpad, and a focus-safe status overlay."""

import ctypes
import os
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import config as cfg
import updates


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
            root.configure(bg="#202124")
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.94)

            frame = tk.Frame(root, bg="#202124", padx=12, pady=7)
            frame.pack(fill="both", expand=True)
            dot = tk.Label(frame, text="\u25cf", bg="#202124", fg="#ff5a5f",
                           font=("Segoe UI", 11))
            dot.pack(side="left")
            label = tk.Label(frame, text="Listening\u2026", bg="#202124", fg="#ffffff",
                             font=("Segoe UI", 10, "bold"), padx=7)
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

            width, height = 224, 38

            def apply_command(command):
                if command == "close":
                    root.destroy()
                    return False
                if command == "hide":
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    return True
                text, colour = self._STATES[command]
                label.configure(text=text)
                dot.configure(fg=colour)
                area = self._work_area()
                x = area.left + ((area.right - area.left - width) // 2)
                y = area.bottom - height - 42
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

            root.after(0, poll)
            root.mainloop()
        except Exception:
            # Dictation must remain usable even if Windows refuses the overlay.
            return


class SetupWindow:
    """Small first-run readiness screen; all processing remains local."""

    def __init__(self, app):
        self.app = app
        self.root = None
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        root = tk.Tk()
        self.root = root
        root.title("Welcome to Presspeech")
        root.resizable(False, False)
        root.lift()
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Presspeech is almost ready",
                  font=("Segoe UI", 16, "bold")).grid(
                      row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text=("Hold %s, speak, then release to type at the cursor.\n"
                  "Speech stays on this PC; no audio or transcripts are uploaded."
                  % self.app.settings.get("hotkey", "right alt").title()),
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))

        ttk.Label(frame, text="Speech model").grid(row=2, column=0, sticky="w")
        self.model_label = ttk.Label(frame, text="Preparing…")
        self.model_label.grid(row=2, column=1, sticky="w", padx=(12, 0))
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=260)
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 13))
        self.progress.start(12)

        ttk.Label(frame, text="Microphone").grid(row=4, column=0, sticky="w")
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

        ttk.Label(frame, text="Push-to-talk").grid(row=5, column=0, sticky="w")
        ttk.Label(frame, text=self.app.settings.get("hotkey", "right alt").title()).grid(
            row=5, column=1, sticky="w", padx=(12, 0), pady=3)

        self.autostart = tk.BooleanVar(
            value=self.app.settings.get("autostart", True))
        ttk.Checkbutton(frame, text="Start Presspeech with Windows",
                        variable=self.autostart).grid(
                            row=6, column=0, columnspan=2, sticky="w", pady=(10, 14))

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="Try Dictation",
                   command=self.app.open_scratchpad).pack(side="left")
        ttk.Button(buttons, text="Finish Setup",
                   command=self._finish).pack(side="right")

        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll_model)
        root.mainloop()
        self.root = None
        self.app.setup_window = None

    def _poll_model(self):
        if self.root is None:
            return
        status = getattr(self.app, "model_status", "pending")
        detail = getattr(self.app, "model_status_detail", "")
        labels = {
            "pending": "Waiting to start…",
            "loading": detail or "Downloading or loading…",
            "ready": "Ready — " + detail,
            "error": "Needs attention — " + detail,
        }
        self.model_label.config(text=labels.get(status, detail or status))
        if status in ("ready", "error"):
            self.progress.stop()
            self.progress.config(mode="determinate", value=100 if status == "ready" else 0)
        else:
            self.root.after(300, self._poll_model)

    def _finish(self):
        settings = self.app.settings
        selected = self.device_values.get(
            self.device.get(), cfg.DEFAULTS["input_device"])
        if selected != settings.get("input_device", cfg.DEFAULTS["input_device"]):
            self.app.input_device = None
        settings["input_device"] = selected
        settings["autostart"] = bool(self.autostart.get())
        settings["setup_complete"] = True
        cfg.save(settings)
        self.app.apply_autostart()
        self._close()

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


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
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        root = tk.Tk()
        self.root = root
        root.title("Presspeech Update")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
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
        ttk.Button(buttons, text="Later", command=self._close).pack(side="left")
        self.download_button = ttk.Button(
            buttons, text="Download Update", command=self._download)
        self.download_button.pack(side="right")
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        root.mainloop()
        self.root = None
        self.app.update_window = None

    def _download(self):
        self._discard_completed_download()
        self.cancel_download.clear()
        self.download_finished.clear()
        self.download_button.config(state="disabled")
        self.status.config(text="Downloading…")
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
                        self.status.config(text="Downloaded %.1f of %.1f GB" %
                                           (done / 1073741824, total / 1073741824))
                    else:
                        self.status.config(text="Downloaded %.1f MB" %
                                           (done / 1048576))
                elif event[0] == "error":
                    self.status.config(text="Download failed")
                    self.download_button.config(state="normal")
                    messagebox.showerror("Update failed", event[1], parent=self.root)
                elif event[0] == "ready":
                    self.progress.config(value=self.progress["maximum"])
                    self.status.config(text="Verified and ready to install")
                    if messagebox.askyesno(
                            "Install update",
                            "Close Presspeech and run the verified installer now?",
                            parent=self.root):
                        try:
                            self.app.launch_update(event[1], self.update)
                        except Exception as exc:
                            self._discard_completed_download()
                            self.status.config(text="Install failed")
                            self.progress.config(value=0)
                            self.download_button.config(state="normal")
                            messagebox.showerror(
                                "Update failed", str(exc), parent=self.root)
                    else:
                        self._discard_completed_download()
                        self.status.config(text="Ready to download")
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


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.root = None
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        s = self.app.settings
        root = tk.Tk()
        self.root = root
        root.title("Presspeech Settings")
        root.resizable(False, False)
        f = ttk.Frame(root, padding=12)
        f.pack(fill="both", expand=True)

        row = 0

        ttk.Label(f, text="Dictation hotkey").grid(row=row, column=0, sticky="w", pady=2)
        self.var_hotkey = ttk.Combobox(f, values=cfg.HOTKEYS, state="readonly", width=14)
        self.var_hotkey.set(s["hotkey"])
        self.var_hotkey.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        row += 1

        ttk.Label(f, text="Trigger").grid(row=row, column=0, sticky="w", pady=2)
        self.var_trigger = tk.StringVar(value=s["trigger"])
        ttk.Radiobutton(f, text="Hold to talk", value="hold", variable=self.var_trigger).grid(
            row=row, column=1, sticky="w", padx=10)
        ttk.Radiobutton(f, text="Press to toggle", value="toggle", variable=self.var_trigger).grid(
            row=row, column=2, sticky="w")
        row += 1

        ttk.Label(f, text="Microphone").grid(row=row, column=0, sticky="w", pady=2)
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

        ttk.Label(f, text="Speech model").grid(row=row, column=0, sticky="w", pady=2)
        self.var_model = ttk.Combobox(f, values=[cfg.MODEL_LABELS[m] for m in cfg.MODELS],
                                      state="readonly", width=26)
        self.var_model.set(cfg.MODEL_LABELS.get(s["model"], cfg.MODEL_LABELS[cfg.MODELS[0]]))
        self.var_model.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        ttk.Label(f, text="GPU models need the NVIDIA runtime").grid(
            row=row, column=2, sticky="w", foreground="#666")
        row += 1

        ttk.Label(f, text="After pasting").grid(row=row, column=0, sticky="w", pady=2)
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
                        variable=self.var_autostart).grid(row=row, column=0, columnspan=3,
                                                          sticky="w", pady=2)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3,
                                                   sticky="ew", pady=8)
        row += 1

        ttk.Label(f, text="Dictionary (fix mishearings / spoken shortcuts):").grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1

        self.var_spoken = tk.StringVar()
        self.var_replace = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_spoken, width=18).grid(
            row=row, column=0, sticky="w", pady=2)
        ttk.Label(f, text="\u2192").grid(row=row, column=1)
        ttk.Entry(f, textvariable=self.var_replace, width=18).grid(
            row=row, column=2, sticky="w", padx=10, pady=2)
        row += 1

        ttk.Button(f, text="Add rule", command=self._add_rule).grid(
            row=row, column=0, sticky="w", pady=2)
        ttk.Button(f, text="Remove selected", command=self._remove_rule).grid(
            row=row, column=1, columnspan=2, sticky="w")
        row += 1

        self.listbox = tk.Listbox(f, width=52, height=6)
        self.listbox.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
        for spoken, replacement in s["dictionary"]:
            self.listbox.insert("end", "%s \u2192 %s" % (spoken, replacement))
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3,
                                                   sticky="ew", pady=8)
        row += 1

        ttk.Button(f, text="Save", command=self._save).grid(row=row, column=0, sticky="w")
        self.status = ttk.Label(f, text="", foreground="#666")
        self.status.grid(row=row, column=1, columnspan=2, sticky="w", padx=10)

        root.protocol("WM_DELETE_WINDOW", self._close)
        root.mainloop()
        self.root = None
        self.app.settings_window = None

    def _add_rule(self):
        spoken = self.var_spoken.get().strip()
        replacement = self.var_replace.get()
        if not spoken:
            return
        self.listbox.insert("end", "%s \u2192 %s" % (spoken, replacement))
        self.var_spoken.set("")
        self.var_replace.set("")

    def _remove_rule(self):
        selection = self.listbox.curselection()
        if selection:
            self.listbox.delete(selection[0])

    def _save(self):
        s = self.app.settings
        label_to_value = {v: k for k, v in cfg.MODEL_LABELS.items()}
        s["hotkey"] = self.var_hotkey.get() or cfg.DEFAULTS["hotkey"]
        s["trigger"] = self.var_trigger.get()
        old_input_device = s.get("input_device", cfg.DEFAULTS["input_device"])
        s["input_device"] = self.device_values.get(
            self.var_device.get(), cfg.DEFAULTS["input_device"])
        if s["input_device"] != old_input_device:
            self.app.input_device = None
        s["model"] = label_to_value.get(self.var_model.get(), cfg.DEFAULTS["model"])
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
        rules = []
        for line in self.listbox.get(0, "end"):
            if "\u2192" in line:
                spoken, replacement = line.split("\u2192", 1)
                rules.append([spoken.strip(), replacement.strip()])
        s["dictionary"] = rules
        cfg.save(s)
        self.app.apply_autostart()
        self.status.config(text="Saved. Hotkey changes apply immediately.")

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


class ScratchpadWindow:
    def __init__(self, app):
        self.app = app
        self.root = None
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        root = tk.Tk()
        self.root = root
        root.title("Presspeech - Try Dictation")
        root.geometry("480x280")
        self.text = tk.Text(root, wrap="word", font=("Segoe UI", 12))
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.btn = ttk.Button(root, text="Dictate (or use the hotkey)", command=self.toggle)
        self.btn.pack(pady=(0, 8))
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.mainloop()
        self.root = None
        self.app.scratchpad = None
        self.app.paste_target = "paste"

    def toggle(self):
        if self.app.recording:
            self.app.stop_recording()
            self.btn.config(text="Dictate (or use the hotkey)")
        else:
            if self.app.start_recording():
                self.app.paste_target = "scratchpad"
                self.btn.config(text="Stop")

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
