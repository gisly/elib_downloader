"""Offline tests for interrupted downloads, recovery and atomic files."""

import configparser
import io
import json
import os
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from bs4 import BeautifulSoup

try:
    from PyPDF2 import PdfWriter
except ImportError:
    from pypdf import PdfWriter

from abstract_lib_downloader import LibraryDownloader
from kazneb_downloader import KAZNEBDownloader
from pgpb_downloader import PGPBDownloader
from prlib_downloader import PRlibDownloader
from resume import (
    DownloadCancelled, ResumeJournal, atomic_bytes, folder_id,
    safe_folder_component, stream_pdf, valid_file,
)
from rgo_downloader import RGODownloader
from shpl_downloader import SHPLDownloader


def cfg(section, root):
    config = configparser.ConfigParser()
    config[section] = {"folder": str(root)}
    return config


def png_bytes():
    out = io.BytesIO()
    Image.new("RGB", (8, 6), "green").save(out, format="PNG")
    return out.getvalue()


def jpg_bytes():
    out = io.BytesIO()
    Image.new("RGB", (8, 6), "blue").save(out, format="JPEG")
    return out.getvalue()


def pdf_bytes():
    out = io.BytesIO()
    with PdfWriter() as writer:
        writer.add_blank_page(width=100, height=100)
        writer.write(out)
    return out.getvalue()


def test_atomic_image_rejects_html_and_preserves_previous_file(tmp_path):
    target = tmp_path / "page.png"
    atomic_bytes(target, png_bytes())
    with pytest.raises(ValueError, match="изображением"):
        atomic_bytes(target, b"<html>login</html>")
    assert valid_file(target)
    assert not (tmp_path / "page.png.part").exists()


def test_truncated_image_is_not_reused(tmp_path):
    path = tmp_path / "page.png"
    path.write_bytes(png_bytes()[:15])
    assert not valid_file(path)
    atomic_bytes(path, png_bytes())
    assert valid_file(path)


def test_pdf_is_checked_before_reuse(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7 some truncated data")
    assert not valid_file(path, "pdf")
    atomic_bytes(path, pdf_bytes(), "pdf")
    assert valid_file(path, "pdf")


def test_book_folders_do_not_escape_selected_directory(tmp_path):
    assert safe_folder_component("../../secrets") == "_.._secrets"
    assert folder_id("PRLIB", "https://www.prlib.ru/item/363162") == "PRLIB_363162"
    assert folder_id("RGO", "https://elib.rgo.ru/handle/123456789/777") == "RGO_777"
    assert folder_id("PDF_READER", "http://example.com/read/88/pdf") == "PDFReader_88"


def test_resume_journal_is_informational_and_recovers(tmp_path):
    journal = ResumeJournal(tmp_path, "RGO")
    journal.total, journal.done, journal.reused = 7, 3, 2
    journal.save(status="paused", message="Interrupted")
    state = ResumeJournal.read(tmp_path)
    assert (state["source"], state["completed"], state["total"], state["status"]) == (
        "RGO", 3, 7, "paused")
    (tmp_path / ".download_state.json").write_text("broken")
    assert ResumeJournal.read(tmp_path) is None


@pytest.mark.parametrize("cls,section,extension", [
    (KAZNEBDownloader, "KAZNEB", ".png"),
    (SHPLDownloader, "SHPL", ".jpeg"),
])
def test_page_downloaders_skip_valid_pages_after_restart(tmp_path, cls, section, extension):
    downloader = cls(cfg(section, tmp_path))
    downloader.queue = queue.Queue()
    downloader.make_pause = Mock()
    downloader.create_folders("123")
    downloader.extract_page_ids = Mock(return_value=["a", "b", "c"])
    image = jpg_bytes() if extension == ".jpeg" else png_bytes()
    first = os.path.join(downloader.folder, f"{0:05d}{extension}")
    atomic_bytes(first, image)
    downloaded = []

    def fake_download(page, number):
        downloaded.append(number)
        atomic_bytes(os.path.join(downloader.folder, f"{number:05d}{extension}"), image)

    downloader.download_page = fake_download
    downloader.process_book("123")
    assert sorted(downloaded) == [1, 2]
    assert all(valid_file(os.path.join(downloader.folder, f"{i:05d}{extension}")) for i in range(3))
    assert ResumeJournal.read(downloader.folder)["reused"] == 1
    downloader.process_book("123")
    assert sorted(downloaded) == [1, 2]  # no new network work


def test_rgo_resume_skips_previous_pages(tmp_path):
    downloader = RGODownloader(cfg("RGO", tmp_path))
    downloader.queue = queue.Queue()
    downloader.make_pause = Mock()
    downloader._resolve_view_url = Mock(return_value="https://elib.rgo.ru/safe-view/123456789/777/1/Qg==")
    downloader.download_html = Mock(return_value=BeautifulSoup('<div data-total-pages="3"></div>', "html.parser"))
    downloader.create_folders("777")
    atomic_bytes(os.path.join(downloader.folder, "00001.png"), png_bytes())
    requested = []

    def download_page(_url, number, folder):
        requested.append(number)
        atomic_bytes(os.path.join(folder, f"{number:05d}.png"), png_bytes())

    downloader.download_page = download_page
    downloader.process_book("https://elib.rgo.ru/handle/123456789/777")
    assert requested == [2, 3]
    downloader.process_book("https://elib.rgo.ru/handle/123456789/777")
    assert requested == [2, 3]
    assert ResumeJournal.read(downloader.folder)["completed"] == 3


def test_prlib_resume_from_existing_full_page_and_cached_tile(tmp_path):
    downloader = PRlibDownloader(cfg("PRLIB", tmp_path))
    downloader.create_folders("363162")
    downloader.queue = queue.Queue()
    downloader.make_pause = Mock()
    downloader.download_html = Mock(return_value=BeautifulSoup("<div></div>", "html.parser"))
    downloader._load_metadata = Mock(return_value=({"pgs": [
        {"f": "page1.tif", "d": [{"w": 8, "h": 6}]},
        {"f": "page2.tif", "d": [{"w": 8, "h": 6}]},
    ]}, "COLL", "GROUP"))
    atomic_bytes(os.path.join(downloader.folder, "page1.jpg"), jpg_bytes())
    tile_folder = os.path.join(downloader.folder, "page2")
    os.makedirs(tile_folder)
    atomic_bytes(os.path.join(tile_folder, "00000.jpg"), jpg_bytes())
    with patch.object(downloader, "http_get", side_effect=AssertionError("Cached tile must not be requested")) as http:
        downloader.process_book("https://www.prlib.ru/item/363162")
    assert valid_file(os.path.join(downloader.folder, "page2.jpg"))
    http.assert_not_called()
    assert ResumeJournal.read(downloader.folder)["reused"] == 1


class FakeResponse:
    def __init__(self, status, content, headers=None, fail_after_first=False):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.fail = fail_after_first

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, chunk_size=131072):
        if self.fail:
            yield self.content[:80]
            raise ConnectionError("Simulated network disconnect")
        yield self.content


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def test_streaming_pdf_resumes_by_range_with_etag(tmp_path):
    pdf = pdf_bytes()
    target = tmp_path / "doc.pdf"
    client = FakeClient([
        FakeResponse(200, pdf, {"ETag": '"v1"'}, fail_after_first=True),
        FakeResponse(206, pdf[80:], {"Content-Range": f"bytes 80-{len(pdf) - 1}/{len(pdf)}"}),
    ])
    with pytest.raises(ConnectionError, match="disconnect"):
        stream_pdf("https://example.test/doc.pdf", target, session=client)
    assert (tmp_path / "doc.pdf.part").stat().st_size == 80
    assert not target.exists()
    assert stream_pdf("https://example.test/doc.pdf", target, session=client) is False
    assert client.calls[1][1]["headers"]["Range"] == "bytes=80-"
    assert client.calls[1][1]["headers"]["If-Range"] == '"v1"'
    assert target.read_bytes() == pdf
    assert not (tmp_path / "doc.pdf.part.json").exists()


