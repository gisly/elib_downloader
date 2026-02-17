#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import os
import json
import shutil

import requests
import logging
from bs4 import BeautifulSoup
import math
from PIL import Image
import numpy as np

from abstract_lib_downloader import LibraryDownloader


class PRlibDownloader(LibraryDownloader):
    METADATA_URL = "https://content.prlib.ru/metadata/public/{book_name}/{book_second_name}/{book_name}.json"
    TILE_URL = (
        "https://content.prlib.ru/fcgi-bin/iipsrv.fcgi?FIF=/var/data/scans/public/{book_name}/{book_second_name}/{page_name}&JTL={"
        "zoom},{tile_num}&CVT=JPG")
    ZOOM = 4
    MAX_TILE_NUM = 999
    CURRENT_TILE_NUM = 6
    BOOK_FOLDER = "prlib"
    PAUSE_SEC = 1

    BOOK_URL = "https://www.prlib.ru/item/"
    current_section = "PRLIB"
    queue = None
    page_from = 1
    RETRY_NUM = 3

    def __init__(self, config):
        self.init_non_authorized_access(config)

    def download_book(self, book_id, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            self.page_from = page_from
            self.create_folders(book_id)
            book_url = self.BOOK_URL + book_id
            self.process_book(book_url)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def process_book(self, book_url):
        main_page = self.download_html(book_url)
        book_name = main_page.find("meta", property="og:image").attrs['content'].split('book_preview/')[-1].split('/')[
            0].upper()
        book_second_name = main_page.select_one("div.diva-viewer").attrs["data-filegroup"]
        book_metadata = self.get_book_metadata(book_url, main_page, book_name, book_second_name)
        pages = book_metadata["pgs"]
        self.MAX_PAGE_WIDTH = book_metadata["pgs"][0]["d"][self.ZOOM]["w"]
        # we count every page twice because they are first downloaded and then concatenated
        total_page_num = len(pages) * 2
        for page_num, page in enumerate(pages):
            if (page_num + 1) >= self.page_from:
                self.queue.put_nowait(round(page_num / total_page_num, 2))
                logging.info(f"Downloading page {page_num}")
                self.make_pause()
                self.download_page(book_name, book_second_name, page)
            else:
                logging.info(f"Skipping page {page_num}")
        self.concatenate_tiles(self.folder, total_page_num)

    def get_book_metadata(self, book_url, main_page, book_name, book_second_name):
        metadata_json = None
        try:
            metadata_json = json.loads(self.get_page_content(self.METADATA_URL.format(book_name=book_name,
                                                                             book_second_name=book_second_name)).text)
        except Exception as e:
            logging.info(f"Error loading metadata, trying another method: {str(e)}")
        if metadata_json is None:
            script_books = main_page.findAll("script")
            for script_book in script_books:
                if script_book.contents:
                    script_text = script_book.contents[0]
                    if script_text.startswith("jQuery.extend"):
                        book_link_parts = script_text.split("imageDir")[-1]. \
                            split(",")[0].split("/")
                        book_name = book_link_parts[-2].strip("\\")
                        book_second_name = book_link_parts[-1].strip("\\").strip('"')
                    break
            try:
                metadata_json = json.loads(self.get_page_content(self.METADATA_URL.format(book_name=book_name,
                                                                                          book_second_name=book_second_name)).text)
            except Exception as e:
                logging.error(f"Error loading metadata: {str(e)}")
        if metadata_json is None:
            raise Exception(f"Could not get metadata for {book_url}")
        return metadata_json

    def download_page(self, book_name, book_second_name, page):
        page_name = page['f']
        output_folder_page = os.path.join(self.folder, page_name.split('.')[0])
        if not os.path.exists(output_folder_page):
            os.makedirs(output_folder_page)

        tile_num = 0
        while tile_num < self.MAX_TILE_NUM:
            tile_url = self.TILE_URL.format(book_name=book_name, book_second_name=book_second_name, page_name=page_name,
                                            zoom=self.ZOOM, tile_num=tile_num)

            result = self.download_jpeg(output_folder_page, tile_num, tile_url)
            if not result:
                break
            tile_num += 1

    def download_jpeg(self, output_folder_page, tile_num, tile_url):
        i = 0
        while i < self.RETRY_NUM:
            try:
                result = self.get_page_content(tile_url)
            except Exception as e:
                logging.error(f"Error downloading {tile_url}: {str(e)}")
            status_code = result.status_code
            if status_code == 200:
                break
            if status_code == 404:
                return False
            self.make_pause()
            i += 1

        content = result.content
        output_tile_file = os.path.join(output_folder_page, str(tile_num).zfill(5) + ".jpg")
        with open(output_tile_file, 'wb') as fout:
            fout.write(content)
        return True

    def download_html(self, url):
        html = self.get_page_content(url).text
        return BeautifulSoup(html, features="html5lib")

    def concatenate_tiles(self, book_folder, total_page_num):
        page_num = 0
        for page_folder in os.listdir(book_folder):
            full_path = os.path.join(book_folder, page_folder)
            if os.path.isdir(full_path):
                self.queue.put_nowait(round((page_num * 2) / total_page_num, 2))
                page_num += 1
                logging.info(f"Concatenating page {page_num}")
                self.concatenate_page_tiles(os.path.join(book_folder, page_folder + ".jpg"), full_path)

    def concatenate_page_tiles(self, output_filename, tile_folder):
        list_of_images = [os.path.join(tile_folder, filename) for filename in os.listdir(tile_folder)]
        image_first_width = Image.open(list_of_images[0]).width
        num_cols = math.ceil(self.MAX_PAGE_WIDTH / image_first_width)
        num_rows = math.ceil(len(list_of_images) / num_cols)
        rows = []
        for row_num in range(0, num_rows):
            img_from = num_cols * row_num
            img_to = img_from + num_cols
            image_row = list_of_images[img_from:img_to]
            image_arr = [np.array(Image.open(x)) for x in image_row]
            res = image_arr[0]
            for i in range(1, len(image_arr)):
                res = np.concatenate([res, image_arr[i]],  axis=1)
            concatenated = Image.fromarray(res)

            rows.append(concatenated)
        full_image = Image.fromarray(
            np.concatenate(
                [row for row in rows],
                axis=0
            ))
        output_filename = os.path.join(output_filename)
        full_image.save(output_filename)
        shutil.rmtree(tile_folder)
