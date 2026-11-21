#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import json
import logging
import os

from abstract_lib_downloader import LibraryDownloader


class SHPLDownloader(LibraryDownloader):
    SHPL_URL = "http://elib.shpl.ru/ru/nodes/"
    SHPL_PAGE_URL = "http://elib.shpl.ru/pages/"
    current_section = "SHPL"
    queue = None
    page_from = 1
    PAUSE_SEC = 1
    PARALLEL_FILES = 3

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
        pages = self.extract_page_ids(book_id)
        if not pages:
            raise RuntimeError("Не удалось найти страницы книги на сайте библиотеки")
        total_page_num = len(pages)
        ext = ".png" if self.current_section == "KAZNEB" else ".jpeg"
        completed = 0

        def fetch(item):
            i, page = item
            target = os.path.join(self.folder, f"{i:05d}{ext}")
            cached = self.file_ready(target)
            if not cached:
                self.download_page(page, i)
            return cached

        def on_done(item, cached):
            nonlocal completed
            completed += 1
            self.report_progress(completed, total_page_num, reused=cached)

        self.parallel(enumerate(pages), fetch, on_done=on_done)

    def extract_page_ids(self, book_id):
        url = self.SHPL_URL + str(book_id)
        main_page = self.download_html(url)

        page_data = json.loads(main_page.select("script")[2].contents[0].split("(")[-1].strip().strip(")"))
        return [page["id"] for page in page_data["pages"]]

    def download_page(self, page_id, page_num):
        page_filename = str(page_num).zfill(5)
        url = self.SHPL_PAGE_URL + str(page_id) + "/zooms/8"
        output_filename = os.path.join(self.folder, page_filename) + ".jpeg"
        self.save_image(url, output_filename)
        logging.info(f"Processed page {page_num}")