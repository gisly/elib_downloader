"""Downloader for publicly accessible documents in the Presidential Library.

The site uses Diva.js metadata and an IIP tile server.  Availability of a
catalogue entry does not guarantee that its scans are publicly accessible.
"""

import json
import logging
import math
import os
import re
import shutil
from io import BytesIO
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from abstract_lib_downloader import LibraryDownloader


class PRlibDownloader(LibraryDownloader):
    METADATA_URL = (
        "https://content.prlib.ru/metadata/public/"
        "{book_name}/{book_second_name}/{book_name}.json"
    )
    TILE_URL = (
        "https://content.prlib.ru/fcgi-bin/iipsrv.fcgi?"
        "FIF=/var/data/scans/public/{book_name}/{book_second_name}/{page_name}"
        "&JTL={zoom},{tile_num}&CVT=JPG"
    )
    ZOOM = 4                 # Maximum requested level; not all documents have it.
    MAX_TILE_NUM = 999
    TILE_SIZE = 256  # IIP JTL tile size used by the public viewer.
    PAUSE_SEC = 1
    PARALLEL_FILES = 3
    RETRY_NUM = 3
    BOOK_URL = "https://www.prlib.ru/item/"
    current_section = "PRLIB"

    def __init__(self, config):
        self.init_non_authorized_access(config)
        self.queue = None
        self.page_from = 1
        self._page_widths = {}

    @staticmethod
    def normalize_book_id(book_id):
        """Accept either an item ID or an item URL, rejecting foreign hosts."""
        value = str(book_id).strip()
        parts = urlsplit(value)
        if parts.scheme or parts.netloc:
            if (parts.scheme not in ("http", "https") or
                    parts.hostname not in ("www.prlib.ru", "prlib.ru") or
                    parts.username is not None or parts.password is not None or
                    parts.port is not None):
                raise ValueError("Для PRLIB нужна ссылка на www.prlib.ru/item/ID")
            match = re.fullmatch(r"/item/(\d+)/?", parts.path)
            if not match:
                raise ValueError("Не удалось выделить ID из ссылки PRLIB (/item/ID)")
            return match.group(1)
        if not re.fullmatch(r"\d+", value):
            raise ValueError("Для PRLIB укажите числовой ID или ссылку /item/ID")
        return value

    def download_book(self, book_id, queue, page_from=1):
        self.queue = queue
        self.page_from = max(1, int(page_from))
        self.folder = os.path.abspath(self.root_folder)
        try:
            normalized_id = self.normalize_book_id(book_id)
            self.create_folders(normalized_id)
            self.process_book(self.BOOK_URL + normalized_id)
        except Exception as exc:
            logging.exception("Ошибка скачивания из Президентской библиотеки")
            return str(exc), os.path.abspath(self.folder)
        return None, os.path.abspath(self.folder)

    @staticmethod
    def _metadata_candidates(html, preview_id=None):
        """Extract possible (collection, filegroup) IDs without assuming a fixed DOM."""
        candidates = []
        preview = html.find("meta", attrs={"property": "og:image"})
        if preview and preview.get("content"):
            match = re.search(r"book_preview/([^/?#]+)", preview["content"], re.I)
            if match:
                preview_id = match.group(1).upper()

        viewer = html.select_one("div.diva-viewer[data-filegroup]")
        if viewer and preview_id and viewer.get("data-filegroup"):
            candidates.append((preview_id, viewer["data-filegroup"]))

        for script in html.find_all("script"):
            text = script.string or script.get_text() or ""
            # A legacy viewer exposes an imageDir in an inline script.
            for match in re.finditer(r"imageDir\s*[:=]\s*['\"]([^'\"]+)['\"]", text):
                components = [part for part in match.group(1).strip("/\\").split("/") if part]
                if len(components) >= 2:
                    candidates.append((components[-2], components[-1]))
        return list(dict.fromkeys(candidates)), preview_id

    def _load_metadata(self, book_url, main_page):
        """Try the catalogue and, when necessary, its dedicated reading tab."""
        candidates, preview_id = self._metadata_candidates(main_page)
        problems = []
        for book_name, filegroup in candidates:
            try:
                return self.get_book_metadata(book_url, main_page, book_name, filegroup), book_name, filegroup
            except (ValueError, RuntimeError, requests.RequestException) as exc:
                problems.append(str(exc))

        match = re.search(r"/item/(\d+)$", book_url)
        if match:
            reading_url = f"https://www.prlib.ru/node/{match.group(1)}/source"
            try:
                reading = self.download_html(reading_url)
                more, _ = self._metadata_candidates(reading, preview_id)
                for book_name, filegroup in more:
                    if (book_name, filegroup) in candidates:
                        continue
                    try:
                        return self.get_book_metadata(book_url, reading, book_name, filegroup), book_name, filegroup
                    except (ValueError, RuntimeError, requests.RequestException) as exc:
                        problems.append(str(exc))
            except Exception as exc:
                problems.append(f"Не удалось открыть вкладку «Чтение»: {exc}")

        details = f" Последняя ошибка: {problems[-1]}" if problems else ""
        raise ValueError(
            "Не удалось получить метаданные сканов PRLIB. "
            "Возможно, документ недоступен для публичного просмотра или сайт изменил просмотрщик."
            + details
        )

    def get_book_metadata(self, book_url, main_page, book_name, book_second_name):
        url = self.METADATA_URL.format(
            book_name=book_name, book_second_name=book_second_name
        )
        result = self.get_page_content(url)
        try:
            metadata = json.loads(result.text)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Метаданные PRLIB не являются JSON: {url}") from exc
        if not isinstance(metadata, dict) or not isinstance(metadata.get("pgs"), list) or not metadata["pgs"]:
            raise ValueError(f"Метаданные PRLIB не содержат страниц (pgs): {url}")
        return metadata

    @classmethod
    def _page_scale(cls, page):
        levels = page.get("d")
        if not isinstance(levels, list) or not levels:
            raise ValueError("У страницы отсутствуют уровни масштаба (d)")
        zoom = min(cls.ZOOM, len(levels) - 1)
        width = levels[zoom].get("w")
        if not isinstance(width, (int, float)) or width <= 0:
            raise ValueError(f"Некорректная ширина страницы PRLIB при масштабе {zoom}")
        return zoom, int(width)

    def process_book(self, book_url):
        html = self.download_html(book_url)
        metadata, book_name, filegroup = self._load_metadata(book_url, html)
        pages = metadata["pgs"]
        self._page_widths = {}
        total = len(pages)
        for page_index, page in enumerate(pages, start=1):
            self.check_cancel()
            if not isinstance(page.get("f"), str) or not page["f"]:
                raise ValueError(f"Отсутствует имя файла страницы {page_index} в метаданных")
            zoom, width = self._page_scale(page)
            basename = os.path.splitext(os.path.basename(page["f"]))[0]
            self._page_widths[basename] = width
            destination = os.path.join(self.folder, basename + ".jpg")
            reused = self.file_ready(destination)
            if page_index >= self.page_from and not reused:
                logging.info("Загружаем страницу %s/%s (масштаб %s)", page_index, total, zoom)
                self._current_page_index = page_index
                self._current_page_total = total
                self.download_page(book_name, filegroup, page)
                tile_folder = os.path.join(self.folder, basename)
                self.concatenate_page_tiles(destination, tile_folder, self._page_widths[basename])
            self.report_progress(page_index, total, reused=reused)

    def download_page(self, book_name, book_second_name, page):
        page_name = page["f"]
        basename = os.path.splitext(os.path.basename(page_name))[0]
        folder = os.path.join(self.folder, basename)
        os.makedirs(folder, exist_ok=True)
        zoom, width = self._page_scale(page)
        height = page["d"][zoom].get("h")

        # Most Diva metadata includes the exact dimensions for this level.
        # Avoid requesting an extra, nonexistent tile when we can derive the
        # expected grid.  Without height, retain the legacy 404 sentinel.
        expected_tiles = None
        if isinstance(height, (int, float)) and height > 0:
            expected_tiles = (math.ceil(width / self.TILE_SIZE) *
                              math.ceil(height / self.TILE_SIZE))
            if expected_tiles > self.MAX_TILE_NUM:
                raise RuntimeError(f"Слишком много фрагментов у страницы {page_name}: {expected_tiles}")
        limit = expected_tiles if expected_tiles is not None else self.MAX_TILE_NUM
        completed_tiles = 0

        def fetch(tile_num):
            url = self.TILE_URL.format(
                book_name=book_name, book_second_name=book_second_name,
                page_name=page_name, zoom=zoom, tile_num=tile_num)
            if not self.download_jpeg(folder, tile_num, url):
                if tile_num == 0:
                    raise RuntimeError(
                        "PRLIB не отдал ни одного фрагмента страницы "
                        f"{page_name} (HTTP 404). Возможно, сканы недоступны.")
                if expected_tiles is not None:
                    raise RuntimeError(
                        f"Не получен фрагмент {tile_num + 1}/{expected_tiles} "
                        f"страницы {page_name} (HTTP 404).")
                return False
            return True

        def completed(_tile_num, _result):
            nonlocal completed_tiles
            completed_tiles += 1
            if expected_tiles is not None:
                self.report_transfer(
                    completed=getattr(self, "_current_page_index", 1) - 1,
                    total=getattr(self, "_current_page_total", 1),
                    detail=completed_tiles, detail_total=expected_tiles,
                    phase=f"Фрагменты страницы {getattr(self, '_current_page_index', 1)}")

        # Only a known grid can safely be requested out of order.  When the
        # height is absent, we must stop at the first 404 as before.
        if expected_tiles is not None:
            self.parallel(range(limit), fetch, on_done=completed)
        else:
            for tile_num in range(limit):
                if not fetch(tile_num):
                    return
            raise RuntimeError(f"Слишком много фрагментов у страницы {page_name} (больше {self.MAX_TILE_NUM})")

    def download_jpeg(self, output_folder_page, tile_num, tile_url):
        """404 ends a tile sequence; cached valid tiles are reused."""
        path = os.path.join(output_folder_page, f"{tile_num:05d}.jpg")
        if self.file_ready(path):
            return True
        last_error = None
        for attempt in range(self.RETRY_NUM):
            self.check_cancel()
            response = None
            try:
                response = self.http_get(
                    tile_url, headers={"User-Agent": self.USER_AGENT}, timeout=30
                )
            except requests.RequestException as exc:
                last_error = f"Сетевая ошибка PRLIB: {exc}"
            else:
                if response.status_code == 404:
                    return False
                if response.status_code in (401, 403):
                    raise RuntimeError(
                        f"Сервер изображений PRLIB вернул HTTP {response.status_code}. "
                        "Возможно, для документа требуются права доступа."
                    )
                if response.status_code == 200:
                    try:
                        self.save_downloaded_bytes(path, response.content)
                    except (UnidentifiedImageError, OSError, ValueError) as exc:
                        raise RuntimeError(
                            f"PRLIB вместо изображения вернул некорректные данные: {tile_url}"
                        ) from exc
                    return True
                last_error = f"Сервер изображений PRLIB вернул HTTP {response.status_code}"
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt + 1 < self.RETRY_NUM:
                self.retry_wait(attempt, response)
        raise RuntimeError(f"{last_error} (фрагмент {tile_num}, {tile_url})")

    def get_page_content(self, url, additional_headers=None):
        """HTTP errors must be explicit; external requests must have a timeout."""
        headers = {"User-Agent": self.USER_AGENT}
        headers.update(additional_headers or {})
        problem = None
        for attempt in range(self.RETRY_NUM):
            response = None
            try:
                response = self.http_get(url, headers=headers, timeout=30)
            except requests.RequestException as exc:
                problem = f"Сетевая ошибка PRLIB при обращении к {url}: {exc}"
            else:
                if response.status_code == 200:
                    return response
                if response.status_code in (401, 403):
                    raise RuntimeError(
                        f"PRLIB вернул HTTP {response.status_code} для {url}. "
                        "Проверьте доступ в браузере и права на просмотр документа."
                    )
                problem = f"PRLIB вернул HTTP {response.status_code} для {url}"
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
            if attempt + 1 < self.RETRY_NUM:
                self.retry_wait(attempt, response)
        raise RuntimeError(problem or f"Не удалось открыть {url}")

    def download_html(self, url):
        return BeautifulSoup(self.get_page_content(url).text, features="html.parser")

    def concatenate_tiles(self, book_folder, total_page_num):
        folders = sorted(
            name for name in os.listdir(book_folder)
            if os.path.isdir(os.path.join(book_folder, name))
            and name in self._page_widths
        )
        for idx, name in enumerate(folders, start=1):
            folder = os.path.join(book_folder, name)
            logging.info("Собираем изображение страницы %s", name)
            self.concatenate_page_tiles(
                os.path.join(book_folder, name + ".jpg"), folder,
                self._page_widths[name]
            )
            self.queue.put_nowait(min(1.0, 0.5 + idx / total_page_num))

    def concatenate_page_tiles(self, output_filename, tile_folder, page_width=None):
        if self.file_ready(output_filename):
            if os.path.isdir(tile_folder):
                shutil.rmtree(tile_folder)
            return
        self.check_cancel()
        filenames = sorted(
            filename for filename in os.listdir(tile_folder)
            if filename.lower().endswith(".jpg")
        )
        if not filenames:
            raise RuntimeError(f"Не найдены фрагменты для сборки страницы: {tile_folder}")
        images = []
        try:
            for filename in filenames:
                with Image.open(os.path.join(tile_folder, filename)) as image:
                    images.append(image.convert("RGB"))
            width = page_width or self._page_widths.get(os.path.basename(tile_folder))
            if not width:
                raise ValueError("Неизвестна ширина страницы PRLIB")
            columns = math.ceil(width / images[0].width)
            rows = [images[i:i + columns] for i in range(0, len(images), columns)]
            output_width = max(sum(image.width for image in row) for row in rows)
            output_height = sum(max(image.height for image in row) for row in rows)
            stitched = Image.new("RGB", (output_width, output_height), "white")
            y = 0
            for row in rows:
                x = 0
                for image in row:
                    stitched.paste(image, (x, y))
                    x += image.width
                y += max(image.height for image in row)
            temp = output_filename + ".part"
            try:
                stitched.save(temp, format="JPEG")
                self.check_cancel()
                if not self.file_ready(temp):
                    raise RuntimeError(f"Некорректная собранная страница: {output_filename}")
                os.replace(temp, output_filename)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            stitched.close()
        finally:
            for image in images:
                image.close()
        shutil.rmtree(tile_folder)
