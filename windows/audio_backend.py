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

    def _release(self, token):
        # A reset cannot hold this lock while this token is outstanding.
        with self._lock:
            self._users.remove(token)

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
            # The caller's operation is idle between discovery and retry.
            # Every other lease, including a stream being closed, vetoes reset.
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
            if self._closed:
                return
            # Keep the lease if native close fails: resetting a possibly live
            # handle would be unsafe. A later successful close releases it.
            self._stream.close()
            self._closed = True
            self._owner._release(self._token)
