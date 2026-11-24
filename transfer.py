"""Small bounded transfer scheduler, connection backoff and progress metrics.

Only independent HTTP file requests are parallelized.  Browser navigation
stays serial.  A worker never writes the progress journal or touches Tk.
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from email.utils import parsedate_to_datetime
import logging
import threading
import time
from datetime import datetime, timezone


class TransferMetrics:
    def __init__(self):
        self.started = time.monotonic()
        self.bytes_downloaded = 0
        self._lock = threading.Lock()

    def add(self, amount):
        with self._lock:
            self.bytes_downloaded += max(0, int(amount))

    def snapshot(self, *, finished=None, total=None, reused=0):
        with self._lock:
            size = self.bytes_downloaded
        elapsed = max(time.monotonic() - self.started, .001)
        # Throughput of actual transferred bytes.  Do not count cache hits.
        speed = size / elapsed if size else None
        eta = None
        if finished is not None and total is not None:
            # Cache hits do not imply network throughput.  ETA is approximate.
            actual = max(0, finished - reused)
            if actual and total > finished:
                eta = min(86400 * 30, int(elapsed / actual * (total - finished)))
        return {"bytes_downloaded": size, "speed_bps": speed, "eta_seconds": eta}


class RateGate:
    """All threads for a source wait on a shared cool-down after HTTP 429/503."""

    def __init__(self):
        self._lock = threading.Lock()
        self._not_before = 0.0
        self._serialize_until = 0.0
        self._request_lock = threading.Lock()

    def wait(self, check_cancel=None):
        while True:
            with self._lock:
                remaining = self._not_before - time.monotonic()
            if remaining <= 0:
                return
            if check_cancel:
                check_cancel()
            time.sleep(min(.15, remaining))

    def run(self, fetch, check_cancel=None):
        self.wait(check_cancel)
        with self._lock:
            conservative = time.monotonic() < self._serialize_until
        if conservative:
            with self._request_lock:
                self.wait(check_cancel)
                return fetch()
        return fetch()

    def delay(self, attempt, retry_after=None, *, rate_limited=False):
        seconds = min(30.0, 1.0 * (2 ** min(attempt, 5)))
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except (ValueError, TypeError):
                try:
                    date = parsedate_to_datetime(retry_after)
                    seconds = (date - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pass
        seconds = max(0.0, min(60.0, seconds))
        with self._lock:
            now = time.monotonic()
            self._not_before = max(self._not_before, now + seconds)
            if rate_limited:
                # Until the server recovers, start requests one at a time.
                self._serialize_until = max(self._serialize_until, now + 60.0)
        logging.info("HTTP request cooldown: %.1f seconds", seconds)


def bounded_parallel(items, worker, *, on_done=None, workers=3, check_cancel=None):
    """Use <=3 outstanding requests; completed output is processed on caller thread.

    Avoid submitting the whole book at once.  If a task fails, stop launching
    work, cancel queued tasks and propagate the error after active tasks end.
    A worker may finish after the cancellation is requested; its output still
    lands in a validated, atomically-written file for later resume.
    """
    workers = max(1, min(3, int(workers)))
    iterator = iter(items)
    if workers == 1:
        for item in iterator:
            if check_cancel:
                check_cancel()
            result = worker(item)
            if on_done:
                on_done(item, result)
        return
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="elib-http") as executor:
        pending = {}

        def submit_next():
            if check_cancel:
                check_cancel()
            try:
                item = next(iterator)
            except StopIteration:
                return False
            pending[executor.submit(worker, item)] = item
            return True

        try:
            for _ in range(workers):
                if not submit_next():
                    break
            while pending:
                if check_cancel:
                    check_cancel()
                done, _ = wait(pending, timeout=.15, return_when=FIRST_COMPLETED)
                for future in done:
                    item = pending.pop(future)
                    result = future.result()
                    if on_done:
                        on_done(item, result)
                    submit_next()
        except BaseException:
            for future in pending:
                future.cancel()
            raise


def readable_speed(bytes_per_second):
    if not bytes_per_second:
        return "—"
    value = float(bytes_per_second)
    for unit in ("Б/с", "КБ/с", "МБ/с", "ГБ/с"):
        if value < 1024 or unit == "ГБ/с":
            return f"{value:.0f} {unit}" if unit == "Б/с" else f"{value:.1f} {unit}"
        value /= 1024


def readable_eta(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} с"
    if seconds < 3600:
        return f"{seconds // 60} мин {seconds % 60} с"
    return f"{seconds // 3600} ч {(seconds % 3600) // 60} мин"
