import logging
import os
import time
import threading
from abc import ABC

import configparser
from multiprocessing import Queue

import requests
from bs4 import BeautifulSoup

from resume import DownloadCancelled, ResumeJournal, atomic_bytes, valid_file, safe_folder_component, stream_pdf
from transfer import RateGate, TransferMetrics, bounded_parallel


class LibraryDownloader(ABC):
    PAUSE_SEC = 1
    PARALLEL_FILES = 1  # Override only for independently downloadable files.
    RETRY_NUM = 3
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    CONFIG_FILE = "config.ini"
    CONFIG_LOGIN = "login"
    CONFIG_PASSWORD = "password"
    CONFIG_FOLDER = "folder"

    current_section: str = NotImplemented
    login: str = NotImplemented
    password: str = NotImplemented
    last_page: int = NotImplemented
    folder: str = NotImplemented
    root_folder: str = NotImplemented
    section_folder: str = NotImplemented

    @classmethod
    def download_book(cls, book_url, queue: Queue):
        pass

    def check_cancel(self):
        if getattr(self, "cancel_event", None) is not None and self.cancel_event.is_set():
            self.was_cancelled = True
            raise DownloadCancelled()

    def make_pause(self):
        self.check_cancel()
        event = getattr(self, "cancel_event", None)
        if event is not None:
            if event.wait(self.PAUSE_SEC):
                raise DownloadCancelled()
        else:
            time.sleep(self.PAUSE_SEC)

    def _metrics(self):
        if not hasattr(self, "transfer_metrics"):
            self.transfer_metrics = TransferMetrics()
        return self.transfer_metrics

    def _rate_gate(self):
        if not hasattr(self, "request_gate"):
            self.request_gate = RateGate()
        return self.request_gate

    def http_session(self):
        """A separate pooled requests.Session per worker (Session is not thread-safe)."""
        if not hasattr(self, "_http_local"):
            self._http_local = threading.local()
            self._all_http_sessions = []
            self._sessions_lock = threading.Lock()
            self._owner_thread = threading.get_ident()
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": self.USER_AGENT})
            # RGO may have obtained an ordinary cookie on the book/viewer page.
            # Clone before starting the worker requests; never share a Session.
            if getattr(self, "session", None) is not None:
                session.cookies.update(self.session.cookies)
            self._http_local.session = session
            with self._sessions_lock:
                self._all_http_sessions.append(session)
        return session

    def close_http(self):
        for session in getattr(self, "_all_http_sessions", []):
            session.close()
        self._all_http_sessions = []

    def http_get(self, url, **kwargs):
        return self._rate_gate().run(
            lambda: self.http_session().get(url, **kwargs), self.check_cancel)

    def retry_wait(self, attempt, response=None):
        retry_after = None
        if response is not None and getattr(response, "status_code", None) in (429, 503):
            retry_after = getattr(response, "headers", {}).get("Retry-After")
        self._rate_gate().delay(
            attempt, retry_after, rate_limited=(getattr(response, "status_code", None) == 429))
        self._rate_gate().wait(self.check_cancel)

    def parallel(self, items, worker, *, on_done=None, workers=None):
        return bounded_parallel(items, worker, on_done=on_done,
                                workers=workers or self.PARALLEL_FILES,
                                check_cancel=self.check_cancel)

    def report_transfer(self, *, completed, total, detail, detail_total, phase):
        """Throttled sub-page progress (main thread or worker-safe queue event)."""
        if not getattr(self, "detail_events", False) or getattr(self, "queue", None) is None:
            return
        now = time.monotonic()
        if detail < detail_total and now - getattr(self, "_transfer_report_at", 0) < .3:
            return
        self._transfer_report_at = now
        stats = self._metrics().snapshot()
        self.queue.put_nowait({"type": "transfer", "completed": completed,
                               "total": total, "detail": detail,
                               "detail_total": detail_total, "phase": phase, **stats})

    def report_progress(self, complete, total, *, reused=False, phase="Загрузка страниц"):
        self.check_cancel()
        tracker = getattr(self, "resume_journal", None)
        if tracker is not None:
            tracker.total = int(total)
            tracker.done = int(complete)
            if reused:
                tracker.reused += 1
            tracker.save()
        q = getattr(self, "queue", None)
        if q is not None:
            if getattr(self, "detail_events", False):
                stats = self._metrics().snapshot(finished=int(complete), total=int(total),
                                                 reused=tracker.reused if tracker else 0)
                q.put_nowait({
                    "type": "progress", "completed": int(complete),
                    "total": int(total), "phase": phase,
                    "reused": tracker.reused if tracker else 0, **stats,
                })
            else:
                q.put_nowait(round(min(1.0, complete / max(1, total)), 3))

    def finalize_download(self, error=None):
        tracker = getattr(self, "resume_journal", None)
        if tracker is not None:
            status = "paused" if getattr(self, "was_cancelled", False) or isinstance(error, DownloadCancelled) else ("error" if error else "completed")
            tracker.save(status=status, message=str(error) if error else "")

    def file_ready(self, filename, kind="image"):
        return valid_file(filename, kind)

    def save_downloaded_bytes(self, filename, content, kind="image"):
        self.check_cancel()
        if self.file_ready(filename, kind):
            return True
        atomic_bytes(filename, content, kind)
        self._metrics().add(len(content))
        return False

    def save_pdf_stream(self, url, destination, *, headers=None):
        previous = 0
        def on_bytes(done, total):
            nonlocal previous
            self._metrics().add(max(0, done - previous))
            previous = done
            q = getattr(self, "queue", None)
            if q is not None and getattr(self, "detail_events", False):
                stats = self._metrics().snapshot()
                if self.current_section == "NEBCHR" and total:
                    q.put_nowait({"type": "progress", "completed": done,
                                  "total": total, "phase": "Загрузка PDF", "reused": 0,
                                  **stats})
                else:
                    # PGPB downloads many independent PDFs: a single fragment
                    # must never override the overall pages/merge progress.
                    now = time.monotonic()
                    if now - getattr(self, "_pdf_report_at", 0) >= .4:
                        self._pdf_report_at = now
                        q.put_nowait({"type": "metrics", "phase": "Загрузка PDF-фрагментов", **stats})
        for attempt in range(self.RETRY_NUM):
            self.check_cancel()
            self._rate_gate().wait(self.check_cancel)
            try:
                return self._rate_gate().run(
                    lambda: stream_pdf(url, destination, headers=headers,
                                       session=self.http_session(), check_cancel=self.check_cancel,
                                       progress_callback=on_bytes), self.check_cancel)
            except (requests.RequestException, RuntimeError) as exc:
                text = str(exc)
                retryable = isinstance(exc, requests.RequestException) or any(
                    f"HTTP {status}" in text for status in (429, 500, 502, 503, 504))
                if not retryable or attempt == self.RETRY_NUM - 1:
                    raise
                # Stream errors retain .part so the next attempt may use Range.
                if "HTTP 429" in text:
                    self._rate_gate().delay(attempt, rate_limited=True)
                    self._rate_gate().wait(self.check_cancel)
                else:
                    self.retry_wait(attempt)
        raise RuntimeError("Не удалось скачать PDF")

    def init_common(self):
        self.transfer_metrics = TransferMetrics()
        self.request_gate = RateGate()
        self._http_local = threading.local()
        self._all_http_sessions = []
        self._sessions_lock = threading.Lock()
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    def init_authorized_access(self, config=None):
        self.init_common()
        if config is None:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_FILE, encoding='utf-8')
        if not config.has_section(self.current_section):
            raise Exception(f"No configuration for {self.current_section}")
        if not config.has_option(self.current_section, self.CONFIG_LOGIN):
            raise Exception(f"No property for {self.CONFIG_LOGIN}")
        if not config.has_option(self.current_section, self.CONFIG_PASSWORD):
            raise Exception(f"No property for {self.CONFIG_PASSWORD}")
        if not config.has_option(self.current_section, self.CONFIG_FOLDER):
            self.root_folder = "."
        else:
            self.root_folder = config.get(self.current_section, self.CONFIG_FOLDER)
        logging.info(f"Using folder {os.path.abspath(self.root_folder)}")
        self.login = config.get(self.current_section, self.CONFIG_LOGIN)
        self.password = config.get(self.current_section, self.CONFIG_PASSWORD)

    def init_non_authorized_access(self, config=None):
        self.init_common()
        if config is None:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_FILE, encoding='utf-8')
        if not config.has_option(self.current_section, self.CONFIG_FOLDER):
            self.root_folder = "."
        else:
            self.root_folder = config.get(self.current_section, self.CONFIG_FOLDER)
        logging.info(f"Using folder {os.path.abspath(self.root_folder)}")

    def create_folders(self, book_id):
        self.folder = os.path.join(self.root_folder, self.current_section + "_" + safe_folder_component(book_id))
        os.makedirs(self.folder, exist_ok=True)
        self.resume_journal = ResumeJournal(self.folder, self.current_section)

    def create_common_section_folder(self):
        self.section_folder = os.path.join(self.root_folder, self.current_section)
        os.makedirs(self.section_folder, exist_ok=True)

    def get_page_content(self, url, additional_headers=None):
        if additional_headers is None:
            additional_headers = dict()
        i = 0
        result = None
        headers = {"User-Agent": self.USER_AGENT}
        headers.update(additional_headers)
        while i < self.RETRY_NUM:
            self.check_cancel()
            try:
                result = self.http_get(url, headers=headers, timeout=30)
            except requests.RequestException:
                i += 1
                if i >= self.RETRY_NUM:
                    raise
                self.retry_wait(i - 1)
                continue
            i += 1
            if result.status_code in (429, 500, 502, 503, 504):
                logging.warning("HTTP %s when downloading %s", result.status_code, url)
                if i < self.RETRY_NUM:
                    self.retry_wait(i - 1, result)
            elif result.status_code == 200:
                break
            else:
                raise Exception(f"Error downloading {url}: {result.status_code}")
        if result.status_code != 200:
            raise Exception(f"Error downloading {url}: {result.status_code}")
        return result

    def save_image(self, url, output_filename):
        if self.file_ready(output_filename):
            return True
        result = self.get_page_content(url)
        self.save_downloaded_bytes(output_filename, result.content, "image")
        return False

    def download_html(self, url, additional_headers=None):
        if additional_headers is None:
            additional_headers = dict()
        result = self.get_page_content(url, additional_headers)
        html = result.text
        return BeautifulSoup(html, features="html5lib")
