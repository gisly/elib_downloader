#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import json
import logging
import os
import html
import re

from abstract_lib_downloader import LibraryDownloader


class KAZNEBDownloader(LibraryDownloader):
    BOOK_URL = "https://kazneb.kz/ru/bookView/view?brId=%s&simple=true"
    PAGE_URL = "https://kazneb.kz/FileStore"
    current_section = "KAZNEB"
    queue = None
    page_from = 1
    PAUSE_SEC = 1

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
        total_page_num = len(pages)
        for i, page in enumerate(pages):
            self.queue.put_nowait(round(i / total_page_num, 2))
            self.download_page(page, i)
            self.make_pause()

    def extract_page_ids(self, book_id):
        url = self.BOOK_URL % book_id
        main_page = self.download_html(url)

        page_data = main_page.select("#block-kazneb-content")[0].select("script")[0].contents[0].split("= []")[-1].strip().split(";\n")
        return [html.unescape(re.sub("[';()\"]", "", page.split('pages.push("/FileStore')[-1])) for page in page_data if page]

    def download_page(self, page_id, page_num):
        page_filename = str(page_num).zfill(5)
        url = self.PAGE_URL + page_id
        output_filename = os.path.join(self.folder, page_filename) + ".png"
        self.save_image(url, output_filename)
        logging.info(f"Processed page {page_num}")
