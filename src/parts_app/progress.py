"""Thread-local procedure progress; only worker jobs persist updates, never supplier reads."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

REPORTER: ContextVar[Callable[[str], None] | None] = ContextVar("reporter", default=None)
STEP: ContextVar[str] = ContextVar("step", default="Preparing component")


@contextmanager
def track(callback: Callable[[str], None]):
    """Attach progress reporting to this job without sharing state across threads."""
    reporter = REPORTER.set(callback)
    step = STEP.set("Preparing component")
    try:
        yield
    finally:
        REPORTER.reset(reporter)
        STEP.reset(step)


def step(message: str):
    """Begin a named procedure phase and publish its description."""
    STEP.set(message)
    activity("")


def activity(message: str):
    """Publish actual activity within the current phase, without fabricating progress."""
    reporter = REPORTER.get()
    if reporter:
        reporter(f"{STEP.get()} — {message}" if message else STEP.get())
