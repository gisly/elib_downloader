import configparser
import json
import logging
import os
import re

import requests
from tqdm import tqdm

from abstract_lib_downloader import LibraryDownloader


class LIBFLDownloader(LibraryDownloader):
    URL_BOOK = "https://catalog.libfl.ru/Bookreader/Viewer?bookID=%s&view_mode=HQ#page/%s/mode/1up"
    URL_CDN = "https://cdn.libfl.ru/books/"
    HEADERS = {
        "referer": "https://catalog.libfl.ru/"
    }
    current_section = "LIBFL"
    queue = None
    page_from = 1

    def __init__(self, config):
        self.init_non_authorized_access(config)

    def download_book(self, book_id, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            self.create_folders(book_id)
            self.page_from = page_from
            self.make_pause()
            self.download_pages(book_id)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)


    def download_pages(self, book_id):
        book_url = self.URL_BOOK % (book_id, self.page_from)
        main_page = self.download_html(book_url)
        book_data = re.search('var exemplar = (\{.+?});',
                              main_page.text, re.S).group(1).strip()
        book_data_structured = json.loads(book_data)
        hq = book_data_structured["Path_HQ"]
        jpeg_files = book_data_structured["JPGFiles"]
        self.last_page = len(jpeg_files)
        n = self.last_page + 1

        for i in tqdm(range(self.page_from, self.last_page + 1)):
            self.queue.put_nowait(round(i / n, 2))
            logging.info(f"Downloading page {i} out of {self.last_page}")
            filename = str(i).zfill(4) + ".jpg"
            full_filename = os.path.join(self.folder, filename)

            self.make_pause()
            image_link = self.URL_CDN + hq + jpeg_files [i-1]
            response = requests.get(image_link, headers=self.HEADERS)
            if not response.ok:
                raise Exception(f"Exception when downloading page {i} out of {self.last_page}")
            with open(full_filename, 'wb') as fout:
                fout.write(response.content)
                logging.info(f"Downloaded page {i} out of {self.last_page}")
            if i >= self.last_page:
                break