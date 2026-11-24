"""Regression tests for PRLIB; no external HTTP requests."""

import configparser
import io
import json
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from prlib_downloader import PRlibDownloader


def downloader(tmp_path):
    config = configparser.ConfigParser()
    config["PRLIB"] = {"folder": str(tmp_path)}
    d = PRlibDownloader(config)
    d.make_pause = Mock()
    d.PARALLEL_FILES = 1  # Preserve deterministic sentinel/metadata regression checks.
    d.queue = queue.Queue()
    return d


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (6, 4), (120, 30, 30)).save(out, "JPEG")
    return out.getvalue()


def response(status=200, data=None):
    return SimpleNamespace(status_code=status, content=jpeg() if data is None else data)


@pytest.mark.parametrize("value", ["363162", " https://www.prlib.ru/item/363162 ",
                                     "https://prlib.ru/item/363162/?x=abc"])
def test_book_id_and_url_are_accepted(value):
    assert PRlibDownloader.normalize_book_id(value) == "363162"


@pytest.mark.parametrize("value", ["https://www.prlib.ru/item/https://www.prlib.ru/item/363162",
                                     "https://example.com/item/363162", "../../../x", "abc", ""])
def test_invalid_urls_are_rejected(value):
    with pytest.raises(ValueError):
        PRlibDownloader.normalize_book_id(value)


def test_full_url_is_not_prepended_again(tmp_path):
    d = downloader(tmp_path)
    d.process_book = Mock()
    err, location = d.download_book("https://www.prlib.ru/item/363162", d.queue)
    assert err is None
    d.process_book.assert_called_once_with("https://www.prlib.ru/item/363162")
    assert location.endswith("PRLIB_363162")


def test_low_resolution_metadata_uses_available_zoom():
    assert PRlibDownloader._page_scale({"d": [{"w": 200}, {"w": 400}]}) == (1, 400)
    assert PRlibDownloader._page_scale({"d": [{"w": 200}] * 6}) == (4, 200)


def test_diva_ids_from_markup():
    html = BeautifulSoup('''<meta property="og:image" content="https://prlib.ru/book_preview/abcde/book.jpg">
                      <div class="diva-viewer" data-filegroup="123"></div>''', "html.parser")
    candidates, preview = PRlibDownloader._metadata_candidates(html)
    assert candidates == [("ABCDE", "123")]
    assert preview == "ABCDE"


def test_script_fallback_scans_beyond_first_script(tmp_path):
    d = downloader(tmp_path)
    html = BeautifulSoup('''<script>irrelevant()</script><script>
        jQuery.extend({imageDir: "/public/ABC/123"});</script>''', "html.parser")
    d.download_html = Mock(return_value=html)
    d.get_book_metadata = Mock(return_value={"pgs": [{"f": "p.tif", "d": [{"w": 200}]}]})
    meta, book_name, filegroup = d._load_metadata("https://www.prlib.ru/item/363162", html)
    assert (book_name, filegroup) == ("ABC", "123")
    assert meta["pgs"][0]["f"] == "p.tif"


def test_tile_404_is_normal_end_of_page(tmp_path):
    d = downloader(tmp_path)
    with patch.object(d, "http_get", side_effect=[response(), response(404)]) as get:
        page = {"f": "scan.tiff", "d": [{"w": 6}]}
        d.folder = str(tmp_path)
        d.download_page("ABC", "123", page)
    assert get.call_count == 2
    assert (tmp_path / "scan" / "00000.jpg").exists()
    assert not (tmp_path / "scan" / "00001.jpg").exists()


def test_404_on_first_tile_is_not_reported_as_success(tmp_path):
    d = downloader(tmp_path)
    d.folder = str(tmp_path)
    with patch.object(d, "http_get", return_value=response(404)):
        with pytest.raises(RuntimeError, match="ни одного фрагмента"):
            d.download_page("ABC", "123", {"f": "scan.tiff", "d": [{"w": 100}]})


