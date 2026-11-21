"""Реестр поддерживаемых библиотек, общий для интерфейсов и задач загрузки."""

from dataclasses import dataclass
from typing import Callable

from kazneb_downloader import KAZNEBDownloader
from libfl_downloader import LIBFLDownloader
from nebchr_downloader import NEBCHRDownloader
from nlrs_downloader import NLRSDownloader
from pdfreader_downloader import PDFReaderDownloader
from pgpb_downloader import PGPBDownloader
from prlib_downloader import PRlibDownloader
from rgo_downloader import RGODownloader
from shpl_downloader import SHPLDownloader


@dataclass(frozen=True)
class Source:
    needs_auth: bool
    hint: str
    factory: Callable


SOURCES = {
    "NLRS": Source(True, "Для ссылки вида https://e.nlrs.ru/open/1644 укажите 1644", NLRSDownloader),
    "RGO": Source(False, "Вставьте ссылку на карточку /handle/123456789/ID или полную ссылку /safe-view/...", RGODownloader),
    "PRLIB": Source(False, "Укажите ID (363162) или полную ссылку https://www.prlib.ru/item/363162", PRlibDownloader),
    "PGPB": Source(False, "Для ссылки вида https://pgpb.ru/digitization/document/4375 укажите 4375", PGPBDownloader),
    "SHPL": Source(False, "Для ссылки вида http://elib.shpl.ru/pages/5006468/ укажите 5006468", SHPLDownloader),
    "PDF_READER": Source(False, "Укажите полную ссылку, например http://62.249.142.211:8083/read/88/pdf", PDFReaderDownloader),
    "LIBFL": Source(True, "Укажите bookID=BJVVV_604652 или OrderId=920010", LIBFLDownloader),
    "NEBCHR": Source(True, "Для ссылки вида https://neb-chr.ru/books/3041/details укажите 3041", NEBCHRDownloader),
    "KAZNEB": Source(False, "Для ссылки вида https://kazneb.kz/ru/catalogue/view/1543925 укажите 1543925", KAZNEBDownloader),
}


def download(source_name, config, book_id, progress_queue, cancel_event=None):
    source = SOURCES[source_name]
    if source_name == "PDF_READER":
        downloader = source.factory(config, book_id)
    else:
        downloader = source.factory(config)
    downloader.cancel_event = cancel_event
    downloader.detail_events = True
    try:
        error, location = downloader.download_book(book_id, progress_queue)
        downloader.finalize_download(error)
        return error, location
    except Exception as exc:
        downloader.finalize_download(exc)
        raise
    finally:
        downloader.close_http()
        # Close browser even if a user cancels before download_book returns.
        driver = getattr(downloader, "driver", None)
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
