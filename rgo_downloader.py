"""Скачивание страниц, которые библиотека РГО предоставляет в просмотрщике.

Не пытается обходить ограничения доступа. При отказе сервера возвращает
понятную диагностику вместо сохранения HTML-страниц под видом PNG.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import shutil
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests

from abstract_lib_downloader import LibraryDownloader


class RGODownloader(LibraryDownloader):
    MAX_POSSIBLE_PAGE_NUM = 9999
    PAUSE_SEC = 5
    PARALLEL_FILES = 2
    HTTP_TIMEOUT_SEC = 30
    current_section = "RGO"

    def __init__(self, config=None):
        self.init_non_authorized_access(config)
        self.folder = self.root_folder
        # One session for the book card, viewer, and images, so ordinary
        # session cookies issued by the server are preserved.
        self.session = self.http_session()
        self.queue = None

    def download_book(self, book_url, queue, page_from=1):
        """Return (error message or None, destination directory)."""
        self.queue = queue
        try:
            if not isinstance(page_from, int) or page_from < 1:
                raise ValueError("Номер начальной страницы должен быть положительным")
            self.process_book(book_url.strip(), page_from=page_from)
            return None, os.path.abspath(self.folder)
        except Exception as exc:
            logging.exception("RGO download failed")
            return str(exc), os.path.abspath(self.folder)
        finally:
            self.session.close()

    def get_page_content(self, url, additional_headers=None):
        """Issue bounded requests with useful diagnostics and no proxy tricks."""
        for attempt in range(self.RETRY_NUM):
            try:
                response = self.http_get(
                    url,
                    headers=additional_headers,
                    timeout=self.HTTP_TIMEOUT_SEC,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                if attempt + 1 < self.RETRY_NUM:
                    self.retry_wait(attempt)
                    continue
                raise ValueError(
                    "РГО: не удалось подключиться к серверу. "
                    "Проверьте доступность сайта в браузере с того же компьютера. "
                    f"Техническая причина: {type(exc).__name__}"
                ) from exc

            if response.status_code == 200:
                return response
            if response.status_code == 403:
                raise ValueError(
                    "РГО вернул HTTP 403 (доступ запрещён). Возможны ограничения "
                    "по IP/региону, авторизации или настройкам сайта. Проверьте "
                    "эту же книгу в браузере из той же сети. Программа не обходит "
                    "ограничения доступа."
                )
            if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < self.RETRY_NUM:
                logging.warning("RGO: HTTP %s, attempt %s/%s", response.status_code, attempt + 1, self.RETRY_NUM)
                self.retry_wait(attempt, response)
                continue
            raise ValueError(
                f"РГО вернул HTTP {response.status_code}. "
                "Проверьте, открывается ли книга в браузере, и повторите попытку позднее."
            )
        raise RuntimeError("Недостижимая ветка повторных запросов РГО")

    @staticmethod
    def _validate_url(url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "elib.rgo.ru" or parsed.username or parsed.password:
            raise ValueError("Ожидается HTTPS-ссылка с сайта elib.rgo.ru")
        if parsed.port not in (None, 443):
            raise ValueError("Ссылка РГО содержит неожиданный порт")
        return parsed

    def _resolve_view_url(self, url):
        parsed = self._validate_url(url)
        if re.fullmatch(r"/safe-view/123456789/\d+/\d+/[^/]+/?", parsed.path):
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

        if re.fullmatch(r"/handle/123456789/\d+/?", parsed.path):
            card = self.download_html(url)
            link = card.select_one('a[href*="/safe-view/"]')
            if link is None or not link.get("href"):
                raise ValueError(
                    "РГО: в карточке нет ссылки на просмотрщик. "
                    "Возможно, у книги нет открытого просмотра или изменилась разметка."
                )
            return self._resolve_view_url(urljoin(url, link["href"]))
        raise ValueError("Укажите ссылку РГО вида /handle/123456789/ID или /safe-view/...")

    @classmethod
    def _total_pages(cls, page):
        """Read the page count without silently guessing if markup changed."""
        for selector, attribute in (
            ("[data-total-pages]", "data-total-pages"),
            ('input[type="number"][max]', "max"),
            ('input[type="range"][max]', "max"),
        ):
            for element in page.select(selector):
                raw = str(element.get(attribute, "")).strip()
                if raw.isdecimal() and 0 < int(raw) <= cls.MAX_POSSIBLE_PAGE_NUM:
                    return int(raw)

        # Compatibility with the previous RGO viewer markup.
        blocks = page.select("div.d-md-flex")
        if len(blocks) > 2:
            for span in reversed(blocks[2].select("span")):
                raw = span.get_text(strip=True)
                if raw.isdecimal() and 0 < int(raw) <= cls.MAX_POSSIBLE_PAGE_NUM:
                    return int(raw)
        raise ValueError(
            "РГО: не удалось определить количество страниц. "
            "Возможно, изменилась разметка просмотрщика или доступ ограничен."
        )

    def process_book(self, book_url, page_from=1):
        view_url = self._resolve_view_url(book_url)
        page_html = self.download_html(view_url)
        total_pages = self._total_pages(page_html)
        book_id = urlsplit(view_url).path.strip("/").split("/")[2]
        self.create_folders(book_id)

        if page_from > total_pages:
            raise ValueError(f"Начальная страница {page_from} больше числа страниц ({total_pages})")

        completed = page_from - 1

        def fetch(number):
            cached = any(self.file_ready(os.path.join(self.folder, f"{number:05d}{extension}"), "signature")
                         for extension in (".png", ".jpg", ".webp", ".gif"))
            if not cached:
                logging.info("RGO: page %s/%s", number, total_pages)
                self.download_page(view_url, number, self.folder)
            return cached

        def on_done(number, cached):
            nonlocal completed
            completed += 1
            self.report_progress(completed, total_pages, reused=cached)

        self.parallel(range(page_from, total_pages + 1), fetch, on_done=on_done)

    @staticmethod
    def _image_extension(content):
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if content.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return ".webp"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        return None

    def download_page(self, book_url, page_num, output_folder):
        for extension in (".png", ".jpg", ".webp", ".gif"):
            if self.file_ready(os.path.join(output_folder, f"{page_num:05d}{extension}"), "signature"):
                return True
        page_url = self.construct_url_page(book_url, page_num)
        response = self.get_page_content(page_url)
        extension = self._image_extension(response.content)
        if extension is None:
            content_type = response.headers.get("Content-Type", "не указан")
            raise ValueError(
                f"РГО: страница {page_num} не является изображением "
                f"(Content-Type: {content_type}). Возможно, сервер вернул "
                "HTML с ошибкой, изменил формат ответа или ограничил доступ."
            )
        filename = os.path.join(output_folder, f"{page_num:05d}{extension}")
        self.save_downloaded_bytes(filename, response.content, "signature")
        return True

    @classmethod
    def construct_url_page(cls, book_url, page_num):
        if not isinstance(page_num, int) or page_num < 1:
            raise ValueError("Номер страницы должен быть положительным")
        parsed = cls._validate_url(book_url)
        segments = parsed.path.strip("/").split("/")
        if len(segments) != 5 or segments[0] != "safe-view" or segments[1] != "123456789":
            raise ValueError("Неверный формат ссылки просмотрщика РГО")
        token = unquote(segments[-1])
        padded_token = token.replace("_", "/") + "=" * (-len(token) % 4)
        try:
            filename = base64.b64decode(padded_token, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("Ссылка РГО содержит некорректный идентификатор файла") from exc
        page_token = base64.b64encode(f"{filename}/{page_num - 1}".encode("utf-8")).decode("ascii")
        page_token = page_token.replace("/", "_")
        path = "/" + "/".join([*segments[:-1], page_token])
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    @staticmethod
    def create_zip_archive(output_folder, output_archive_name):
        return shutil.make_archive(output_archive_name, "zip", root_dir=output_folder)
