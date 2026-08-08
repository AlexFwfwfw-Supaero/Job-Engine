"""What the long-running actions are doing right now, for the page to show.

A single local user runs one search and one read at a time, so module-level
records are enough; there is no queue and nothing to persist. Starting a second
run while one is going is refused rather than doubling the work.

Each record owns the sentence it displays. The template renders that sentence on
first paint and the poller rewrites it thereafter, so the wording lives here and
nowhere else.

The counters are written by a worker thread and read by request threads. The
lock is what makes appending to `failed` safe; the integers would survive
without it, but a half-updated pair of counters would read as nonsense.
"""

from __future__ import annotations

import threading


class AiProgress:
    """Progress of the background model read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.total = 0
        self.done = 0
        self.failures: list[str] = []

    def start(self, total: int) -> None:
        with self._lock:
            self.running, self.total, self.done = True, total, 0
            self.failures = []

    def step(self) -> None:
        """One posting finished. Called per job so the count moves during the
        run instead of jumping from nothing to everything at the end."""
        with self._lock:
            self.done += 1

    def finish(self, done: int, failures: list[str] | None = None) -> None:
        with self._lock:
            self.running, self.done = False, done
            self.failures = list(failures or [])

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def text(self) -> str:
        if self.running:
            return f"{self.done}/{self.total} analysed"
        if not self.total:
            return ""
        tail = f", {self.failed} failed" if self.failed else ""
        return f"Model read {self.done} posting(s){tail}."

    def as_dict(self) -> dict:
        return {"running": self.running, "total": self.total,
                "done": self.done, "failed": self.failed,
                # Why each one failed, not just how many. A run that reports
                # "28 failed" and nothing else gives you nowhere to start.
                "failures": list(self.failures), "text": self.text}


class PollProgress:
    """Progress of the background employer sweep.

    Granularity is one employer: that is the unit `poll_all` iterates over, and
    how deep a board paginates is not knowable before fetching it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.total = 0
        self.done = 0
        self.current = ""
        self.matched = 0
        self.new = 0
        self.failed: list[str] = []

    def start(self, total: int) -> None:
        with self._lock:
            self.running, self.total = True, total
            self.done = self.matched = self.new = 0
            self.current = ""
            self.failed = []

    def starting(self, employer: str) -> None:
        with self._lock:
            self.current = employer

    def completed(self, report) -> None:
        with self._lock:
            self.done += 1
            self.matched += report.stored
            self.new += report.new
            if report.error:
                self.failed.append(f"{report.employer}: {report.error}")

    def finish(self) -> None:
        with self._lock:
            self.running, self.current = False, ""

    @property
    def text(self) -> str:
        if self.running:
            who = f"Searching {self.current}… " if self.current else "Searching… "
            return (f"{who}{self.done}/{self.total} employers, "
                    f"{self.matched} matched, {self.new} new")
        if not self.total:
            return ""
        parts = [f"Polled {self.total} employer(s)",
                 f"{self.matched} matched", f"{self.new} new"]
        if self.failed:
            # Named, not counted. poll_all swallows a broken source so the rest
            # of the sweep continues, and until now nothing surfaced that — a
            # parser that stopped working read as a quiet market.
            parts.append(f"{len(self.failed)} failed ({'; '.join(self.failed)})")
        return ", ".join(parts) + "."

    def as_dict(self) -> dict:
        return {"running": self.running, "total": self.total, "done": self.done,
                "current": self.current, "matched": self.matched,
                "new": self.new, "failed": list(self.failed), "text": self.text}


PROGRESS = AiProgress()
# The whole-set briefing is one call, but a slow one — a long prompt and a long
# answer. It gets its own record so it can run while postings are being read.
ADVICE = AiProgress()
POLL = PollProgress()