def test_403_is_actionable_and_no_invalid_file(tmp_path):
    d = downloader(tmp_path)
    with patch.object(d, "http_get", return_value=response(403)):
        with pytest.raises(RuntimeError, match="HTTP 403"):
            d.download_jpeg(str(tmp_path), 0, "https://content.prlib.ru/tile")
    assert list(tmp_path.glob("*.jpg")) == []


def test_html_error_is_not_saved_as_image(tmp_path):
    d = downloader(tmp_path)
    with patch.object(d, "http_get", return_value=response(200, b"<html>Login</html>")):
        with pytest.raises(RuntimeError, match="некорректные данные"):
            d.download_jpeg(str(tmp_path), 0, "https://content.prlib.ru/tile")
    assert not list(tmp_path.glob("*.jpg"))


def test_network_failures_do_not_crash_with_unbound_result(tmp_path):
    d = downloader(tmp_path)
    with patch.object(d, "http_get", side_effect=__import__("requests").Timeout("timeout")):
        with pytest.raises(RuntimeError, match="Сетевая ошибка"):
            d.download_jpeg(str(tmp_path), 0, "https://content.prlib.ru/tile")


def test_stitching_uses_sorted_tile_filenames(tmp_path):
    d = downloader(tmp_path)
    folder = tmp_path / "scan"
    folder.mkdir()
    Image.new("RGB", (5, 3), "red").save(folder / "00001.jpg")
    Image.new("RGB", (5, 3), "blue").save(folder / "00000.jpg")
    result = tmp_path / "scan.jpg"
    d.concatenate_page_tiles(str(result), str(folder), 10)
    with Image.open(result) as image:
        left, right = image.getpixel((1, 1)), image.getpixel((6, 1))
    assert left[2] > left[0]  # blue tile must be left
    assert right[0] > right[2]
    assert not folder.exists()


def test_metadata_missing_pages_is_explained(tmp_path):
    d = downloader(tmp_path)
    d.get_page_content = Mock(return_value=SimpleNamespace(text=json.dumps({"pgs": []})))
    with pytest.raises(ValueError, match="не содержат страниц"):
        d.get_book_metadata("https://www.prlib.ru/item/363162", None, "ABC", "123")


def test_dimensions_avoid_requesting_nonexistent_extra_tile(tmp_path):
    d = downloader(tmp_path)
    d.folder = str(tmp_path)
    # 300x350 at 256px tiles -> 2 x 2 = four tiles.
    with patch.object(d, "download_jpeg", return_value=True) as get:
        d.download_page("ABC", "123", {"f": "scan.tiff", "d": [{"w": 300, "h": 350}]})
    assert get.call_count == 4
    assert all("JTL=0," in call.args[2] for call in get.call_args_list)


def test_missing_expected_tile_raises_instead_of_truncating(tmp_path):
    d = downloader(tmp_path)
    d.folder = str(tmp_path)
    with patch.object(d, "download_jpeg", side_effect=[True, False]):
        with pytest.raises(RuntimeError, match="2/4"):
            d.download_page("ABC", "123", {"f": "scan.tiff", "d": [{"w": 300, "h": 350}]})


def test_metadata_http_403_reports_access_restriction(tmp_path):
    d = downloader(tmp_path)
    with patch.object(d, "http_get", return_value=response(403)):
        with pytest.raises(RuntimeError, match="HTTP 403"):
            d.get_page_content("https://content.prlib.ru/metadata/x")


def test_metadata_requests_have_a_timeout(tmp_path):
    d = downloader(tmp_path)
    result = SimpleNamespace(status_code=200, text='{"pgs": []}')
    with patch.object(d, "http_get", return_value=result) as req:
        d.get_page_content("https://www.prlib.ru/item/363162")
    assert req.call_args.kwargs["timeout"] == 30