def test_streaming_pdf_restarts_if_server_ignores_range(tmp_path):
    pdf = pdf_bytes()
    target = tmp_path / "doc.pdf"
    (tmp_path / "doc.pdf.part").write_bytes(pdf[:90])
    (tmp_path / "doc.pdf.part.json").write_text(json.dumps({"validator": '"v1"'}))
    client = FakeClient([FakeResponse(200, pdf, {"ETag": '"v2"'})])
    assert stream_pdf("https://example.test/doc.pdf", target, session=client) is False
    assert target.read_bytes() == pdf  # not old 90 bytes + new full PDF


def test_cancellation_preserves_completed_pages(tmp_path):
    class Demo(LibraryDownloader):
        current_section = "DEMO"

    d = Demo()
    d.root_folder = str(tmp_path)
    d.queue = queue.Queue()
    d.cancel_event = threading.Event()
    d.create_folders("100")
    d.save_downloaded_bytes(os.path.join(d.folder, "00001.png"), png_bytes())
    d.report_progress(1, 3)
    d.cancel_event.set()
    with pytest.raises(DownloadCancelled):
        d.check_cancel()
    d.finalize_download(DownloadCancelled())
    assert valid_file(os.path.join(d.folder, "00001.png"))
    assert ResumeJournal.read(d.folder)["status"] == "paused"


def test_pgpb_part_pdf_fragments_survive_failed_run(tmp_path):
    d = PGPBDownloader(cfg("PGPB", tmp_path))
    d.PARALLEL_FILES = 1  # This test controls the exact interruption order.
    d.create_folders("4375")
    d.queue = queue.Queue()
    d.make_pause = Mock()
    d.extract_page_urls = Mock(return_value=[("1", "/first.pdf"), ("2", "/second.pdf")])
    saves = []

    def intermittent(url, filename):
        saves.append(url)
        if url.endswith("second.pdf") and len(saves) == 2:
            raise ConnectionError("network disconnected")
        atomic_bytes(filename, pdf_bytes(), "pdf")

    d.save_pdf = intermittent
    with pytest.raises(ConnectionError):
        d.process_book("4375")
    assert valid_file(os.path.join(d.folder, "_TEMP", "00001.pdf"), "pdf")
    d.process_book("4375")
    assert saves == ["https://pgpb.ru/first.pdf", "https://pgpb.ru/second.pdf",
                     "https://pgpb.ru/second.pdf"]
    assert valid_file(os.path.join(d.folder, "4375.pdf"), "pdf")
    assert not os.path.isdir(os.path.join(d.folder, "_TEMP"))
