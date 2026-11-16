"""Model artwork loads asynchronously, stays isolated, and survives offline use."""

import json
import os
from threading import Event, Lock, get_ident
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from mxcc.device_image_cache import DeviceImageCache, MAX_IMAGE_BYTES, decode_image, image_key


_APP = None


@pytest.fixture
def qt_app():
    global _APP
    _APP = QApplication.instance() or QApplication(["device-image-cache-test"])
    return _APP


def wait_until(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate(), "Artwork job did not finish within the test deadline"


def png(color="#27ae60", width=64, height=48):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


def result(data=None, *, model="MX Master 3S"):
    return SimpleNamespace(data=png() if data is None else data, model_name=model,
                           product_url="https://www.logitech.com/products/mx-master-3s.html",
                           image_url="https://resource.logitech.com/mx-master-3s.png")


@pytest.fixture
def managers(qt_app, tmp_path):
    created = []

    def create(fetcher, **kwargs):
        cache = DeviceImageCache(directory=tmp_path, fetcher=fetcher, **kwargs)
        created.append(cache)
        return cache

    yield create
    for cache in created:
        cache.stop()
    wait_until(qt_app, lambda: all(cache.running == 0 for cache in created))


def test_requests_are_nonblocking_deduplicated_and_fetch_off_the_ui_thread(qt_app, managers):
    started, release = Event(), Event()
    threads = []
    main_thread = get_ident()

    def fetch(name):
        threads.append(get_ident())
        started.set()
        release.wait(2)
        return result()

    cache = managers(fetch)
    ready = []
    cache.ready.connect(lambda key: ready.append((key, get_ident())))
    before = time.monotonic()
    try:
        key = cache.request(0x046d, 0xb034, "MX Master 3S")
        assert time.monotonic() - before < 0.5
        assert started.wait(1)
        for _ in range(20):
            assert cache.request(0x046d, 0xb034, "MX Master 3S") == key
        ui_callbacks = []
        QTimer.singleShot(0, lambda: ui_callbacks.append(True))
        wait_until(qt_app, lambda: bool(ui_callbacks))
        assert cache.image(key).isNull()
        assert cache.running == 1 and cache.pending == {key}
        assert len(threads) == 1 and threads[0] != main_thread
    finally:
        release.set()
    wait_until(qt_app, lambda: not cache.pending)
    assert ready == [(key, main_thread)]
    assert cache.image(key).pixelColor(0, 0) == QColor("#27ae60")


def test_only_two_downloads_run_concurrently(qt_app, managers):
    release = Event()
    started = []
    lock = Lock()

    def fetch(name):
        with lock:
            started.append(name)
        release.wait(2)
        return result(model=name)

    cache = managers(fetch)
    try:
        keys = [cache.request(0x046d, product, name) for product, name in
                ((0xb034, "MX Master 3S"), (0xb042, "MX Master 4"), (0xc07e, "G402"))]
        wait_until(qt_app, lambda: len(started) == 2)
        assert cache.running == 2 and len(cache.queue) == 1
    finally:
        release.set()
    wait_until(qt_app, lambda: not cache.pending)
    assert len(started) == 3
    assert all(not cache.image(key).isNull() for key in keys)


def test_successful_download_is_reused_by_a_new_offline_manager(qt_app, managers, tmp_path):
    calls = []
    first = managers(lambda name: calls.append(name) or result())
    key = first.request(0x046d, 0xb034, "MX Master 3S")
    wait_until(qt_app, lambda: not first.pending)
    assert (tmp_path / f"{key}.img").is_file()
    assert (tmp_path / f"{key}.json").is_file()

    offline_calls = []
    offline = managers(lambda name: offline_calls.append(name))
    assert offline.request(0x046d, 0xb034, "MX Master 3S", connected=False) == key
    wait_until(qt_app, lambda: not offline.pending)
    assert calls == ["MX Master 3S"] and offline_calls == []
    assert offline.image(key) == first.image(key)


@pytest.mark.parametrize("damage", ["image", "metadata", "different_model"])
def test_corrupt_or_mismatched_disk_cache_is_refetched(qt_app, managers, tmp_path, damage):
    first = managers(lambda name: result())
    key = first.request(0x046d, 0xb034, "MX Master 3S")
    wait_until(qt_app, lambda: not first.pending)
    if damage == "image":
        (tmp_path / f"{key}.img").write_bytes(b"corrupt image")
    elif damage == "metadata":
        (tmp_path / f"{key}.json").write_text("invalid JSON")
    else:
        path = tmp_path / f"{key}.json"
        info = json.loads(path.read_text())
        info["model"] = "another model"
        path.write_text(json.dumps(info))
    calls = []
    repaired = managers(lambda name: calls.append(name) or result(png("#e74c3c")))
    repaired.request(0x046d, 0xb034, "MX Master 3S")
    wait_until(qt_app, lambda: not repaired.pending)
    assert calls == ["MX Master 3S"]
    assert repaired.image(key).pixelColor(0, 0) == QColor("#e74c3c")


@pytest.mark.parametrize("raises", [False, True])
def test_failed_lookup_is_not_repeated_on_polls_or_immediate_reopen(qt_app, managers, raises):
    calls = []

    def fetch(name):
        calls.append(name)
        if raises:
            raise OSError("Network unavailable")

    cache = managers(fetch)
    key = cache.request(0x046d, 0xb034, "MX Master 3S")
    wait_until(qt_app, lambda: not cache.pending)
    for _ in range(20):
        cache.request(0x046d, 0xb034, "MX Master 3S")
    reopened = managers(fetch)
    reopened.request(0x046d, 0xb034, "MX Master 3S")
    wait_until(qt_app, lambda: not reopened.pending)
    assert calls == ["MX Master 3S"]
    assert cache.image(key).isNull() and reopened.image(key).isNull()


@pytest.mark.parametrize("connected", [False, None])
def test_offline_cache_miss_does_not_download_or_delay_reconnection(qt_app, managers, connected, monkeypatch):
    calls = []
    cache = managers(lambda name: calls.append(name) or result())
    disk_reads = []
    original_read = cache._read_cached
    monkeypatch.setattr(cache, "_read_cached", lambda key, name: disk_reads.append(key) or original_read(key, name))
    key = cache.request(0x046d, 0xb034, "MX Master 3S", connected=connected)
    wait_until(qt_app, lambda: not cache.pending)
    assert calls == [] and cache.image(key).isNull()
    for _ in range(20):
        cache.request(0x046d, 0xb034, "MX Master 3S", connected=connected)
    assert not cache.pending and disk_reads == [key]
    cache.request(0x046d, 0xb034, "MX Master 3S", connected=True)
    wait_until(qt_app, lambda: not cache.pending)
    assert calls == ["MX Master 3S"]
    assert not cache.image(key).isNull()


def test_product_and_model_identities_never_share_an_image(qt_app, managers):
    calls = []

    def fetch(name):
        calls.append(name)
        return result(png("#27ae60" if name == "MX Master 3S" else "#e74c3c"), model=name)

    cache = managers(fetch)
    master = cache.request(0x046d, 0xb034, "MX Master 3S")
    g402 = cache.request(0x046d, 0xc07e, "G402")
    wait_until(qt_app, lambda: not cache.pending)
    assert master != g402
    assert cache.image(master) != cache.image(g402)
    assert image_key(0x046d, 0xb034, "MX Master 3S") != image_key(0x046d, 0xb042, "MX Master 3S")
    assert image_key(0x1234, 0xb034, "MX Master 3S") is None
    assert image_key(0x046d, 0xb034, "") is None
    assert set(calls) == {"MX Master 3S", "G402"}


@pytest.mark.parametrize("content", [b"", b"<html>not an image</html>", b"x" * (MAX_IMAGE_BYTES + 1)])
def test_invalid_or_oversized_content_is_rejected(content):
    assert decode_image(content).isNull()


@pytest.mark.parametrize("width,height", [(4097, 32), (4001, 4000)])
def test_excessive_image_dimensions_are_rejected(width, height):
    assert decode_image(png(width=width, height=height)).isNull()


@pytest.mark.parametrize("width,height", [(1, 1), (31, 40), (40, 31)])
def test_tiny_placeholder_images_are_rejected(width, height):
    assert decode_image(png(width=width, height=height)).isNull()


def test_valid_large_image_is_scaled_proportionally():
    image = decode_image(png(width=1600, height=800))
    assert (image.width(), image.height()) == (1200, 600)


def test_invalid_download_never_emits_ready_or_persists_a_photo(qt_app, managers, tmp_path):
    cache = managers(lambda name: result(b"<html>not a photo</html>"))
    ready = []
    cache.ready.connect(ready.append)
    key = cache.request(0x046d, 0xb034, "MX Master 3S")
    wait_until(qt_app, lambda: not cache.pending)
    assert ready == [] and cache.image(key).isNull()
    assert not (tmp_path / f"{key}.img").exists()
    assert not (tmp_path / f"{key}.json").exists()
