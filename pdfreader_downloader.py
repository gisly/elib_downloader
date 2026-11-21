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
    ATTEMPTS_MAX = 3600  # One-hour ceiling with 1-second polling.
    DOWNLOAD_POLL_SEC = 1.0
    driver = None

    def __init__(self, config, book_id_value):
        book_server_url = book_id_value.split("://")[-1].split("/")[0]
        self.init_non_authorized_access(config)
        # Registry key is PDF_READER; historical output directory uses PDFReader.
        # Respect the GUI-selected folder without changing existing book paths.
        if config is not None and config.has_option("PDF_READER", self.CONFIG_FOLDER):
            self.root_folder = config.get("PDF_READER", self.CONFIG_FOLDER)
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

        self._options = options
        self.driver = None

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
            completed = [name for name in os.listdir(self.folder)
                         if name.lower().endswith(".pdf") and
                         self.file_ready(os.path.join(self.folder, name), "pdf")]
            if completed:
                self.report_progress(1, 1, reused=True, phase="PDF уже сохранён")
            else:
                self.open_site(book_url)
                resulting_path = self.download_pages()
                self.move_book(resulting_path)
                self.report_progress(1, 1, phase="PDF сохранён")
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def open_site(self, book_url):
        if self.driver is None:
            self.driver = webdriver.Chrome(options=self._options)
            logging.info("Initialized driver")
        self.driver.get(book_url)
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        wait.until(
            expected_conditions.element_to_be_clickable(
                (By.CSS_SELECTOR, "div[class='textLayer']")))
        logging.info("Site loaded")

    def download_pages(self):
        """
        Download the pdf using the built-in pdfreader.js function. We have to monitor the downloads section of the browser
        @return:
        """
        resulting_path = None
        logging.info("Start downloading book to %s", self.section_folder)
        self.check_cancel()
        self.driver.execute_script("PDFViewerApplication.downloadOrSave();")
        i = 0
        while i < self.ATTEMPTS_MAX:
            self.check_cancel()
            if getattr(self, "cancel_event", None) is not None:
                if self.cancel_event.wait(self.DOWNLOAD_POLL_SEC):
                    self.check_cancel()
            else:
                import time
                time.sleep(self.DOWNLOAD_POLL_SEC)
            current_downloads = self.get_current_downloads()
            if not current_downloads:
                break
            current_download_properties = current_downloads[0].split("$$$")
            current_state = int(current_download_properties[0])
            current_percentage = int(current_download_properties[1])
            if self.queue is not None:
                if getattr(self, "detail_events", False):
                    self.queue.put_nowait({"type": "progress", "completed": current_percentage,
                                          "total": 100, "phase": "Загрузка PDF браузером", "reused": 0})
                else:
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
        destination = os.path.join(self.folder, book_name)
        if not self.file_ready(old_path, "pdf"):
            raise RuntimeError("Браузер не завершил сохранение PDF; попробуйте ещё раз")
        os.replace(old_path, destination)
