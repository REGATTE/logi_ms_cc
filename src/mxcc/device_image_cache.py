"""Download exact model artwork off the UI thread and retain it for offline use."""

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import time

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, Signal, Slot
from PySide6.QtGui import QImage, QImageReader

from .device_image_catalog import fetch_model_image, normalize_model_name
from .paths import cache_dir


MAX_IMAGE_BYTES = 8 * 1024 * 1024
RETRY_SECONDS = 15 * 60


def image_key(vendor, product, model_name):
    """No addresses, serials, or user aliases are used as artwork identities."""
    name = normalize_model_name(model_name)
    if vendor != 0x046d or not name:
        return None
    identity = f"1\0{vendor:04x}:{product:04x}\0{name}"
    return hashlib.sha256(identity.encode()).hexdigest()


def decode_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        return QImage()
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if (not size.isValid() or min(size.width(), size.height()) < 32
            or max(size.width(), size.height()) > 4096
            or size.width() * size.height() > 16_000_000):
        return QImage()
    # Product photos never need more than this in the device card or editor.
    if max(size.width(), size.height()) > 1200:
        size.scale(1200, 1200, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    return reader.read()


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".artwork-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class DeviceImageCache(QObject):
    """Two bounded daemon jobs; no network or image decoding in paint events."""

    ready = Signal(str)
    _finished = Signal(str, object)

    def __init__(self, parent=None, *, enabled=True, directory=None, fetcher=None):
        super().__init__(parent)
        self.enabled = enabled
        self.directory = Path(directory) if directory else cache_dir().parent / "devices"
        self.fetcher = fetcher or fetch_model_image
        self.images = {}
        self.pending = set()
        self.attempted = {}
        self.offline_attempted = {}
        self.queue = deque()
        self.running = 0
        self.closed = False
        self._finished.connect(self._complete)

    def image(self, key):
        return self.images.get(key, QImage())

    def request(self, vendor, product, model_name, *, connected=True):
        key = image_key(vendor, product, model_name)
        if not key or not self.enabled or self.closed:
            return key
        if key in self.images or key in self.pending:
            return key
        attempts = self.attempted if connected is True else self.offline_attempted
        if time.monotonic() - attempts.get(key, -RETRY_SECONDS) < RETRY_SECONDS:
            return key
        self.pending.add(key)
        self.queue.append((key, model_name, connected is True))
        self._start_next()
        return key

    def stop(self):
        self.closed = True
        self.queue.clear()

    def _start_next(self):
        while self.queue and self.running < 2 and not self.closed:
            key, name, download = self.queue.popleft()
            attempts = self.attempted if download else self.offline_attempted
            attempts[key] = time.monotonic()
            self.running += 1
            Thread(target=self._load, args=(key, name, download), daemon=True,
                   name="mxcc-device-image").start()

    def _read_cached(self, key, name):
        metadata = self.directory / f"{key}.json"
        path = self.directory / f"{key}.img"
        try:
            if metadata.stat().st_size > 8192 or path.stat().st_size > MAX_IMAGE_BYTES:
                return QImage()
            info = json.loads(metadata.read_text())
            if info.get("version") != 1 or info.get("model") != normalize_model_name(name):
                return QImage()
            data = path.read_bytes()
            if info.get("sha256") != hashlib.sha256(data).hexdigest():
                return QImage()
            return decode_image(data)
        except (OSError, ValueError, AttributeError):
            return QImage()

    def _recent_miss(self, key):
        try:
            age = time.time() - (self.directory / f"{key}.miss").stat().st_mtime
            return 0 <= age < RETRY_SECONDS
        except OSError:
            return False

    def _load(self, key, name, download):
        image = QImage()
        attempted_fetch = False
        try:
            image = self._read_cached(key, name)
            if image.isNull() and download and not self._recent_miss(key):
                attempted_fetch = True
                result = self.fetcher(name)
                if result is not None:
                    image = decode_image(result.data)
                    if not image.isNull():
                        info = {"version": 1, "model": normalize_model_name(name),
                                "matched_name": result.model_name,
                                "sha256": hashlib.sha256(result.data).hexdigest(),
                                "product_url": result.product_url, "image_url": result.image_url}
                        try:
                            _atomic_write(self.directory / f"{key}.img", result.data)
                            _atomic_write(self.directory / f"{key}.json", json.dumps(info).encode())
                        except OSError:
                            pass  # A read-only/full cache must not hide a valid image.
        except Exception:
            # Network, parsing and image failures retain the neutral placeholder.
            pass
        if attempted_fetch and image.isNull():
            try:
                _atomic_write(self.directory / f"{key}.miss", b"")
            except OSError:
                pass
        try:
            self._finished.emit(key, image)
        except RuntimeError:
            pass  # Settings closed while the bounded download was in progress.

    @Slot(str, object)
    def _complete(self, key, image):
        self.running -= 1
        self.pending.discard(key)
        if not self.closed and not image.isNull():
            self.images[key] = image
            self.ready.emit(key)
        self._start_next()
