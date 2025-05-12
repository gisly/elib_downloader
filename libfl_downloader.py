from selenium import webdriver
import json
import logging
import os
import re

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from tqdm import tqdm

from abstract_lib_downloader import LibraryDownloader


class LIBFLDownloader(LibraryDownloader):
    URL_LOGIN = "https://lk.libfl.ru"
    URL_BOOK = "https://catalog.libfl.ru/Bookreader/Viewer?%s&view_mode=HQ#page/%s/mode/1up"

    URL_CDN = "https://cdn.libfl.ru/books/"
    HEADERS = {
        "referer": "https://catalog.libfl.ru/"
    }
    current_section = "LIBFL"
    queue = None
    page_from = 1

    def __init__(self, config):
        self.init_authorized_access(config)

        options = webdriver.ChromeOptions()
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-gpu")
        options.add_argument("--headless")
        options.add_argument("--window-position=-10000,-10000")
        self.driver = webdriver.Chrome(options=options)
        logging.info("Initialized driver")

    def __del__(self):
        if self.driver:
            try:
                self.driver.quit()
                logging.info("Closed driver")
            except Exception as e:
                logging.error(f"Exception closing driver {e}")

    def download_book(self, book_id, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            self.create_folders(book_id)
            self.page_from = page_from
            self.make_pause()
            self.open_site()
            self.download_pages(book_id)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def open_site(self):
        self.driver.get(self.URL_LOGIN)
        element_username = self.driver.find_element(by=By.NAME, value="login")
        element_username.clear()
        element_username.send_keys(self.login)
        element_password = self.driver.find_element(by=By.NAME, value="password")
        element_password.clear()
        element_password.send_keys(self.password)
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        element_button_ok = wait.until(
            expected_conditions.element_to_be_clickable(
                (By.NAME, "submit")))

        element_button_ok.click()

    def download_pages(self, book_id):
        cookies = self.driver.get_cookies()
        cookies_str = ";".join([c["name"] + "=" + c["value"] for c in cookies])

        book_url = self.URL_BOOK % (book_id, self.page_from)
        self.make_pause()
        match = None
        for i in range(0, 3):
            main_page = self.download_html(book_url, additional_headers={"Cookie": cookies_str, })
            self.make_pause()
            match = re.search('var exemplar = (\{.+?});',
                              main_page.text, re.S)
            if match:
                break

        if not match:
            raise Exception(f"Could not start downloading")

        book_data = match.group(1).strip()
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