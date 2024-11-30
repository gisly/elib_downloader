#!/usr/bin/python
# -*- coding: utf-8 -*
__author__ = "gisly"

import logging
import os
import shutil

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait

from abstract_lib_downloader import LibraryDownloader


class PDFReaderDownloader(LibraryDownloader):
    current_section = "PDFReader"
    queue = None
    page_from = 1
    PAUSE_SEC = 20
    ATTEMPTS_MAX = 100
    driver = None

    def __init__(self, config, book_id_value):
        book_server_url = book_id_value.split("://")[-1].split("/")[0]
        self.init_non_authorized_access(config)
        self.create_common_section_folder()
        options = webdriver.ChromeOptions()
        options.add_experimental_option("prefs", {
            "profile.default_content_settings.popups": 0,
            "download.default_directory": os.path.abspath(self.section_folder),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,
            "download.extensions_to_open": "application/pdf"
        })
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-gpu")
        options.add_argument("--headless")
        options.add_argument("--window-position=-10000,-10000")
        options.add_argument("--safebrowsing-disable-download-protection")
        options.add_argument("--safebrowsing-disable-extension-blacklist")
        options.add_argument("--allow-running-insecure-content")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--unsafely-treat-insecure-origin-as-secure=" + "http://" + book_server_url)

        self.driver = webdriver.Chrome(options=options)
        logging.info("Initialized driver")

    def __del__(self):
        if self.driver:
            try:
                self.driver.quit()
                logging.info("Closed driver")
            except Exception as e:
                logging.error(f"Exception closing driver {e}")

    def download_book(self, book_url, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            book_name = book_url.strip("/").split("/")[-2]
            self.create_folders(book_name)
            self.page_from = page_from
            self.make_pause()
            self.open_site(book_url)
            resulting_path = self.download_pages()
            self.move_book(resulting_path)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def open_site(self, book_url):
        self.driver.get(book_url)
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        wait.until(
            expected_conditions.element_to_be_clickable(
                (By.CSS_SELECTOR, "div[class='textLayer']")))
        self.make_pause()
        logging.info("Site loaded")

    def download_pages(self):
        """
        Download the pdf using the built-in pdfreader.js function. We have to monitor the downloads section of the browser
        @return:
        """
        resulting_path = None
        logging.info("Start downloading book to %s", self.section_folder)
        self.driver.execute_script("PDFViewerApplication.downloadOrSave();")
        i = 0
        while i < self.ATTEMPTS_MAX:
            self.make_pause()
            current_downloads = self.get_current_downloads()
            if not current_downloads:
                break
            current_download_properties = current_downloads[0].split("$$$")
            current_state = int(current_download_properties[0])
            current_percentage = int(current_download_properties[1])
            self.queue.put_nowait(current_percentage * 0.01)
            current_path = current_download_properties[2]
            if current_state != 0 or current_percentage >= 100 or current_percentage < 0:
                resulting_path = current_path
                break
            i += 1
        if resulting_path is None:
            raise Exception("Could not download pages")
        return resulting_path

    def get_current_downloads(self):
        if not self.driver.current_url.startswith("chrome://downloads"):
            self.driver.get("chrome://downloads/")
        return self.driver.execute_script("""
            return document.querySelector('downloads-manager')
            .shadowRoot.querySelector('#downloadsList')
            .items
            .map(e => e.state + '$$$' + e.percent + '$$$' + e.filePath);
            """)

    def move_book(self, old_path):
        book_name = os.path.basename(old_path)
        shutil.move(old_path, os.path.join(self.folder, book_name))
