"""Progress reporting that stages can call without knowing who is listening.

The CLI ignores it; the UI's job runner installs a reporter per job thread.
"""

from __future__ import annotations

import threading
from typing import Callable

_local = threading.local()

Reporter = Callable[[float | None, str], None]


def set_reporter(fn: Reporter | None) -> None:
    _local.fn = fn


def report(fraction: float | None = None, message: str = "") -> None:
    """fraction in 0..1 (None = indeterminate), plus a short human message."""
    fn = getattr(_local, "fn", None)
    if fn:
        fn(fraction, message)


def current() -> Reporter | None:
    """The reporter for this thread, to hand to helper threads (reporters are thread-local)."""
    return getattr(_local, "fn", None)
