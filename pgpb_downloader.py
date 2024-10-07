#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import logging
import shutil

import requests
import os
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader, PdfWriter

from abstract_lib_downloader import LibraryDownloader


class PGPBDownloader(LibraryDownloader):
    PGPB_URL = 'https://pgpb.ru'
    PGPB_MAIN_SUFFIX = '/digitization/document'
    current_section = "PGPB"
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
        pages = self.extract_page_urls(book_id)
        temporary_pdf_dir = os.path.join(self.folder, "_TEMP")
        os.makedirs(temporary_pdf_dir, exist_ok=True)
        total_page_num = len(pages)
        for i, page in enumerate(pages):
            self.queue.put_nowait(round(i / total_page_num, 2))
            self.download_page(page, temporary_pdf_dir)
            self.make_pause()
        self.merge_pdf(book_id, temporary_pdf_dir)

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
        html = requests.get(url).text
        return BeautifulSoup(html, features="html5lib")

    def save_pdf(self, url, output_filename):
        pdf = requests.get(url).content
        with open(output_filename, 'wb') as fout:
            fout.write(pdf)

    def merge_pdf(self, book_id, temporary_pdf_dir):
        with PdfWriter() as writer:
            for item in os.listdir(temporary_pdf_dir):
                if item.endswith(".pdf"):
                    full_path = os.path.join(temporary_pdf_dir, item)
                    reader = PdfReader(full_path)
                    for page in reader.pages:
                        page.compress_content_streams()
                        writer.add_page(page)
        
        output_filename = os.path.join(self.folder, str(book_id) + '.pdf')
        with open(output_filename, "wb") as f:
            writer.write(f)
        shutil.rmtree(temporary_pdf_dir)
