"""Crash-safe downloads shared by all library adapters.

Completed files are verified before being reused. Files are written into
`.part` and atomically promoted. A small state file is informational only:
recovery *never* trusts it instead of checking the actual downloaded files.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError
try:
    from PyPDF2 import PdfReader
except ImportError:  # pypdf is the maintained successor; both APIs are compatible here.
    from pypdf import PdfReader


class DownloadCancelled(RuntimeError):
    def __init__(self):
        super().__init__("Загрузка остановлена. Уже сохранённые страницы останутся на диске — можно продолжить позже.")


IMAGE_PREFIXES = (
    b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a",
)


def valid_file(filename, kind="image"):
    """Check completed content (not just file existence)."""
    try:
        path = Path(filename)
        if not path.is_file() or path.stat().st_size == 0:
            return False
        if kind == "image":
            with Image.open(path) as image:
                image.verify()
        elif kind == "signature":  # for servers returning uncommon supported image codecs
            with path.open("rb") as stream:
                prefix = stream.read(16)
            if not (prefix.startswith(IMAGE_PREFIXES) or
                    prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP"):
                return False
            # A correct signature alone does not prove that the image isn't
            # truncated. Cache only images Pillow can open completely.
            with Image.open(path) as image:
                image.verify()
        elif kind == "pdf":
            with path.open("rb") as stream:
                if not stream.read(5) == b"%PDF-":
                    return False
                stream.seek(max(0, path.stat().st_size - 2048))
                if b"%%EOF" not in stream.read():
                    return False
            with path.open("rb") as stream:
                PdfReader(stream, strict=False).pages.__len__()
        elif kind != "binary":
            raise ValueError(f"Unknown file type: {kind}")
        return True
    except Exception as exc:
        # PdfReader can raise parser-specific errors for truncated files.
        if isinstance(exc, ValueError) and str(exc).startswith("Unknown file type"):
            raise
        return False


def validate_bytes(data: bytes, kind="image"):
    if not data:
        raise ValueError("Сервер вернул пустой файл")
    if kind == "image":
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ValueError("Сервер вернул данные, не являющиеся корректным изображением") from exc
    elif kind == "signature":
        if not (data.startswith(IMAGE_PREFIXES) or
                data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            raise ValueError("Сервер вернул данные, не являющиеся изображением")
    elif kind == "pdf":
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
            raise ValueError("Сервер вернул неполный PDF (или HTML вместо PDF)")
        try:
            PdfReader(io.BytesIO(data), strict=False).pages.__len__()
        except Exception as exc:
            raise ValueError("Полученный PDF повреждён") from exc
    elif kind != "binary":
        raise ValueError(f"Unknown file type: {kind}")


def atomic_bytes(filename, data, kind="image"):
    """Never leave a misleading valid-looking final file after interruption."""
    validate_bytes(data, kind)
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    try:
        with temp.open("wb") as out:
            out.write(data)
        # Atomic rename prevents exposing a partially written final file.
        # Not calling fsync for every 256px tile avoids significant overhead
        # on Windows disks (power-loss durability is not guaranteed).
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return path


def safe_folder_component(text):
    # A book ID is not allowed to escape the selected download folder.
    value = str(text).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if value in ("", ".", ".."):
        raise ValueError("Некорректный идентификатор книги")
    return value[:120]


def folder_id(source, book_input):
    """Predict where a resumed book lives; matching adapter naming."""
    value = book_input.strip()
    if source == "PRLIB":
        m = re.search(r"(?:/item/)?(\d+)/?(?:\?.*)?$", value)
        if m:
            value = m.group(1)
    elif source == "RGO":
        segments = urlsplit(value).path.strip("/").split("/")
        if len(segments) >= 3 and segments[0] == "safe-view":
            value = segments[2]
        elif len(segments) >= 3 and segments[0] == "handle":
            value = segments[2]
    elif source == "PDF_READER":
        segments = value.strip("/").split("/")
        if len(segments) >= 2:
            value = segments[-2]
    prefix = "PDFReader" if source == "PDF_READER" else source
    return f"{prefix}_{safe_folder_component(value)}"


class ResumeJournal:
    """Human-readable status, not the authority for deciding what's complete."""

    def __init__(self, folder, source):
        self.path = Path(folder) / ".download_state.json"
        self.source = source
        self.total = None
        self.done = 0
        self.reused = 0

    def save(self, *, status="downloading", message=""):
        data = {
            "version": 1, "source": self.source, "status": status,
            "completed": self.done, "total": self.total, "reused": self.reused,
            "message": message, "updated": int(time.time()),
        }
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temp.open("w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()

    @staticmethod
    def read(folder):
        try:
            with (Path(folder) / ".download_state.json").open(encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None


def stream_pdf(url, destination, *, headers=None, session=None, check_cancel=None,
               progress_callback=None, timeout=35, chunk_size=131072):
    """HTTP Range resume for an incomplete PDF, when the server supports it.

    A partial download is reused only if we have a validator (ETag or
    Last-Modified) to send in If-Range. Otherwise it is restarted to avoid
    combining content from different document versions.
    """
    destination = Path(destination)
    if valid_file(destination, "pdf"):
        return True
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    meta = destination.with_name(destination.name + ".part.json")
    if valid_file(part, "pdf"):
        os.replace(part, destination)
        if meta.exists():
            meta.unlink()
        return True
    request_headers = dict(headers or {})
    client = session or requests
    partial_size = part.stat().st_size if part.exists() else 0
    validator = None
    if partial_size:
        try:
            validator = json.loads(meta.read_text(encoding="utf-8")).get("validator")
        except (OSError, ValueError, AttributeError):
            pass
        if validator:
            request_headers.update({"Range": f"bytes={partial_size}-", "If-Range": validator})
        else:
            partial_size = 0
    if check_cancel:
        check_cancel()
    with client.get(url, headers=request_headers, stream=True, timeout=timeout) as response:
        if response.status_code not in (200, 206):
            raise RuntimeError(f"Ошибка скачивания PDF: HTTP {response.status_code}")
        range_header = response.headers.get("Content-Range", "")
        append = bool(partial_size and response.status_code == 206 and
                      range_header.startswith(f"bytes {partial_size}-"))
        if response.status_code == 206 and not append:
            raise RuntimeError("Сервер прислал неожиданный диапазон PDF")
        if not append:
            partial_size = 0
            etag = response.headers.get("ETag")
            validator = (etag if etag and not etag.startswith("W/") else None)
            validator = validator or response.headers.get("Last-Modified")
            if validator:
                meta.write_text(json.dumps({"validator": validator}), encoding="utf-8")
            elif meta.exists():
                meta.unlink()
        total_size = None
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes \d+-\d+/(\d+)", content_range)
        if match:
            total_size = int(match.group(1))
        elif response.headers.get("Content-Length", "").isdigit():
            total_size = partial_size + int(response.headers["Content-Length"])
        received = partial_size
        with part.open("ab" if append else "wb") as out:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if check_cancel:
                    check_cancel()
                if chunk:
                    out.write(chunk)
                    received += len(chunk)
                    if progress_callback:
                        progress_callback(received, total_size)
        # Preserve .part and validator if the file is still corrupt/truncated.
        if not valid_file(part, "pdf"):
            raise RuntimeError("PDF загружен не полностью; частичный файл сохранён для продолжения")
    os.replace(part, destination)
    if meta.exists():
        meta.unlink()
    return False
