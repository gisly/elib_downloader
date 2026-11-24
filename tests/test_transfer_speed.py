"""Offline tests for bounded parallel speed-up and safe resumability."""

import configparser
import io
import os
import queue
import threading
import time
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from PIL import Image

try:
    from PyPDF2 import PdfReader, PdfWriter
except ImportError:
    from pypdf import PdfReader, PdfWriter

from abstract_lib_downloader import LibraryDownloader
from kazneb_downloader import KAZNEBDownloader
from pgpb_downloader import PGPBDownloader
from prlib_downloader import PRlibDownloader
from rgo_downloader import RGODownloader
from shpl_downloader import SHPLDownloader
from resume import atomic_bytes, valid_file
from transfer import RateGate, TransferMetrics, bounded_parallel, readable_eta, readable_speed


def cfg(source, root):
    config = configparser.ConfigParser()
    config[source] = {"folder": str(root)}
    return config


def img(kind="JPEG"):
    result = io.BytesIO()
    Image.new("RGB", (10, 10), "orange").save(result, kind)
    return result.getvalue()


def small_pdf(width):
    result = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    writer.write(result)
    return result.getvalue()


def tracking_worker(save):
    """Observe overlapping work without relying on exact task completion order."""
    lock = threading.Lock()
    state = {"current": 0, "peak": 0, "threads": set()}

    def worker(*args):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            state["threads"].add(threading.get_ident())
        try:
            time.sleep(.015)
            return save(*args)
        finally:
            with lock:
                state["current"] -= 1

    return worker, state


def test_parallel_is_bounded_and_processes_completions_on_caller_thread():
    origin = threading.get_ident()
    callbacks = []
    worker, state = tracking_worker(lambda value: value * 10)
    bounded_parallel(range(12), worker, workers=3,
                     on_done=lambda item, answer: callbacks.append((item, answer, threading.get_ident())))
    assert 1 < state["peak"] <= 3
    assert {number for number, value, thread in callbacks} == set(range(12))
    assert all(value == number * 10 and thread == origin for number, value, thread in callbacks)


def test_parallel_exception_stops_scheduling_and_preserves_existing_files(tmp_path):
    saved = tmp_path / "good.jpeg"

    def worker(n):
        if n == 2:
            raise ConnectionError("Обрыв")
        atomic_bytes(saved.with_name(f"{n}.jpeg"), img())
        return n

    with pytest.raises(ConnectionError, match="Обрыв"):
        bounded_parallel(range(50), worker, workers=3)
    assert len(list(tmp_path.glob("*.jpeg"))) < 10  # no unbounded speculative requests


def test_rate_gate_respects_retry_after_and_enters_conservative_mode():
    gate = RateGate()
    gate.delay(0, "0", rate_limited=True)
    assert gate._serialize_until > time.monotonic()
    assert gate._not_before <= time.monotonic()
    calls = []
    assert gate.run(lambda: calls.append("ok") or 5) == 5
    assert calls == ["ok"]
    gate.delay(9, "999999")
    assert 0 <= gate._not_before - time.monotonic() <= 60.1


