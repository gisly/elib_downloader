#!/usr/bin/env python3
"""
elib_downloader — Modern Desktop GUI (CustomTkinter)
Replaces the NiceGUI web-based frontend with a native desktop application.
All backend downloaders remain unchanged.
"""

import configparser
import multiprocessing
import os
import queue
import threading
from tkinter import filedialog

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Backend imports (unchanged)
# ---------------------------------------------------------------------------
from libfl_downloader import LIBFLDownloader
from nebchr_downloader import NEBCHRDownloader
from nlrs_downloader import NLRSDownloader
from pdfreader_downloader import PDFReaderDownloader
from pgpb_downloader import PGPBDownloader
from prlib_downloader import PRlibDownloader
from rgo_downloader import RGODownloader
from shpl_downloader import SHPLDownloader
from kazneb_downloader import KAZNEBDownloader

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SOURCES = {
    "NLRS":       {"needs_auth": True,  "hint": "Для ссылки вида https://e.nlrs.ru/open/1644 укажите 1644"},
    "RGO":        {"needs_auth": False, "hint": "Укажите полную ссылку, например\nhttps://elib.rgo.ru/safe-view/123456789/231378/1/..."},
    "PRLIB":      {"needs_auth": False, "hint": "Для ссылки вида https://www.prlib.ru/item/680723 укажите 680723"},
    "PGPB":       {"needs_auth": False, "hint": "Для ссылки вида https://pgpb.ru/digitization/document/4375 укажите 4375"},
    "SHPL":       {"needs_auth": False, "hint": "Для ссылки вида http://elib.shpl.ru/pages/5006468/ укажите 5006468"},
    "PDF_READER": {"needs_auth": False, "hint": "Укажите полную ссылку, например\nhttp://62.249.142.211:8083/read/88/pdf"},
    "LIBFL":      {"needs_auth": True,  "hint": "Укажите ID книги или заказа, например\nbookID=BJVVV_604652 ЛИБО OrderId=920010"},
    "NEBCHR":     {"needs_auth": True,  "hint": "Укажите ID книги, например 3041"},
    "KAZNEB":     {"needs_auth": False, "hint": "Для ссылки вида https://kazneb.kz/ru/catalogue/view/1543925\nукажите 1543925"},
}

DOWNLOAD_FUNCTIONS = {
    "NLRS":       lambda cfg, bid, q: NLRSDownloader(cfg).download_book(bid, q),
    "RGO":        lambda cfg, bid, q: RGODownloader(cfg).download_book(bid, q),
    "PRLIB":      lambda cfg, bid, q: PRlibDownloader(cfg).download_book(bid, q),
    "PGPB":       lambda cfg, bid, q: PGPBDownloader(cfg).download_book(bid, q),
    "SHPL":       lambda cfg, bid, q: SHPLDownloader(cfg).download_book(bid, q),
    "PDF_READER": lambda cfg, bid, q: PDFReaderDownloader(cfg, bid).download_book(bid, q),
    "LIBFL":      lambda cfg, bid, q: LIBFLDownloader(cfg).download_book(bid, q),
    "NEBCHR":     lambda cfg, bid, q: NEBCHRDownloader(cfg).download_book(bid, q),
    "KAZNEB":     lambda cfg, bid, q: KAZNEBDownloader(cfg).download_book(bid, q),
}

# ---------------------------------------------------------------------------
# Color palette & theme
# ---------------------------------------------------------------------------
# Light warm palette — clean, soft, professional
BG_LIGHT      = "#f5f2ed"
BG_CARD       = "#ffffff"
BG_INPUT      = "#f0ece6"
FG_TEXT        = "#2c2c2c"
FG_DIM         = "#7a7670"
ACCENT         = "#c47b2b"
ACCENT_HOVER   = "#a8641f"
SUCCESS        = "#2d9b6e"
ERROR          = "#c94444"
BORDER         = "#ddd7ce"
DISABLED_BG    = "#e8e4de"


