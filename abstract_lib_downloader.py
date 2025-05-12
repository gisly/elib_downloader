import logging
import os
import time
from abc import ABC

import configparser
from multiprocessing import Queue

import requests
from bs4 import BeautifulSoup


class LibraryDownloader(ABC):
    PAUSE_SEC = 1
    RETRY_NUM = 3
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    CONFIG_FILE = "config.ini"
    CONFIG_LOGIN = "login"
    CONFIG_PASSWORD = "password"
    CONFIG_FOLDER = "folder"

    current_section: str = NotImplemented
    login: str = NotImplemented
    password: str = NotImplemented
    last_page: int = NotImplemented
    folder: str = NotImplemented
    root_folder: str = NotImplemented
    section_folder: str = NotImplemented

    @classmethod
    def download_book(cls, book_url, queue: Queue):
        pass

    def make_pause(self):
        time.sleep(self.PAUSE_SEC)

    def init_common(self):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    def init_authorized_access(self, config=None):
        self.init_common()
        if config is None:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_FILE, encoding='utf-8')
        if not config.has_section(self.current_section):
            raise Exception(f"No configuration for {self.current_section}")
        if not config.has_option(self.current_section, self.CONFIG_LOGIN):
            raise Exception(f"No property for {self.CONFIG_LOGIN}")
        if not config.has_option(self.current_section, self.CONFIG_PASSWORD):
            raise Exception(f"No property for {self.CONFIG_PASSWORD}")
        if not config.has_option(self.current_section, self.CONFIG_FOLDER):
            self.root_folder = "."
        else:
            self.root_folder = config.get(self.current_section, self.CONFIG_FOLDER)
        logging.info(f"Using folder {os.path.abspath(self.root_folder)}")
        self.login = config.get(self.current_section, self.CONFIG_LOGIN)
        self.password = config.get(self.current_section, self.CONFIG_PASSWORD)

    def init_non_authorized_access(self, config=None):
        self.init_common()
        if config is None:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_FILE, encoding='utf-8')
        if not config.has_option(self.current_section, self.CONFIG_FOLDER):
            self.root_folder = "."
        else:
            self.root_folder = config.get(self.current_section, self.CONFIG_FOLDER)
        logging.info(f"Using folder {os.path.abspath(self.root_folder)}")

    def create_folders(self, book_id):
        self.folder = os.path.join(self.root_folder, self.current_section + "_" + book_id)
        os.makedirs(self.folder, exist_ok=True)

    def create_common_section_folder(self):
        self.section_folder = os.path.join(self.root_folder, self.current_section)
        os.makedirs(self.section_folder, exist_ok=True)

    def get_page_content(self, url, additional_headers=None):
        if additional_headers is None:
            additional_headers = dict()
        i = 0
        result = None
        headers = {"User-Agent": self.USER_AGENT}
        headers.update(additional_headers)
        while i < self.RETRY_NUM:
            result = requests.get(url, headers=headers)
            i += 1
            if result.status_code == 429:
                logging.error(f"Error {result.status_code} received when downloading {url}")
                self.make_pause()
            elif result.status_code == 200:
                break
            else:
                raise Exception(f"Error downloading {url}: {result.status_code}")
        if result.status_code == 429:
            raise Exception(f"Error downloading {url}: {result.status_code}")
        return result

    def save_image(self, url, output_filename):
        result = self.get_page_content(url)
        image = result.content
        with open(output_filename, 'wb') as fout:
            fout.write(image)

    def download_html(self, url, additional_headers=None):
        if additional_headers is None:
            additional_headers = dict()
        result = self.get_page_content(url, additional_headers)
        html = result.text
        return BeautifulSoup(html, features="html5lib")