def test_metrics_thread_safe_and_display_helpers():
    meter = TransferMetrics()
    threads = [threading.Thread(target=lambda: [meter.add(1024) for _ in range(100)])
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    result = meter.snapshot(finished=1, total=4)
    assert result["bytes_downloaded"] == 409600
    assert result["speed_bps"] > 0
    assert result["eta_seconds"] is not None
    assert readable_speed(2048) == "2.0 КБ/с"
    assert readable_eta(3661) == "1 ч 1 мин"
    assert readable_eta(None) == "—"


def test_per_thread_http_sessions_are_pooled_but_not_shared(monkeypatch, tmp_path):
    import abstract_lib_downloader
    created = []

    class Session:
        def __init__(self):
            self.headers = {}
            self.cookies = {}
            self.calls = []
            created.append(self)

        def get(self, url, **kwargs):
            self.calls.append(url)
            return type("Resp", (), {"status_code": 200, "content": b"ok"})()

        def close(self):
            pass

    monkeypatch.setattr(abstract_lib_downloader.requests, "Session", Session)
    d = SHPLDownloader(cfg("SHPL", tmp_path))
    d.http_get("https://example.invalid/a")
    d.http_get("https://example.invalid/b")
    assert len(created) == 1
    assert created[0].calls == ["https://example.invalid/a", "https://example.invalid/b"]

    def task(_):
        d.http_get("https://example.invalid/page")
        time.sleep(.02)

    d.parallel(range(9), task)
    assert len(created) == 4  # owner + 3 isolated worker sessions
    assert sum(len(session.calls) for session in created) == 11
    d.close_http()


def test_prlib_known_tile_grid_downloads_concurrently(tmp_path):
    d = PRlibDownloader(cfg("PRLIB", tmp_path))
    d.create_folders("123")
    d.queue = queue.Queue()
    d.detail_events = True
    d._current_page_index = 1
    d._current_page_total = 1

    def save(folder, number, _url):
        atomic_bytes(os.path.join(folder, f"{number:05d}.jpg"), img())
        return True

    d.download_jpeg, state = tracking_worker(save)
    d.download_page("A", "B", {"f": "page.tif", "d": [{"w": 512, "h": 512}]})
    assert 1 < state["peak"] <= 3
    folder = tmp_path / "PRLIB_123" / "page"
    assert all(valid_file(folder / f"{n:05d}.jpg") for n in range(4))
    events = list(d.queue.queue)
    assert events and events[-1]["detail"] == 4
    d.concatenate_page_tiles(str(folder) + ".jpg", str(folder), 512)
    assert valid_file(str(folder) + ".jpg")


def test_prlib_unknown_tile_count_stays_serial(tmp_path):
    d = PRlibDownloader(cfg("PRLIB", tmp_path))
    d.create_folders("123")
    requested = []

    def download(_folder, number, _url):
        requested.append(number)
        return number < 3

    d.download_jpeg = download
    d.download_page("A", "B", {"f": "unknown.tif", "d": [{"w": 512}]})
    assert requested == [0, 1, 2, 3]


def test_rgo_pages_parallel_and_resume(tmp_path):
    d = RGODownloader(cfg("RGO", tmp_path))
    d.queue = queue.Queue()
    d._resolve_view_url = Mock(return_value="https://elib.rgo.ru/safe-view/123456789/77/1/YQ==")
    d.download_html = Mock(return_value=BeautifulSoup('<div data-total-pages="7"></div>', "html.parser"))

    def save(_url, number, folder):
        atomic_bytes(os.path.join(folder, f"{number:05d}.png"), img("PNG"))
        return True

    d.download_page, state = tracking_worker(save)
    d.process_book("https://elib.rgo.ru/handle/123456789/77")
    assert 1 < state["peak"] <= 2
    assert d.download_page.__name__ == "worker"
    assert len(list((tmp_path / "RGO_77").glob("*.png"))) == 7
    requests_before = sum(1 for path in (tmp_path / "RGO_77").glob("*.png"))
    d.download_page = Mock(side_effect=AssertionError("already downloaded"))
    d.process_book("https://elib.rgo.ru/handle/123456789/77")
    d.download_page.assert_not_called()
    assert requests_before == 7
    d.close_http()


@pytest.mark.parametrize("adapter,source,extension", [
    (SHPLDownloader, "SHPL", "jpeg"),
    (KAZNEBDownloader, "KAZNEB", "png"),
])
def test_single_image_adapters_fetch_in_parallel(tmp_path, adapter, source, extension):
    d = adapter(cfg(source, tmp_path))
    d.queue = queue.Queue()
    d.create_folders("77")
    d.extract_page_ids = Mock(return_value=list(range(8)))

    def save(_page, number):
        atomic_bytes(os.path.join(d.folder, f"{number:05d}.{extension}"),
                     img("PNG" if extension == "png" else "JPEG"))

    d.download_page, state = tracking_worker(save)
    d.process_book("77")
    assert 1 < state["peak"] <= 3
    assert len(list((tmp_path / f"{source}_77").glob(f"*.{extension}"))) == 8


def test_pgpb_parallel_fragments_merge_in_source_order(tmp_path):
    d = PGPBDownloader(cfg("PGPB", tmp_path))
    d.queue = queue.Queue()
    d.create_folders("77")
    pages = [(str(index), f"/{index}.pdf") for index in (1, 2, 3, 4)]
    d.extract_page_urls = Mock(return_value=pages)

    def save(page, folder):
        index = int(page[0])
        time.sleep(.02 * (5 - index))  # intentionally finish in reverse order
        atomic_bytes(os.path.join(folder, f"{index:05d}.pdf"), small_pdf(index * 100), "pdf")

    d.download_page = save
    d.process_book("77")
    with open(tmp_path / "PGPB_77" / "77.pdf", "rb") as f:
        output = PdfReader(f)
        assert [int(page.mediabox.width) for page in output.pages] == [100, 200, 300, 400]


def test_libfl_independent_jpeg_urls_parallel(tmp_path):
    pytest.importorskip("selenium")
    from libfl_downloader import LIBFLDownloader
    d = LIBFLDownloader.__new__(LIBFLDownloader)
    LibraryDownloader.init_common(d)
    d.folder = str(tmp_path)
    d.queue = queue.Queue()
    d.page_from = 1
    data = {"Path_HQ": "", "JPGFiles": [str(n) + ".jpg" for n in range(7)]}
    import json
    d.download_html = Mock(return_value=type("Html", (), {"text": f"var exemplar = {json.dumps(data)};"})())
    downloaded = []

    def get(url, additional_headers=None):
        downloaded.append(url)
        time.sleep(.015)
        return type("Resp", (), {"content": img()})()

    d.get_page_content = get
    d.download_pages("bookID=123")
    assert len(list(tmp_path.glob("*.jpg"))) == 7
    assert len(downloaded) == 7
    d.close_http()


def test_pdfreader_progress_poll_is_fast_and_browser_serial(monkeypatch):
    pytest.importorskip("selenium")
    import pdfreader_downloader
    d = pdfreader_downloader.PDFReaderDownloader.__new__(pdfreader_downloader.PDFReaderDownloader)
    d.driver = Mock()
    d.queue = queue.Queue()
    d.detail_events = True
    d.get_current_downloads = Mock(side_effect=[
        ["0$$$30$$$C:/downloads/book.pdf"],
        ["1$$$100$$$C:/downloads/book.pdf"],
    ])
    monkeypatch.setattr(pdfreader_downloader, "time", __import__("time"), raising=False)
    monkeypatch.setattr(pdfreader_downloader.time, "sleep", lambda _seconds: None)
    assert d.DOWNLOAD_POLL_SEC <= 1.0
    assert d.download_pages() == "C:/downloads/book.pdf"
    assert d.get_current_downloads.call_count == 2
