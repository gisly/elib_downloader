import logging
import os

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
from tqdm import tqdm

from abstract_lib_downloader import LibraryDownloader


class NLRSDownloader(LibraryDownloader):
    URL_LOGIN = "https://e.nlrs.ru/login"
    URL_BOOK = "https://e.nlrs.ru/online/"
    current_section = "NLRS"
    driver = None
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
        element_username = self.driver.find_element(by=By.NAME, value="username")
        element_username.clear()
        element_username.send_keys(self.login)
        element_password = self.driver.find_element(by=By.NAME, value="password")
        element_password.clear()
        element_password.send_keys(self.password)
        element_button_ok = self.driver.find_element(by=By.CLASS_NAME, value="btn-primary")
        element_button_ok.click()

    def download_pages(self, book_id):
        self.driver.get(self.URL_BOOK + book_id)
        self.move_to_beginning()
        self.download_images()

    def move_to_beginning(self):
        self.make_pause()
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        slider = wait.until(
            expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, "input[type='range']")))
        current_page = int(slider.get_attribute("value"))
        self.last_page = int(slider.get_attribute("max")) + 1
        if current_page > self.page_from:
            for i in range(current_page, self.page_from - 1, -1):
                self.make_pause()
                button_previous_page = self.get_previous_page_button()
                button_previous_page.click()
                logging.info(f"Moving to page {i}")
        logging.info(f"Moved to page {self.page_from}")

    def download_images(self):
        cookies = self.driver.get_cookies()
        cookies_str = ";".join([c["name"] + "=" + c["value"] for c in cookies])
        n = self.last_page + 1
        for i in tqdm(range(1, self.last_page + 1)):
            self.queue.put_nowait(round(i / n, 2))
            if i >= self.page_from:
                logging.info(f"Downloading page {i} out of {self.last_page}")
                filename = str(i).zfill(4) + ".png"
                full_filename = os.path.join(self.folder, filename)
                self.make_pause()
                wait = WebDriverWait(self.driver, 0)
                element_file_viewer = wait.until(
                    expected_conditions.element_to_be_clickable((By.ID, "root")))
                element_image = element_file_viewer. \
                    find_element(by=By.ID, value="page-container"). \
                    find_element(by=By.TAG_NAME, value="img")
                image_link = element_image.get_attribute("src")
                headers = {
                    "cookie": cookies_str,
                }
                response = requests.get(image_link, headers=headers)
                with open(full_filename, 'wb') as fout:
                    fout.write(response.content)
                logging.info(f"Downloaded page {i} out of {self.last_page}")
            if i < self.last_page:
                self.get_next_page_button().click()

    def get_next_page_button(self):
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        return wait.until(
            expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, "button[title='Перейти на следующую страницу']")))


    def get_previous_page_button(self):
        wait = WebDriverWait(self.driver, self.PAUSE_SEC)
        return wait.until(
            expected_conditions.element_to_be_clickable((By.CSS_SELECTOR, "button[title='Перейти на предыдущую страницу']")))

