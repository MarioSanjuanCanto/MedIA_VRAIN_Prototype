"""Bounded diagnostics and progress shared by synchronous tools and background jobs."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import RLock
from time import perf_counter

_reporter = ContextVar("media_progress", default=None)
_trace = ContextVar("media_trace", default=None)
_cancel_event = ContextVar("media_cancel_event", default=None)
_recent = deque(maxlen=100)
_lock = RLock()


class CancellationRequested(Exception):
    """Internal cooperative-cancellation signal for background operations."""


def current_cancel_event():
    """Return the current job's thread-safe cancellation event, when present."""
    return _cancel_event.get()


def check_cancelled(event=None):
    """Raise at an explicit safe checkpoint after cancellation was requested."""
    selected = event if event is not None else _cancel_event.get()
    if selected is not None and selected.is_set():
        raise CancellationRequested


def progress(stage: str, **details):
    callback = _reporter.get()
    if callback:
        callback(stage, details)


@contextmanager
def stage(name: str):
    started = perf_counter()
    progress(name)
    try:
        yield
    finally:
        trace = _trace.get()
        if trace is not None:
            trace[name] = round(trace.get(name, 0) + perf_counter() - started, 4)


def measured(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        trace = {}
        token = _trace.set(trace)
        started = perf_counter()
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict):
                result["timings_seconds"] = trace
            elif isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        item["timings_seconds"] = trace
            return result
        finally:
            trace["total"] = round(perf_counter() - started, 4)
            _trace.reset(token)
            with _lock:
                _recent.append({"operation": fn.__name__, **trace})
    return run


def recent_timings():
    with _lock:
        return list(_recent)
