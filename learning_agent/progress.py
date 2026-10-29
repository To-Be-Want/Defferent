"""Task-stage events only; never model private reasoning."""
from contextvars import ContextVar

callback = ContextVar('learning_progress', default=None)


def report(stage, **fields):
    emit = callback.get()
    if emit:
        emit({'type': 'study-agent-progress', 'stage': stage, **fields})