class App(ctk.CTk):
    """Main application window."""

    def __init__(self):
        super().__init__()

        # ---- Window setup ----
        self.title("elib_downloader")
        self.geometry("960x640")
        self.minsize(820, 560)
        self.configure(fg_color=BG_LIGHT)

        # State
        self.folder = os.path.abspath(".")
        self.is_downloading = False
        self.queue: queue.Queue | None = None

        self._build_ui()
        self._on_source_changed()
        self._poll_progress()

    # =======================================================================
    # UI Construction
    # =======================================================================
    def _build_ui(self):
        # ---------- Header ----------
        header = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="📚  elib_downloader",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=ACCENT
        ).pack(side="left", padx=20)

        ctk.CTkLabel(
            header, text="Скачивание книг из электронных библиотек",
            font=ctk.CTkFont(size=13), text_color=FG_DIM
        ).pack(side="left", padx=8)

        # ---------- Main content area with two columns ----------
        body = ctk.CTkFrame(self, fg_color=BG_LIGHT)
        body.pack(fill="both", expand=True, padx=20, pady=(16, 20))
        body.columnconfigure(0, weight=1, minsize=360)
        body.columnconfigure(1, weight=1, minsize=360)
        body.rowconfigure(0, weight=1)

        # ===== LEFT COLUMN — controls =====
        left = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=14, border_width=1, border_color=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        inner_left = ctk.CTkFrame(left, fg_color="transparent")
        inner_left.pack(fill="both", expand=True, padx=24, pady=24)

        # Source selector
        self._section_label(inner_left, "Источник")
        self.source_var = ctk.StringVar(value="NLRS")
        self.source_menu = ctk.CTkOptionMenu(
            inner_left, variable=self.source_var,
            values=list(SOURCES.keys()),
            command=lambda _: self._on_source_changed(),
            fg_color=BG_INPUT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_INPUT,
            text_color=FG_TEXT, dropdown_text_color=FG_TEXT,
            font=ctk.CTkFont(size=14), width=280, corner_radius=8
        )
        self.source_menu.pack(anchor="w", pady=(0, 14))

        # Login
        self._section_label(inner_left, "Логин")
        self.login_entry = self._make_entry(inner_left, "Логин (емейл)")
        self.login_entry.pack(fill="x", pady=(0, 10))

        # Password
        self._section_label(inner_left, "Пароль")
        self.password_entry = self._make_entry(inner_left, "Пароль", show="•")
        self.password_entry.pack(fill="x", pady=(0, 10))

        # Book ID
        self._section_label(inner_left, "Идентификатор книги")
        self.book_id_entry = self._make_entry(inner_left, "Идентификатор книги")
        self.book_id_entry.pack(fill="x", pady=(0, 16))

        # Folder chooser
        folder_frame = ctk.CTkFrame(inner_left, fg_color="transparent")
        folder_frame.pack(fill="x", pady=(0, 18))

        ctk.CTkButton(
            folder_frame, text="📁 Папка", width=100,
            fg_color=BG_INPUT, hover_color=BORDER, text_color=FG_TEXT,
            border_width=1, border_color=BORDER, corner_radius=8,
            font=ctk.CTkFont(size=13),
            command=self._pick_folder
        ).pack(side="left")

        self.folder_label = ctk.CTkLabel(
            folder_frame, text=self._truncate_path(self.folder),
            font=ctk.CTkFont(size=12), text_color=FG_DIM, anchor="w"
        )
        self.folder_label.pack(side="left", padx=10, fill="x", expand=True)

        # Download button
        self.download_btn = ctk.CTkButton(
            inner_left, text="⬇  Скачать книгу", height=44,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=BG_CARD, font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10,
            command=self._start_download
        )
        self.download_btn.pack(fill="x", pady=(0, 10))

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            inner_left, progress_color=ACCENT, fg_color=BG_INPUT,
            height=6, corner_radius=3
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.pack_forget()  # hidden initially

        # Status label
        self.status_label = ctk.CTkLabel(
            inner_left, text="", font=ctk.CTkFont(size=12),
            text_color=FG_DIM, anchor="w", wraplength=320
        )
        self.status_label.pack(fill="x")

        # ===== RIGHT COLUMN — help / source info =====
        right = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=14, border_width=1, border_color=BORDER)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        inner_right = ctk.CTkFrame(right, fg_color="transparent")
        inner_right.pack(fill="both", expand=True, padx=24, pady=24)

        ctk.CTkLabel(
            inner_right, text="Справка по источникам",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=FG_TEXT
        ).pack(anchor="w", pady=(0, 14))

        # Scrollable list of sources
        scroll = ctk.CTkScrollableFrame(
            inner_right, fg_color="transparent",
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=FG_DIM
        )
        scroll.pack(fill="both", expand=True)

        for name, info in SOURCES.items():
            card = ctk.CTkFrame(scroll, fg_color=BG_INPUT, corner_radius=10, border_width=1, border_color=BORDER)
            card.pack(fill="x", pady=(0, 8))

            card_inner = ctk.CTkFrame(card, fg_color="transparent")
            card_inner.pack(fill="x", padx=14, pady=10)

            title_row = ctk.CTkFrame(card_inner, fg_color="transparent")
            title_row.pack(fill="x")

            ctk.CTkLabel(
                title_row, text=name,
                font=ctk.CTkFont(size=14, weight="bold"), text_color=ACCENT
            ).pack(side="left")

            if info["needs_auth"]:
                ctk.CTkLabel(
                    title_row, text="🔒 Нужна регистрация",
                    font=ctk.CTkFont(size=11), text_color=FG_DIM
                ).pack(side="right")

            ctk.CTkLabel(
                card_inner, text=info["hint"],
                font=ctk.CTkFont(size=12), text_color=FG_DIM,
                anchor="w", justify="left", wraplength=360
            ).pack(fill="x", pady=(4, 0))

        # ---------- Hint bar for selected source ----------
        self.hint_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=36)
        self.hint_frame.pack(fill="x")
        self.hint_frame.pack_propagate(False)

        self.hint_label = ctk.CTkLabel(
            self.hint_frame, text="",
            font=ctk.CTkFont(size=12), text_color=FG_DIM
        )
        self.hint_label.pack(side="left", padx=20)

    # =======================================================================
    # Helpers
    # =======================================================================
    @staticmethod
    def _section_label(parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=12, weight="bold"), text_color=FG_DIM
        ).pack(anchor="w", pady=(0, 4))

    @staticmethod
    def _make_entry(parent, placeholder, show=None):
        entry = ctk.CTkEntry(
            parent, placeholder_text=placeholder,
            fg_color=BG_INPUT, border_color=BORDER, text_color=FG_TEXT,
            placeholder_text_color=FG_DIM,
            font=ctk.CTkFont(size=14), height=38, corner_radius=8,
            show=show
        )
        return entry

    @staticmethod
    def _truncate_path(path, max_len=45):
        if len(path) <= max_len:
            return path
        return "…" + path[-(max_len - 1):]

    # =======================================================================
    # Callbacks
    # =======================================================================
    def _on_source_changed(self):
        src = self.source_var.get()
        info = SOURCES[src]
        needs_auth = info["needs_auth"]

        if needs_auth:
            self.login_entry.configure(state="normal", fg_color=BG_INPUT)
            self.password_entry.configure(state="normal", fg_color=BG_INPUT)
        else:
            self.login_entry.delete(0, "end")
            self.password_entry.delete(0, "end")
            self.login_entry.configure(state="disabled", fg_color=DISABLED_BG)
            self.password_entry.configure(state="disabled", fg_color=DISABLED_BG)

        self.hint_label.configure(text=f"{src}: {info['hint'].splitlines()[0]}")

    def _pick_folder(self):
        path = filedialog.askdirectory(initialdir=self.folder)
        if path:
            self.folder = path
            self.folder_label.configure(text=self._truncate_path(self.folder))

    def _start_download(self):
        if self.is_downloading:
            return

        src = self.source_var.get()
        book_id = self.book_id_entry.get().strip()
        if not book_id:
            self._set_status("Укажите идентификатор книги", ERROR)
            return

        if SOURCES[src]["needs_auth"]:
            if not self.login_entry.get().strip() or not self.password_entry.get().strip():
                self._set_status("Укажите логин и пароль", ERROR)
                return

        self.is_downloading = True
        self.download_btn.configure(state="disabled", fg_color=BORDER)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self._set_status("Скачивание… Пожалуйста, подождите.", FG_DIM)

        # Build config
        config = configparser.ConfigParser()
        config.read_string(f"[{src}]")
        config[src]["login"] = self.login_entry.get()
        config[src]["password"] = self.password_entry.get()
        config[src]["folder"] = self.folder

        self.queue = queue.Queue()

        thread = threading.Thread(
            target=self._download_thread,
            args=(src, config, book_id, self.queue),
            daemon=True,
        )
        thread.start()

    def _download_thread(self, source, config, book_id, q: queue.Queue):
        try:
            func = DOWNLOAD_FUNCTIONS[source]
            error_text, result = func(config, book_id, q)
            if error_text:
                self.after(0, self._download_finished, f"Ошибка: {error_text}", ERROR)
            else:
                self.after(0, self._download_finished, f"Книга сохранена в {result}", SUCCESS)
        except Exception as e:
            self.after(0, self._download_finished, f"Ошибка: {e}", ERROR)

    def _download_finished(self, message, color):
        self.is_downloading = False
        self.download_btn.configure(state="normal", fg_color=ACCENT)
        self.progress_bar.pack_forget()
        self._set_status(message, color)

    def _set_status(self, text, color=FG_DIM):
        self.status_label.configure(text=text, text_color=color)

    # =======================================================================
    # Progress polling
    # =======================================================================
    def _poll_progress(self):
        if self.is_downloading and self.queue:
            try:
                while not self.queue.empty():
                    val = self.queue.get_nowait()
                    if isinstance(val, (int, float)):
                        self.progress_bar.set(min(val, 1.0))
            except Exception:
                pass
        self.after(100, self._poll_progress)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("dark-blue")

    app = App()
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # Required for PyInstaller on Windows
    main()
