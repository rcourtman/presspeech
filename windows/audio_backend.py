"""Coordinate process-wide PortAudio reset with every native audio operation."""

from contextlib import contextmanager
from functools import wraps
import threading


class AudioBackendBusy(RuntimeError):
    """The native backend is being refreshed; callers should retry later."""


class AudioBackend:
    def __init__(self, backend):
        self.backend = backend
        self._lock = threading.Lock()
        self._users = set()
        # A failed native close may leave a live handle. Keep both the handle
        # and its reservation after application cleanup drops its reference.
        self._failed_closes = set()
        self._initialized = True

    def _acquire(self):
        # Reset can involve a slow device driver. Never make a UI, recording
        # deadline, or another discovery wait for it while holding app state.
        if not self._lock.acquire(blocking=False):
            raise AudioBackendBusy("Audio devices are refreshing; try again.")
        try:
            token = object()
            self._users.add(token)
            return token
        finally:
            self._lock.release()

    def _release(self, token, closed_stream=None):
        # A reset cannot hold this lock while this token is outstanding.
        with self._lock:
            self._users.remove(token)
            if closed_stream is not None:
                self._failed_closes.discard(closed_stream)

    def _retain_failed_close(self, stream):
        with self._lock:
            self._failed_closes.add(stream)

    @contextmanager
    def operation(self):
        token = self._acquire()
        try:
            yield token
        finally:
            self._release(token)

    def guarded(self, function):
        @wraps(function)
        def call(*args, **kwargs):
            with self.operation():
                return function(*args, **kwargs)
        return call

    def open_input_stream(self, **kwargs):
        # Reserve before calling the driver: an in-flight constructor is just
        # as vulnerable to Pa_Terminate as an already attached capture stream.
        token = self._acquire()
        try:
            stream = self.backend.InputStream(**kwargs)
        except BaseException:
            self._release(token)
            raise
        return _InputStream(self, token, stream)

    def rescan(self, token, still_current):
        if not self._lock.acquire(blocking=False):
            return False
        try:
            failed_closes = tuple(self._failed_closes)
            cleanup_tokens = {stream._token for stream in failed_closes}
            # Only the requesting operation and retained failed-close handles
            # may participate. Active streams, constructors and other queries
            # veto both cleanup retries and reset.
            if self._users != ({token} | cleanup_tokens) or not still_current():
                return False
        finally:
            self._lock.release()

        # rescan is called on the discovery worker. Drivers may block in close,
        # so never hold the admission lock here. Each stream keeps its lease
        # until native close succeeds; new callers remain safe and responsive.
        for stream in failed_closes:
            if not stream.retry_close():
                return False

        if not self._lock.acquire(blocking=False):
            return False
        try:
            # A new operation or recording epoch may have arrived while close
            # was pending. Revalidate ownership before touching PortAudio.
            if self._users != {token} or not still_current():
                return False
            terminate = getattr(self.backend, "_terminate", None)
            initialize = getattr(self.backend, "_initialize", None)
            if terminate is None or initialize is None:
                return False
            if self._initialized:
                terminate()
                self._initialized = False
            # If a previous initialize failed, retry it without terminating an
            # uninitialized library (which would make recovery fail forever).
            initialize()
            self._initialized = True
            return True
        finally:
            self._lock.release()


class _InputStream:
    def __init__(self, owner, token, stream):
        self._owner = owner
        self._token = token
        self._stream = stream
        self._close_lock = threading.Lock()
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def close(self):
        with self._close_lock:
            self._close_locked()

    def retry_close(self):
        # A concurrent application cleanup owns this handle already. Do not
        # queue a second driver call or make discovery wait for its close lock.
        if not self._close_lock.acquire(blocking=False):
            return False
        try:
            try:
                self._close_locked()
            except Exception:
                return False
            return True
        finally:
            self._close_lock.release()

    def _close_locked(self):
        if self._closed:
            return
        try:
            self._stream.close()
        except BaseException:
            # Application cleanup intentionally catches native close failures.
            # Retaining the wrapper here makes a later worker rescan able to
            # retry safely even after that caller has discarded its reference.
            self._owner._retain_failed_close(self)
            raise
        self._closed = True
        self._owner._release(self._token, closed_stream=self)
