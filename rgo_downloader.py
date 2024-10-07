#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import base64
import logging
import re
import shutil
import os

from abstract_lib_downloader import LibraryDownloader


class RGODownloader(LibraryDownloader):
    MAX_POSSIBLE_PAGE_NUM = 9999
    PAUSE_SEC = 5
    BOOK_FOLDER = "rgo"
    DEFAULT_URL_PART = "safe-view/123456789/"
    current_section = "RGO"
    queue = None
    page_from = 1


    def __init__(self, config):
        self.init_non_authorized_access(config)

    def download_book(self, book_id, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            self.page_from = page_from
            self.process_book(book_id)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def process_book(self, book_url):
        i = 1
        main_page = self.download_html(book_url)
        total_page_num = int(main_page.select("div.d-md-flex")[2].select("span")[-1].text)
        book_name = book_url.split(self.DEFAULT_URL_PART)[-1].split("/")[0]
        self.create_folders(book_name)
        while True and i < self.MAX_POSSIBLE_PAGE_NUM:
            logging.info(f"Downloading page {i}")
            self.make_pause()
            result = self.download_page(book_url, i, self.folder)
            if not result:
                return
            self.queue.put_nowait(round(i / total_page_num, 2))
            i += 1

    def download_page(self, book_url, page_num, output_folder):
        url = self.construct_url_page(book_url, page_num)
        result = self.get_page_content(url)
        content = result.content
        text = result.text
        if text.startswith("Error"):
            return False
        with open(os.path.join(output_folder, str(page_num).zfill(5) + ".png"), "wb") as fout:
            fout.write(content)
        return True

    @staticmethod
    def create_zip_archive(output_folder, output_archive_name):
        shutil.make_archive(
            base_name=output_archive_name,
            format="zip",
            root_dir=output_folder)

    @staticmethod
    def construct_url_page(book_url, page_num):
        url_parts = book_url.split("/")
        url_parts_main = url_parts[0:-1]
        filename = re.sub("_", "/", url_parts[-1].split("#")[0])
        filename_decoded = base64.b64decode(filename).decode("utf-8")
        filename_decoded_page = filename_decoded + "/" + str(page_num - 1)
        filename_decoded_page_base64 = base64.b64encode(filename_decoded_page.encode("utf-8")).decode("utf-8")
        filename_decoded_suffix = re.sub("[\\/]", "_", filename_decoded_page_base64)
        request_url = "/".join(url_parts_main) + "/" + filename_decoded_suffix
        return request_url
