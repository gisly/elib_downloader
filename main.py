#!/usr/bin/env python3
import configparser
import os.path
from multiprocessing import Manager, Queue
import cairo
from nicegui import run, ui

from local_file_picker import local_file_picker
from nlrs_downloader import NLRSDownloader
from pdfreader_downloader import PDFReaderDownloader
from pgpb_downloader import PGPBDownloader
from prlib_downloader import PRlibDownloader
from rgo_downloader import RGODownloader
from shpl_downloader import SHPLDownloader

queue = None
is_book_download_in_progress = False
progressbar = None
folder_displayed = None
spinner = None
login = None
password = None
button_download = None
button_choose_folder = None
selector_book_source = None
folder = "."

NLRS = "NLRS"
RGO = "RGO"
PRLIB = "PRLIB"
PGPB = "PGPB"
SHPL = "SHPL"
PDF_READER = "PDF_READER"

async def handle_click():
    methods = {NLRS: download_nlrs, RGO: download_rgo,
               PRLIB: download_prlib,
               PGPB: download_pgpb, SHPL: download_shpl,
               PDF_READER: download_pdfreader}
    global is_book_download_in_progress
    global queue
    if is_book_download_in_progress:
        return
    config = configparser.ConfigParser()
    source_chosen = selector_book_source.value
    config.read_string(f"[{source_chosen}]")
    config[source_chosen]["login"] = login.value
    config[source_chosen]["password"] = password.value
    config[source_chosen]["folder"] = folder
    progressbar.visible = True
    is_book_download_in_progress = True
    button_download.disable()
    button_choose_folder.disable()
    queue = Manager().Queue()
    try:
        method_chosen = methods[source_chosen]
        error_text, result = await run.cpu_bound(method_chosen, config, book_id.value, queue)
        if error_text:
            ui.notify(f"Произошла ошибка: {error_text}", type="negative", timeout=0, close_button=True)
        else:
            ui.notify(f"Книга сохранена в {result}", timeout=0, close_button=True)
        progressbar.visible = False
    except Exception as e:
        ui.notify(f"Произошла ошибка: {str(e)}", type="negative", timeout=0, close_button=True)
    finally:
        is_book_download_in_progress = False
        button_download.enable()
        button_choose_folder.enable()


async def process_text_fields():
    if is_book_download_in_progress:
        return
    if selector_book_source.value == NLRS:
        if len(login.value) > 0 and len(password.value) > 0 and len(book_id.value) > 0:
            button_download.enable()
        else:
            button_download.disable()
    else:
        if len(book_id.value) > 0:
            button_download.enable()
        else:
            button_download.disable()


async def pick_file() -> None:
    global folder
    global folder_displayed
    folders = await local_file_picker('~', multiple=False, show_folders_only=True)
    folder = folders[0]
    folder_displayed.set_text(folder)


def download_nlrs(config, book_id_value, queue: Queue):
    nlrs_downloader = NLRSDownloader(config)
    return nlrs_downloader.download_book(book_id_value, queue)


def download_rgo(config, book_id_value, queue: Queue):
    rgo_downloader = RGODownloader(config)
    return rgo_downloader.download_book(book_id_value, queue)


def download_prlib(config, book_id_value, queue: Queue):
    prlib_downloader = PRlibDownloader(config)
    return prlib_downloader.download_book(book_id_value, queue)


def download_pgpb(config, book_id_value, queue: Queue):
    pgpb_downloader = PGPBDownloader(config)
    return pgpb_downloader.download_book(book_id_value, queue)


def download_shpl(config, book_id_value, queue: Queue):
    shpl_downloader = SHPLDownloader(config)
    return shpl_downloader.download_book(book_id_value, queue)


def download_pdfreader(config, book_id_value, queue: Queue):
    pdfreader_downloader = PDFReaderDownloader(config, book_id_value)
    return pdfreader_downloader.download_book(book_id_value, queue)


def draw(surface: cairo.Surface) -> None:
    context = cairo.Context(surface)
    context.select_font_face("Arial", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    context.set_font_size(20)
    context.move_to(10, 40)
    if login:
        context.show_text(login.value)
    context.move_to(10, 80)
    if password:
        context.show_text(password.value)


def process_timer(progressbar, spinner, queue):
    global is_book_download_in_progress
    progressbar.set_value(queue.get() if queue and not queue.empty() else progressbar.value)
    if is_book_download_in_progress:
        spinner.visible = True
    else:
        spinner.visible = False


@ui.page("/")
def main_page():
    global queue
    global login
    global password
    global book_id
    global button_download
    global button_choose_folder
    global progressbar
    global spinner
    global folder_displayed
    global selector_book_source
    queue = Manager().Queue()
    ui.page_title("LibDownloader")

    with ui.row():
        with ui.column():
            selector_book_source = ui.select([NLRS, RGO, PRLIB, PGPB, SHPL, PDF_READER], value=NLRS, on_change=process_text_fields)
            login = ui.input("Логин", placeholder="Логин (емейл)", on_change=process_text_fields)
            password = ui.input("Пароль", placeholder="Пароль", on_change=process_text_fields)
            book_id = ui.input("Идентификатор книги", placeholder="Идентификатор книги", on_change=process_text_fields)
        with ui.column():
            button_download = ui.button("Скачать книгу", on_click=lambda: handle_click())
            button_download.disable()
            button_choose_folder = ui.button("Указать папку для сохранения", on_click=pick_file, icon='folder')
            folder_displayed = ui.label(os.path.abspath(folder))
        with ui.column():
            with ui.list().props("dense separator"):
                with ui.item():
                    with ui.item_section():
                        ui.item_label(NLRS)
                        ui.item_label("Необходима регистрация").props('caption')
                        ui.item_label("Для ссылки вида https://e.nlrs.ru/open/1644 укажите 1644").props(
                            "caption")
                with ui.item():
                    with ui.item_section():
                        ui.item_label(RGO)
                        ui.item_label("Укажите полную ссылку, например").props('caption')
                        ui.item_label(
                            "https://elib.rgo.ru/safe-view/123456789/231378/1/MTM0OTVfVHVuZ3Vzc2tvLXJ1c3NraWkgc2xvdmFyJyBla3NwZWRpY2l5YSBwbyBpenVjaGVuaS5wZGY=").props(
                            "caption")
                with ui.item():
                    with ui.item_section():
                        ui.item_label(PRLIB)
                        ui.item_label("Для ссылки вида https://www.prlib.ru/item/680723 укажите 680723").props(
                            "caption")

                with ui.item():
                    with ui.item_section():
                        ui.item_label(PGPB)
                        ui.item_label("Для ссылки вида https://pgpb.ru/digitization/document/4375 укажите 4375").props(
                            "caption")

                with ui.item():
                    with ui.item_section():
                        ui.item_label(SHPL)
                        ui.item_label("Для ссылки вида http://elib.shpl.ru/pages/5006468/ укажите 5006468").props(
                            "caption")

                with ui.item():
                    with ui.item_section():
                        ui.item_label(PDF_READER)
                        ui.item_label("Укажите полную ссылку, например").props('caption')
                        ui.item_label(
                            "http://62.249.142.211:8083/read/88/pdf").props(
                            "caption")
            spinner = ui.spinner("dots", size="lg", color="red")
            spinner.visible = False
            progressbar = ui.linear_progress(value=0, show_value=False).props("instant-feedback")
            progressbar.visible = False
            ui.timer(0.1,
                     callback=lambda: process_timer(progressbar, spinner, queue))


ui.run(native=True, reconnect_timeout=0, window_size=(1500, 500))
