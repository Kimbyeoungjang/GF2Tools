from __future__ import annotations

import traceback
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.result.emit(self.fn())
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


class ProgressWorker(QRunnable):
    """Worker whose callable receives a thread-safe progress(str) callback."""
    def __init__(self, fn: Callable[[Callable[[str], None]], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.result.emit(self.fn(lambda text: self.signals.progress.emit(str(text))))
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()


# PySide normally lets QThreadPool own an auto-deleting QRunnable, but keeping a
# Python-side strong reference until ``finished`` avoids wrapper lifetime edge
# cases when a runnable only exists in a local variable.  The registry is tiny
# (bounded by active work) and every Worker/ProgressWorker emits finished from a
# finally block.
_ACTIVE_WORKERS: dict[int, QRunnable] = {}


def start_worker(pool: QThreadPool, worker: Worker | ProgressWorker):
    """Start *worker* while retaining its Python wrapper until completion."""
    key = id(worker)
    _ACTIVE_WORKERS[key] = worker
    worker.signals.finished.connect(lambda k=key: _ACTIVE_WORKERS.pop(k, None))
    pool.start(worker)
    return worker


def active_worker_count() -> int:
    """Small diagnostic hook used by tests/support tooling."""
    return len(_ACTIVE_WORKERS)

def run_worker(
    pool: QThreadPool,
    fn: Callable[[], Any],
    *,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
) -> Worker:
    """Create, wire and start a normal worker with one shared pattern.

    UI modules previously repeated the same Worker/signals/start_worker sequence.
    Keeping the wiring here makes lifetime retention and future cancellation/error
    policy changes apply consistently without touching every page/dialog.
    """
    worker = Worker(fn)
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    return start_worker(pool, worker)


def run_progress_worker(
    pool: QThreadPool,
    fn: Callable[[Callable[[str], None]], Any],
    *,
    on_progress: Callable[[str], None] | None = None,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
) -> ProgressWorker:
    """Run a non-cancellable job that reports short human-readable progress text."""
    worker = ProgressWorker(fn)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    return start_worker(pool, worker)




class CancellationToken:
    """Thread-safe cooperative cancellation flag shared by UI jobs."""
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class CancellableWorkerHandle:
    """Small UI-side handle for cooperative cancellation."""
    def __init__(self, token: CancellationToken):
        self.token = token

    def cancel(self) -> None:
        self.token.cancel()

    @property
    def cancelled(self) -> bool:
        return self.token.is_cancelled()


def run_cancellable_worker(
    pool: QThreadPool,
    fn: Callable[[Callable[[], bool]], Any],
    *,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
) -> CancellableWorkerHandle:
    token = CancellationToken()
    worker = Worker(lambda: fn(token.is_cancelled))
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    start_worker(pool, worker)
    return CancellableWorkerHandle(token)


def run_cancellable_progress_worker(
    pool: QThreadPool,
    fn: Callable[[Callable[[str], None], Callable[[], bool]], Any],
    *,
    on_progress: Callable[[str], None] | None = None,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
) -> CancellableWorkerHandle:
    token = CancellationToken()
    worker = ProgressWorker(lambda progress: fn(progress, token.is_cancelled))
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    start_worker(pool, worker)
    return CancellableWorkerHandle(token)
