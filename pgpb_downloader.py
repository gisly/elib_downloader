#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import logging
import shutil

import requests
import os
from bs4 import BeautifulSoup
try:
    from PyPDF2 import PdfReader, PdfWriter
except ImportError:
    from pypdf import PdfReader, PdfWriter

from abstract_lib_downloader import LibraryDownloader


class PGPBDownloader(LibraryDownloader):
    PGPB_URL = 'https://pgpb.ru'
    PGPB_MAIN_SUFFIX = '/digitization/document'
    current_section = "PGPB"
    queue = None
    page_from = 1
    PAUSE_SEC = 1
    PARALLEL_FILES = 2

    def __init__(self, config):
        self.init_non_authorized_access(config)

    def download_book(self, book_id, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            self.page_from = page_from
            self.create_folders(book_id)
            self.process_book(book_id)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def process_book(self, book_id):
        output_pdf = os.path.join(self.folder, str(book_id) + ".pdf")
        if self.file_ready(output_pdf, "pdf"):
            self.report_progress(1, 1, reused=True, phase="Готово")
            return
        pages = self.extract_page_urls(book_id)
        if not pages:
            raise RuntimeError("Не удалось найти страницы книги на сайте библиотеки")
        temporary_pdf_dir = os.path.join(self.folder, "_TEMP")
        os.makedirs(temporary_pdf_dir, exist_ok=True)
        total_page_num = len(pages)
        completed = 0

        def fetch(page):
            page_filename = os.path.join(temporary_pdf_dir, str(page[0]).zfill(5) + ".pdf")
            reused = self.file_ready(page_filename, "pdf")
            if not reused:
                self.download_page(page, temporary_pdf_dir)
            return reused

        def on_done(page, reused):
            nonlocal completed
            completed += 1
            self.report_progress(completed, total_page_num + 1, reused=reused)

        self.parallel(pages, fetch, on_done=on_done)
        self.check_cancel()
        self.merge_pdf(book_id, temporary_pdf_dir, pages)
        self.report_progress(total_page_num + 1, total_page_num + 1, phase="PDF собран")

    def extract_page_urls(self, book_id):
        url = self.PGPB_URL + self.PGPB_MAIN_SUFFIX + "/" + str(book_id)
        main_page = self.download_html(url)
        page_tags = main_page.select(".digitization-view-left")
        return [(page_tag.get("data-id"), page_tag.get("data-url"))
                for page_tag in page_tags if page_tag.get("data-url") is not None]

    def download_page(self, page, temporary_pdf_dir):
        page_id = page[0]
        page_id_zero_padded = page_id.zfill(5)
        page_url = page[1]
        url = self.PGPB_URL + page_url
        output_filename = os.path.join(temporary_pdf_dir, page_id_zero_padded) + ".pdf"
        self.save_pdf(url, output_filename)
        logging.info(f"Processed page {page_id}")

    def download_html(self, url):
        return super().download_html(url)

    def save_pdf(self, url, output_filename):
        return self.save_pdf_stream(url, output_filename)

    def merge_pdf(self, book_id, temporary_pdf_dir, pages=None):
        output_filename = os.path.join(self.folder, str(book_id) + ".pdf")
        temp_filename = output_filename + ".part"
        try:
            with PdfWriter() as writer:
                files = ([str(page_id).zfill(5) + ".pdf" for page_id, _ in pages]
                         if pages is not None else sorted(
                             filename for filename in os.listdir(temporary_pdf_dir)
                             if filename.endswith(".pdf")))
                for item in files:
                    self.check_cancel()
                    full_path = os.path.join(temporary_pdf_dir, item)
                    reader = PdfReader(full_path)
                    for page in reader.pages:
                        page.compress_content_streams()
                        writer.add_page(page)
                with open(temp_filename, "wb") as f:
                    writer.write(f)
            if not self.file_ready(temp_filename, "pdf"):
                raise RuntimeError("Собранный PDF повреждён")
            self.check_cancel()
            os.replace(temp_filename, output_filename)
            shutil.rmtree(temporary_pdf_dir)
        finally:
            if os.path.exists(temp_filename):
                os.unlink(temp_filename)
