from selenium import webdriver
import logging
import os
import re

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from abstract_lib_downloader import LibraryDownloader


class NEBCHRDownloader(LibraryDownloader):
    URL_LOGIN = "https://neb-chr.ru/login"
    URL_BOOK = "https://neb-chr.ru/read/%s"
    URL_CDN = "https://neb-chr.ru"
    HEADERS = {
        "referer": "https://catalog.libfl.ru/"
    }
    current_section = "NEBCHR"
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

    def open_site(self):
        self.driver.get(self.URL_LOGIN)
        element_username = self.driver.find_element(by=By.ID, value="login_email")
        element_username.clear()
        element_username.send_keys(self.login)
        element_password = self.driver.find_element(by=By.ID, value="login_password")
        element_password.clear()
        element_password.send_keys(self.password)
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        element_button_ok = wait.until(
            expected_conditions.element_to_be_clickable(
                (By.XPATH, "//button[text()='Войти']")))

        element_button_ok.click()


    def download_book(self, book_id, queue, page_from=1):
        error_text = None
        try:
            self.queue = queue
            self.create_folders(book_id)
            self.page_from = page_from
            self.make_pause()
            self.open_site()
            self.make_pause()
            self.download_pages(book_id)
        except Exception as e:
            logging.error(f"Exception occurred {e}")
            error_text = str(e)
        return error_text, os.path.abspath(self.folder)

    def download_pages(self, book_id):
        cookies = self.driver.get_cookies()
        cookies_str = ";".join([c["name"] + "=" + c["value"] for c in cookies])

        book_url = self.URL_BOOK % book_id
        main_page = self.download_html(book_url, additional_headers={"Cookie": cookies_str, })
        self.make_pause()
        match = re.search('const fileName = (.+?);',
                              main_page.text, re.S)
        if not match:
            raise Exception(f"Could not start downloading")

        book_link = match.group(1).strip().strip('"')
        image_link = self.URL_CDN + book_link
        response = requests.get(image_link)
        full_filename = os.path.join(self.folder, book_id + ".pdf")
        if not response.ok:
            raise Exception(f"Exception when downloading")
        self.queue.put_nowait(0.5)
        with open(full_filename, 'wb') as fout:
            fout.write(response.content)
            logging.info(f"Downloaded book")
        self.queue.put_nowait(1)