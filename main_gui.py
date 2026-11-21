#!/usr/bin/env python3
"""Desktop interface for elib_downloader: resume, cancel and clear status."""

from __future__ import annotations

import configparser
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from clipboard_shortcuts import install_entry_context_menu, install_entry_shortcuts
from resume import ResumeJournal, folder_id
from sources import SOURCES, download
from transfer import readable_speed, readable_eta
from windows_keyboard import WindowsShortcutPoller, needs_windows_fallback

# Light interface, consistent hierarchy; no UI framework beyond CustomTkinter.
BG = "#F4F5F3"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F2F5F3"
INK = "#1D322B"
MUTED = "#66766E"
GREEN = "#276C53"
GREEN_HOVER = "#1C5943"
GREEN_SOFT = "#E5F1EA"
BORDER = "#DCE5DF"
ERROR = "#AE4141"
SUCCESS = "#26794F"

LIBRARY_NAMES = {
    "NLRS": "Национальная библиотека Республики Саха",
    "RGO": "Русское географическое общество",
    "PRLIB": "Президентская библиотека",
    "PGPB": "Пермская государственная библиотека",
    "SHPL": "Государственная публичная историческая библиотека",
    "PDF_READER": "Просмотрщик PDF",
    "LIBFL": "Библиотека иностранной литературы",
    "NEBCHR": "Национальная библиотека Чувашии",
    "KAZNEB": "Казахстанская национальная электронная библиотека",
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("elib_downloader")
        self.geometry("1040x690")
        self.minsize(860, 610)
        self.configure(fg_color=BG)

        self.folder = os.path.abspath(".")
        self.last_result_folder = self.folder
        self.is_downloading = False
        self.queue: queue.Queue | None = None
        self.cancel_event: threading.Event | None = None
        self._keyboard_poller = None
        self._context_menus = []

        self._build_ui()
        self._bind_clipboard_shortcuts()
        self._on_source_changed()
        self._poll_progress()

    def _label(self, parent, text):
        return ctk.CTkLabel(parent, text=text, text_color=INK,
                            font=ctk.CTkFont(size=13, weight="bold"), anchor="w")

    def _entry(self, parent, placeholder, show=None):
        return ctk.CTkEntry(parent, placeholder_text=placeholder,
                            fg_color=SURFACE, text_color=INK, border_color=BORDER,
                            placeholder_text_color=MUTED, height=43,
                            font=ctk.CTkFont(size=14), corner_radius=9, show=show)

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color=INK, corner_radius=0, height=82)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="elib_downloader", text_color="#FFFFFF",
                     font=ctk.CTkFont(size=23, weight="bold")).pack(
                         anchor="w", padx=30, pady=(15, 0))
        ctk.CTkLabel(header, text="Электронные библиотеки — без потери прогресса",
                     text_color="#C9DDD1", font=ctk.CTkFont(size=12)).pack(
                         anchor="w", padx=31)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=22)
        body.grid_columnconfigure(0, weight=6, uniform="columns")
        body.grid_columnconfigure(1, weight=4, uniform="columns")
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=16,
                            border_width=1, border_color=BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        controls = ctk.CTkFrame(left, fg_color="transparent")
        controls.pack(fill="both", expand=True, padx=27, pady=24)

        ctk.CTkLabel(controls, text="Новая загрузка", text_color=INK,
                     font=ctk.CTkFont(size=21, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(controls, text="Оставшиеся страницы скачаются автоматически",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(
                         anchor="w", pady=(2, 22))

        self._label(controls, "1. Библиотека").pack(anchor="w", pady=(0, 7))
        self.source_var = ctk.StringVar(value="PRLIB")
        self.source_menu = ctk.CTkOptionMenu(
            controls, variable=self.source_var, values=list(SOURCES),
            command=lambda _: self._on_source_changed(),
            fg_color=GREEN, button_color=GREEN, button_hover_color=GREEN_HOVER,
            dropdown_fg_color=SURFACE, dropdown_text_color=INK,
            dropdown_hover_color=GREEN_SOFT, text_color="#FFFFFF",
            width=300, height=40, corner_radius=9,
            font=ctk.CTkFont(size=14, weight="bold"))
        self.source_menu.pack(fill="x")
        self.source_name_label = ctk.CTkLabel(
            controls, text="", text_color=MUTED, anchor="w", wraplength=430,
            font=ctk.CTkFont(size=12))
        self.source_name_label.pack(fill="x", pady=(6, 16))

        self._label(controls, "2. Ссылка или номер книги").pack(anchor="w", pady=(0, 7))
        self.book_id_entry = self._entry(controls, "Вставьте ссылку на книгу")
        self.book_id_entry.pack(fill="x")
        self.book_id_entry.bind("<KeyRelease>", lambda _e: self._refresh_resume_hint(), add="+")
        self.book_id_entry.bind("<FocusOut>", lambda _e: self._refresh_resume_hint(), add="+")
        self.source_hint = ctk.CTkLabel(controls, text="", text_color=MUTED,
                                        font=ctk.CTkFont(size=12),
                                        justify="left", anchor="w", wraplength=430)
        self.source_hint.pack(fill="x", pady=(6, 12))

        self.credentials = ctk.CTkFrame(controls, fg_color=SURFACE_ALT, corner_radius=10)
        credentials_inner = ctk.CTkFrame(self.credentials, fg_color="transparent")
        credentials_inner.pack(fill="x", padx=12, pady=12)
        self._label(credentials_inner, "Доступ к библиотеке").pack(anchor="w", pady=(0, 7))
        self.login_entry = self._entry(credentials_inner, "Логин или email")
        self.login_entry.pack(fill="x", pady=(0, 7))
        self.password_entry = self._entry(credentials_inner, "Пароль", show="•")
        self.password_entry.pack(fill="x")

        self._label(controls, "3. Куда сохранить").pack(anchor="w", pady=(13, 7))
        path_row = ctk.CTkFrame(controls, fg_color="transparent")
        path_row.pack(fill="x")
        self.folder_label = ctk.CTkLabel(
            path_row, text=self._display_path(self.folder), anchor="w",
            text_color=MUTED, font=ctk.CTkFont(size=12))
        self.folder_label.pack(side="left", expand=True, fill="x")
        ctk.CTkButton(path_row, text="Выбрать…", width=110, height=34,
                      fg_color=SURFACE_ALT, hover_color=BORDER, text_color=INK,
                      border_color=BORDER, border_width=1, corner_radius=8,
                      command=self._pick_folder).pack(side="right")

        button_row = ctk.CTkFrame(controls, fg_color="transparent")
        button_row.pack(fill="x", pady=(22, 0))
        self.download_btn = ctk.CTkButton(
            button_row, text="Скачать / продолжить", fg_color=GREEN,
            hover_color=GREEN_HOVER, height=47, corner_radius=10,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start_download)
        self.download_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.cancel_btn = ctk.CTkButton(
            button_row, text="Остановить", width=108, height=47,
            fg_color=SURFACE_ALT, hover_color=BORDER, text_color=INK,
            corner_radius=10, state="disabled", command=self._cancel_download)
        self.cancel_btn.pack(side="right")

        right = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=16,
                             border_width=1, border_color=BORDER)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        pane = ctk.CTkFrame(right, fg_color="transparent")
        pane.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(pane, text="Состояние загрузки", text_color=INK,
                     font=ctk.CTkFont(size=19, weight="bold")).pack(anchor="w")
        self.status_tag = ctk.CTkLabel(
            pane, text="Ожидание", fg_color=SURFACE_ALT,
            text_color=GREEN, corner_radius=7, height=27,
            font=ctk.CTkFont(size=12, weight="bold"))
        self.status_tag.pack(anchor="w", pady=(12, 18), ipadx=12)

        progress_row = ctk.CTkFrame(pane, fg_color="transparent")
        progress_row.pack(fill="x")
        self.progress_summary = ctk.CTkLabel(
            progress_row, text="Готово к скачиванию", text_color=INK,
            anchor="w", font=ctk.CTkFont(size=14, weight="bold"))
        self.progress_summary.pack(side="left", fill="x", expand=True)
        self.progress_percent = ctk.CTkLabel(
            progress_row, text="0%", text_color=GREEN,
            font=ctk.CTkFont(size=14, weight="bold"))
        self.progress_percent.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(
            pane, fg_color=SURFACE_ALT, progress_color=GREEN,
            height=11, corner_radius=6)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(10, 7))
        self.transfer_label = ctk.CTkLabel(
            pane, text="Скорость: —    ·    Осталось: —", text_color=MUTED,
            anchor="w", font=ctk.CTkFont(size=12))
        self.transfer_label.pack(fill="x", pady=(0, 9))
        self.status_label = ctk.CTkLabel(
            pane, text="Выберите библиотеку и укажите книгу.",
            text_color=MUTED, justify="left", anchor="w", wraplength=320,
            font=ctk.CTkFont(size=12))
        self.status_label.pack(fill="x")
        self.resume_hint = ctk.CTkLabel(
            pane, text="", text_color=GREEN, justify="left", anchor="w",
            wraplength=320, font=ctk.CTkFont(size=12))
        self.resume_hint.pack(fill="x", pady=(10, 0))

        ctk.CTkFrame(pane, height=1, fg_color=BORDER).pack(fill="x", pady=20)
        ctk.CTkLabel(pane, text="Ход работы", text_color=INK,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(0, 9))
        self.activity = ctk.CTkTextbox(
            pane, fg_color=SURFACE_ALT, text_color=INK, border_width=0,
            corner_radius=9, font=ctk.CTkFont(size=12), wrap="word", height=125)
        self.activity.pack(fill="both", expand=True)
        self.activity.insert("end", "Здесь появятся этапы загрузки и сообщения об ошибках.\n")
        self.activity.configure(state="disabled")
        ctk.CTkButton(
            pane, text="Открыть папку с файлами", height=38,
            fg_color=SURFACE_ALT, hover_color=BORDER,
            text_color=INK, corner_radius=8,
            command=self._open_folder).pack(fill="x", pady=(15, 0))

    def _bind_clipboard_shortcuts(self):
        entries = (self.login_entry, self.password_entry, self.book_id_entry)
        for entry in entries:
            self._context_menus.append(install_entry_context_menu(entry))
            if os.name != "nt":
                install_entry_shortcuts(entry)
        if os.name == "nt":
            patchlevel = self.tk.call("info", "patchlevel")
            if needs_windows_fallback(patchlevel):
                try:
                    self._keyboard_poller = WindowsShortcutPoller(self, entries)
                except OSError:
                    logging.exception("Не удалось включить обработку горячих клавиш")

    @staticmethod
    def _display_path(value, limit=46):
        return value if len(value) <= limit else "…" + value[-limit + 1:]

    def _log(self, text):
        self.activity.configure(state="normal")
        self.activity.insert("end", f"{datetime.now():%H:%M}  {text}\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _on_source_changed(self):
        src = self.source_var.get()
        self.source_name_label.configure(text=LIBRARY_NAMES.get(src, src))
        self.source_hint.configure(text=SOURCES[src].hint)
        if SOURCES[src].needs_auth:
            self.credentials.pack(fill="x", pady=(0, 0), after=self.source_hint)
        else:
            self.credentials.pack_forget()
            self.login_entry.delete(0, "end")
            self.password_entry.delete(0, "end")
        self._refresh_resume_hint()

    def _pick_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder)
        if folder:
            self.folder = folder
            self.folder_label.configure(text=self._display_path(folder))
            self._refresh_resume_hint()

    def _resume_folder(self):
        value = self.book_id_entry.get().strip()
        if not value:
            return None
        try:
            return os.path.join(self.folder, folder_id(self.source_var.get(), value))
        except ValueError:
            return None

    def _refresh_resume_hint(self):
        target = self._resume_folder()
        state = ResumeJournal.read(target) if target else None
        if state and state.get("status") != "completed":
            done, total = state.get("completed", 0), state.get("total")
            suffix = f" из {total}" if total is not None else ""
            self.resume_hint.configure(
                text=f"↻ Найдена незавершённая загрузка: {done}{suffix}. "
                     "Готовые файлы будут проверены и пропущены.")
        elif state and state.get("status") == "completed":
            self.resume_hint.configure(text="✓ Книга уже скачивалась. Готовые файлы будут проверены.")
        else:
            self.resume_hint.configure(text="Докачка включена автоматически для всех библиотек.")

    def _start_download(self):
        if self.is_downloading:
            return
        source = self.source_var.get()
        book = self.book_id_entry.get().strip()
        if not book:
            self._set_status("Вставьте ссылку или идентификатор книги.", ERROR)
            return
        if SOURCES[source].needs_auth and (not self.login_entry.get().strip() or
                                          not self.password_entry.get().strip()):
            self._set_status("Для этой библиотеки нужны логин и пароль.", ERROR)
            return
        config = configparser.ConfigParser()
        config.add_section(source)
        config.set(source, "login", self.login_entry.get())
        config.set(source, "password", self.password_entry.get())
        config.set(source, "folder", self.folder)
        self.is_downloading = True
        self.cancel_event = threading.Event()
        self.queue = queue.Queue()
        self.source_menu.configure(state="disabled")
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status_tag.configure(text="Загружается", text_color=GREEN)
        self.progress_summary.configure(text="Получение данных…")
        self.progress_percent.configure(text="0%")
        self.progress_bar.set(0)
        self.transfer_label.configure(text="Скорость: —    ·    Осталось: —")
        self._set_status("Подключаемся к библиотеке…")
        self._log(f"Начало: {source} — {book[:90]}")
        self.last_result_folder = self._resume_folder() or self.folder
        worker = threading.Thread(
            target=self._download_thread,
            args=(source, config, book, self.queue, self.cancel_event), daemon=True)
        worker.start()

    @staticmethod
    def _download_thread(source, config, book, output, cancel_event):
        try:
            error, folder = download(source, config, book, output, cancel_event)
            output.put_nowait(("finished", error, folder))
        except Exception as exc:
            logging.exception("Ошибка скачивания")
            output.put_nowait(("finished", str(exc), None))

    def _cancel_download(self):
        if not self.is_downloading or self.cancel_event is None:
            return
        self.cancel_event.set()
        self.cancel_btn.configure(state="disabled")
        self.status_tag.configure(text="Остановка…")
        self._log("Запрошена остановка. Сохранённые файлы останутся на диске.")
        self._set_status("Ожидаем завершения текущего запроса…")

    def _download_finished(self, error, folder):
        self.is_downloading = False
        self.source_menu.configure(state="normal")
        self.download_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        cancelled = bool(self.cancel_event and self.cancel_event.is_set())
        if folder:
            self.last_result_folder = folder
        if cancelled:
            self.status_tag.configure(text="Остановлено", text_color=MUTED)
            self._set_status("Загрузка остановлена. Нажмите «Скачать / продолжить», чтобы докачать.")
            self._log("Остановлено. Прогресс сохранён.")
        elif error:
            self.status_tag.configure(text="Ошибка", text_color=ERROR)
            self._set_status(error, ERROR)
            self._log(f"Ошибка: {error}")
        else:
            self.status_tag.configure(text="Готово", text_color=SUCCESS)
            self.progress_bar.set(1)
            self.progress_percent.configure(text="100%")
            self._set_status(f"Сохранено в: {folder}", SUCCESS)
            self._log("Готово. Файлы находятся в выбранной папке.")
        self._refresh_resume_hint()

    def _set_status(self, text, color=MUTED):
        self.status_label.configure(text=text, text_color=color)

    def _update_transfer(self, event):
        speed = readable_speed(event.get("speed_bps"))
        eta = readable_eta(event.get("eta_seconds"))
        self.transfer_label.configure(text=f"Скорость: {speed}    ·    Осталось: {eta}")

    def _poll_progress(self):
        if self.queue is not None:
            try:
                while True:
                    event = self.queue.get_nowait()
                    if isinstance(event, dict) and event.get("type") == "progress":
                        done = event["completed"]
                        total = max(1, event["total"])
                        fraction = max(0.0, min(done / total, 1.0))
                        self.progress_bar.set(fraction)
                        self.progress_percent.configure(text=f"{fraction:.0%}")
                        self.progress_summary.configure(text=f"{done} из {total}")
                        reused = event.get("reused", 0)
                        phase = event.get("phase", "Загрузка страниц")
                        extra = f" · готовых файлов пропущено: {reused}" if reused else ""
                        self._set_status(phase + extra)
                        self._update_transfer(event)
                    elif isinstance(event, dict) and event.get("type") == "transfer":
                        self._set_status(
                            f"{event['phase']}: {event['detail']} из {event['detail_total']}")
                        self._update_transfer(event)
                    elif isinstance(event, dict) and event.get("type") == "metrics":
                        self._update_transfer(event)
                    elif isinstance(event, (int, float)):
                        value = max(0.0, min(float(event), 1.0))
                        self.progress_bar.set(value)
                        self.progress_percent.configure(text=f"{value:.0%}")
                    elif isinstance(event, tuple) and event[0] == "finished":
                        self._download_finished(event[1], event[2])
            except queue.Empty:
                pass
        try:
            self.after(100, self._poll_progress)
        except Exception:
            pass

    def _open_folder(self):
        folder = self.last_result_folder
        if not os.path.isdir(folder):
            folder = self.folder
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as exc:
            self._set_status(f"Не удалось открыть папку: {exc}", ERROR)

    def destroy(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
        if self._keyboard_poller is not None:
            self._keyboard_poller.close()
            self._keyboard_poller = None
        super().destroy()


def main():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("dark-blue")
    app = App()
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
