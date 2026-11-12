"""The queue of QC runs: one run at a time, in the order they were started.

Progress lives in memory (the page asks every second); the run's status is in the
database, so runs that were queued or running when the server stopped start again.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

log = logging.getLogger("sanger_seq_analyzer.server.jobs")


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    current: str = ""


class JobQueue:
    def __init__(self, store, executor=None):
        self.store = store
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="qc-run")
        self._own_executor = executor is None
        self.progress: dict[int, Progress] = {}
        self.cancel_flags: dict[int, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, run_id: int) -> None:
        with self._lock:
            self.progress[run_id] = Progress()
            self.cancel_flags[run_id] = threading.Event()
        self.executor.submit(self._run, run_id)

    def _run(self, run_id: int) -> None:
        flag = self.cancel_flags.get(run_id) or threading.Event()

        def progress(n: int, total: int, name: str) -> None:
            p = self.progress.setdefault(run_id, Progress())
            p.done, p.total, p.current = n, total, name

        try:
            self.store.execute(run_id, progress, flag.is_set)
        except Exception:  # execute() records failures itself; this is a last resort
            log.exception("run %s crashed", run_id)
        finally:
            with self._lock:
                self.progress.pop(run_id, None)
                self.cancel_flags.pop(run_id, None)

    def cancel(self, run_id: int) -> bool:
        flag = self.cancel_flags.get(run_id)
        if flag is None:
            return False
        flag.set()
        return True

    def resume(self) -> list[int]:
        """Queue again the runs the last server process did not finish."""
        ids = self.store.queued()
        for run_id in ids:
            self.submit(run_id)
        return ids

    def stop(self) -> None:
        """Let the current run finish (seconds); runs still waiting stay queued and start next time."""
        if self._own_executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
