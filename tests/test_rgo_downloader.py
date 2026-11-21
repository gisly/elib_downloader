"""Offline regression tests; never contact elib.rgo.ru."""

import base64
import configparser
import queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from rgo_downloader import RGODownloader


VIEW = "https://elib.rgo.ru/safe-view/123456789/231378/1/" + base64.b64encode(
    b"book.pdf"
).decode("ascii")


def downloader(tmp_path):
    config = configparser.ConfigParser()
    config["RGO"] = {"folder": str(tmp_path)}
    result = RGODownloader(config)
    result.make_pause = Mock()
    return result


def response(status=200, content=b"\x89PNG\r\n\x1a\nDATA", content_type="image/png"):
    return SimpleNamespace(
        status_code=status, content=content, text=content.decode("utf-8", errors="replace"),
        headers={"Content-Type": content_type}
    )


def test_rgo_page_url_resolves_zero_based_index(tmp_path):
    page_url = RGODownloader.construct_url_page(VIEW, 3)
    token = page_url.rsplit("/", 1)[-1].replace("_", "/")
    assert base64.b64decode(token) == b"book.pdf/2"


def test_page_url_accepts_encoded_padding():
    page_url = RGODownloader.construct_url_page(VIEW.replace("=", "%3D"), 2)
    token = page_url.rsplit("/", 1)[-1].replace("_", "/")
    assert base64.b64decode(token) == b"book.pdf/1"


def test_rejects_unsafe_or_unexpected_urls(tmp_path):
    for url in ("http://elib.rgo.ru/safe-view/x", "https://evil.example/safe-view/x",
                "https://elib.rgo.ru.evil.example/safe-view/x",
                "https://elib.rgo.ru:444/safe-view/x"):
        with pytest.raises(ValueError):
            downloader(tmp_path)._resolve_view_url(url)


def test_card_can_resolve_viewer_link(tmp_path):
    d = downloader(tmp_path)
    d.download_html = Mock(return_value=BeautifulSoup(
        f'<a href="{VIEW}">Просмотреть</a>', "html.parser"))
    assert d._resolve_view_url("https://elib.rgo.ru/handle/123456789/231378") == VIEW


def test_count_uses_explicit_metadata_or_old_viewer_markup():
    page = BeautifulSoup('<div data-total-pages="12"></div>', "html.parser")
    assert RGODownloader._total_pages(page) == 12
    old = BeautifulSoup(
        '<div class="d-md-flex"></div>' * 2 + '<div class="d-md-flex"><span>1</span><span>24</span></div>',
        "html.parser")
    assert RGODownloader._total_pages(old) == 24
    with pytest.raises(ValueError, match="количество страниц"):
        RGODownloader._total_pages(BeautifulSoup("<p>Недоступно</p>", "html.parser"))


def test_403_reports_restriction_and_never_retries(tmp_path):
    d = downloader(tmp_path)
    d.session.get = Mock(return_value=response(status=403, content=b"Forbidden", content_type="text/html"))
    with pytest.raises(ValueError, match="403"):
        d.get_page_content(VIEW)
    assert d.session.get.call_count == 1


def test_429_retries_with_pause(tmp_path):
    d = downloader(tmp_path)
    d.session.get = Mock(side_effect=[response(status=429), response()])
    assert d.get_page_content(VIEW).status_code == 200
    assert d._rate_gate() is not None


def test_rejects_html_instead_of_saving_fake_png(tmp_path):
    d = downloader(tmp_path)
    d.get_page_content = Mock(return_value=response(content=b"<html>login</html>", content_type="text/html"))
    with pytest.raises(ValueError, match="не является изображением"):
        d.download_page(VIEW, 1, str(tmp_path))
    assert list(tmp_path.glob("*.png")) == []


def test_keeps_image_format(tmp_path):
    d = downloader(tmp_path)
    d.get_page_content = Mock(return_value=response(content=b"\xff\xd8\xffJPEG", content_type="image/jpeg"))
    d.download_page(VIEW, 1, str(tmp_path))
    assert (tmp_path / "00001.jpg").read_bytes() == b"\xff\xd8\xffJPEG"


def test_process_book_reports_progress_and_respects_page_from(tmp_path):
    d = downloader(tmp_path)
    d.download_html = Mock(return_value=BeautifulSoup('<div data-total-pages="3"></div>', "html.parser"))
    d.download_page = Mock(return_value=True)
    q = queue.Queue()
    d.queue = q
    d.process_book(VIEW, page_from=2)
    assert sorted(call.args[1] for call in d.download_page.call_args_list) == [2, 3]
    assert [q.get_nowait(), q.get_nowait()] == [round(2 / 3, 3), 1.0]
    assert (tmp_path / "RGO_231378").is_dir()


def test_error_response_is_propagated_to_gui(tmp_path):
    d = downloader(tmp_path)
    d.download_html = Mock(side_effect=ValueError("РГО вернул HTTP 403"))
    err, location = d.download_book(VIEW, queue.Queue())
    assert "403" in err
    assert location == str(tmp_path)