def test_end_to_end_mock_download_of_example_item(tmp_path):
    d = downloader(tmp_path)
    main_page = BeautifulSoup('''
        <meta property="og:image" content="https://www.prlib.ru/book_preview/ABC/thumbnail.jpg">
        <div class="diva-viewer" data-filegroup="123"></div>''', "html.parser")
    d.download_html = Mock(return_value=main_page)
    d.get_book_metadata = Mock(return_value={
        "pgs": [{"f": "first.tiff", "d": [{"w": 6, "h": 4}]}]
    })

    def write_tile(folder, number, url):
        with open(__import__("os").path.join(folder, f"{number:05d}.jpg"), "wb") as f:
            f.write(jpeg())
        return True

    d.download_jpeg = Mock(side_effect=write_tile)
    error, folder = d.download_book("https://www.prlib.ru/item/363162", d.queue)
    assert error is None
    assert (tmp_path / "PRLIB_363162" / "first.jpg").exists()
    assert d.download_jpeg.call_count == 1
    assert 1.0 in list(d.queue.queue)


def test_prlib_5938488_complete_cached_grid_disagrees_with_metadata(tmp_path):
    """Regression for PRLIB_363144 page 226: real image is 10x7, not 13x19."""
    d = downloader(tmp_path)
    d.folder = str(tmp_path)
    tile_folder = tmp_path / "5938488_doc1"
    tile_folder.mkdir()
    for index in range(70):
        col, row = index % 10, index // 10
        width = 46 if col == 9 else 256
        height = 88 if row == 6 else 256
        Image.new("RGB", (width, height), (10, 30, 80)).save(
            tile_folder / f"{index:05d}.jpg", "JPEG")
    page = {"f": "5938488_doc1.tiff", "d": [{"w": 3200, "h": 4700}]}
    with patch.object(d, "download_jpeg", side_effect=AssertionError("Already cached!")):
        actual_width = d.download_page("A", "B", page)
    assert actual_width == 2350
    assert d._cached_complete_grid(str(tile_folder)) == (2350, 1624)
    out = tmp_path / "5938488_doc1.jpg"
    d.concatenate_page_tiles(str(out), str(tile_folder), actual_width)
    with Image.open(out) as image:
        assert image.size == (2350, 1624)


def test_prlib_recovers_when_first_404_is_after_complete_image(tmp_path):
    d = downloader(tmp_path)
    d.TILE_SIZE = 4
    d.PARALLEL_FILES = 3
    d.folder = str(tmp_path)
    requested = []

    def fetch(folder, index, _url):
        requested.append(index)
        if index >= 6:
            return False
        width = 2 if index % 3 == 2 else 4
        height = 1 if index >= 3 else 4
        Image.new("RGB", (width, height), "blue").save(
            __import__('os').path.join(folder, f"{index:05d}.jpg"), "JPEG")
        return True

    d.download_jpeg = fetch
    # Metadata implies a 4x4 tile grid, while IIP contains a complete 3x2.
    actual_width = d.download_page("A", "B", {
        "f": "scan.tiff", "d": [{"w": 15, "h": 13}]
    })
    assert actual_width == 10
    assert sorted(set(range(6)) - set(requested)) == []
    out = tmp_path / "scan.jpg"
    d.concatenate_page_tiles(str(out), str(tmp_path / "scan"), actual_width)
    with Image.open(out) as image:
        assert image.size == (10, 5)


def test_prlib_does_not_accept_incomplete_grid_on_404(tmp_path):
    d = downloader(tmp_path)
    d.TILE_SIZE = 4
    d.folder = str(tmp_path)

    def fetch(folder, index, _url):
        if index == 6:
            return False
        # No shorter bottom row; this cannot be proven to be complete.
        width = 2 if index % 3 == 2 else 4
        Image.new("RGB", (width, 4), "green").save(
            __import__('os').path.join(folder, f"{index:05d}.jpg"), "JPEG")
        return True

    d.download_jpeg = fetch
    with pytest.raises(RuntimeError, match="HTTP 404"):
        d.download_page("A", "B", {"f": "scan.tiff", "d": [{"w": 15, "h": 13}]})
