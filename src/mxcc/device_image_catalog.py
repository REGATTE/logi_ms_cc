"""Bounded, exact-model artwork lookup from Logitech's public product catalog.

The official search page's Svelte data supplies candidate product URLs. Only a
matching primary Product JSON-LD object may supply an image; search thumbnails,
recommendations and generic Open Graph images are deliberately ignored. This
best-effort catalog can omit discontinued or unusually named models. No match
is preferable to a picture of a different model or generation.

This module performs blocking I/O and must only run in a background thread.
It receives a hardware model name, never a Bluetooth address or user alias.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PAGE_HOSTS = frozenset(("www.logitech.com", "www.logitechg.com", "www.logi.com", "logi.com"))
IMAGE_HOSTS = frozenset(("resource.logitech.com", "resource.logitechg.com"))
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_REDIRECTS = 3
_GENERIC_SUFFIX = re.compile(
    r"(?:\s+(?:wireless gaming mouse|gaming mouse|wireless mouse|"
    r"wireless keyboard|gaming keyboard|usb keyboard|mouse|keyboard|webcam|headset))$"
)


@dataclass(frozen=True)
class DownloadedImage:
    data: bytes
    image_url: str
    product_url: str
    model_name: str


def normalize_model_name(name: str) -> str:
    """Normalize spelling only; retain generation, edition and variant tokens."""
    if not isinstance(name, str) or len(name) > 160:
        return ""
    name = unicodedata.normalize("NFKC", name).casefold()
    name = re.sub(r"^(?:logitech|logi)\s+", "", name.strip())
    name = " ".join(re.sub(r"[^a-z0-9]+", " ", name).split())
    return _GENERIC_SUFFIX.sub("", name).strip()


def _allowed_url(url: str, hosts: frozenset[str]) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in hosts
                and parts.username is None and parts.password is None
                and parts.port in (None, 443) and not parts.fragment)
    except (TypeError, ValueError):
        return False


class _OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts
        self.count = 0

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > _MAX_REDIRECTS or not _allowed_url(newurl, self.hosts):
            raise ValueError("Product artwork redirect is not allowed")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _read_url(url: str, *, hosts, limit: int, deadline: float) -> tuple[bytes, str]:
    if not _allowed_url(url, hosts):
        raise ValueError("Product artwork URL is not allowed")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Product artwork lookup expired")
    opener = build_opener(_OfficialRedirects(hosts))
    request = Request(url, headers={"User-Agent": "MX-Creative-Control/0.3 device-artwork",
                                    "Accept": "application/json,text/html,image/png,*/*;q=0.5"})
    with opener.open(request, timeout=min(5.0, remaining)) as response:
        final_url = response.geturl()
        if not _allowed_url(final_url, hosts):
            raise ValueError("Product artwork response URL is not allowed")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > limit:
            raise ValueError("Product artwork response exceeds its size limit")
        chunks, size = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Product artwork lookup expired")
            chunk = response.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise ValueError("Product artwork response exceeds its size limit")
        return b"".join(chunks), final_url


def _graph_value(data, reference):
    if type(reference) is not int or reference < 0 or reference >= len(data):
        return None
    return data[reference]


def _search_products(payload: bytes):
    """Read a few known Svelte graph fields without evaluating JavaScript."""
    lines = payload.decode("utf-8").splitlines()
    document = json.loads(lines[0]) if lines else {}
    if not isinstance(document, dict) or not isinstance(document.get("nodes", []), list):
        return
    for node in document.get("nodes", ()):
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            continue
        value = data[0]
        for field in ("initialApiResults", "products", "hits"):
            value = _graph_value(data, value.get(field)) if isinstance(value, dict) else None
        if not isinstance(value, list):
            continue
        for reference in value[:24]:
            hit = _graph_value(data, reference)
            if isinstance(hit, dict):
                yield {key: _graph_value(data, hit.get(key))
                       for key in ("title", "productIdentifier", "brand", "c_url")}


def _same_model(expected: str, *names) -> bool:
    return any(isinstance(name, str) and normalize_model_name(name) == expected for name in names)


def _matching_product(expected: str, title, identifier) -> bool:
    if _same_model(expected, identifier):
        return True
    if not _same_model(expected, title) or not isinstance(identifier, str):
        return False
    # Catalog slugs sometimes append descriptive words (Lift's slug is
    # lift-vertical-ergonomic-mouse). Marketing titles can also omit a real
    # variant: G502 HERO is titled only G502. Never discard HERO, LIGHTSPEED,
    # generation numbers, Mini, Plus, SE, business/Mac editions, etc.
    slug = normalize_model_name(identifier)
    if not slug.startswith(expected + " "):
        return False
    description = slug[len(expected) + 1:].split()
    return bool(description) and set(description) <= {
        "vertical", "ergonomic", "advanced", "compact", "performance",
        "wireless", "wired", "gaming", "mouse", "keyboard", "webcam",
        "headset", "trackball", "illuminated", "minimalist",
    }


def _product_url(url) -> bool:
    return (isinstance(url, str) and _allowed_url(url, PAGE_HOSTS)
            and re.fullmatch(r"/en-us/(?:business/)?shop/p/[a-zA-Z0-9.-]+/?", urlsplit(url).path) is not None
            and not urlsplit(url).query)


def _page_identity(url):
    """Variant SKUs share a model page; they do not identify a USB/HID model."""
    parts = urlsplit(url)
    path = re.sub(r"\.[0-9]{3}-[0-9]+/?$", "", parts.path.rstrip("/"))
    return parts.hostname, path


class _ProductJson(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.text = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.inside = dict(attrs).get("type", "").casefold() == "application/ld+json"
            self.text = []

    def handle_data(self, data):
        if self.inside:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.inside:
            self.inside = False
            try:
                self.documents.append(json.loads("".join(self.text)))
            except (ValueError, RecursionError):
                pass


def _product_objects(document):
    if isinstance(document, list):
        for item in document:
            if isinstance(item, dict):
                yield from _product_objects(item)
    elif isinstance(document, dict):
        if document.get("@type") == "Product":
            yield document
        # Do not descend into offers, recommendations or ProductModel variants.
        graph = document.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    yield item


def _primary_image(payload: bytes, page_url: str, model: str):
    parser = _ProductJson()
    parser.feed(payload.decode("utf-8"))
    for document in parser.documents:
        for product in _product_objects(document):
            brand = product.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            if brand not in ("Logitech", "Logitech G", "Logi"):
                continue
            if not _matching_product(model, product.get("name"), product.get("productID")):
                continue
            identity = product.get("url") or product.get("@id")
            if not _product_url(identity) or _page_identity(identity) != _page_identity(page_url):
                continue
            images = product.get("image", [])
            if isinstance(images, (str, dict)):
                images = [images]
            if not isinstance(images, list):
                continue
            for candidate in images[:12]:
                if isinstance(candidate, dict):
                    candidate = candidate.get("contentUrl") or candidate.get("url")
                if not isinstance(candidate, str) or not _allowed_url(candidate, IMAGE_HOSTS):
                    continue
                parts = urlsplit(candidate)
                offset = parts.path.find("/content/dam/")
                if offset < 0 or parts.query:
                    continue
                # Logitech's public CDN transform keeps a small transparent PNG.
                image_url = urlunsplit(("https", parts.netloc,
                    "/w_800,c_limit,f_png/d_transparent.gif" + parts.path[offset:], "", ""))
                return image_url, product.get("name") or model
    return None


def fetch_model_image(name: str) -> DownloadedImage | None:
    """Download an exact official model image, or return None on any lookup miss.

    At most two product pages, one official search and two images are fetched,
    within a 20-second budget. A direct model-slug fallback helps when search
    omits a discontinued device; page identity is checked again in every case.
    Caller must validate the image dimensions/decoding before caching it.
    """
    model = normalize_model_name(name)
    if (not model or len(model) < 3 or model in {"mouse", "keyboard", "receiver", "usb receiver", "device"}
            or not re.search(r"[a-z]", model)):
        return None
    deadline = time.monotonic() + 20
    candidates = []
    try:
        query = urlencode({"q": name, "limit": 12})
        payload, _ = _read_url("https://www.logitech.com/en-us/search/__data.json?" + query,
                               hosts=PAGE_HOSTS, limit=MAX_DOCUMENT_BYTES, deadline=deadline)
        matches = []
        for product in _search_products(payload):
            url = product.get("c_url")
            if (product.get("brand") in ("Logitech", "Logitech G", "Logi")
                    and _matching_product(model, product.get("title"), product.get("productIdentifier"))
                    and _product_url(url)):
                matches.append(product)
        exact_ids = [hit for hit in matches if _same_model(model, hit.get("productIdentifier"))]
        chosen = exact_ids or matches
        # A short display name must not arbitrarily select one of several
        # differently identified models. The full product ID wins when known.
        if len({hit["productIdentifier"] for hit in chosen}) > 1:
            return None
        candidates.extend(dict.fromkeys(hit["c_url"] for hit in chosen))
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, HTTPError, URLError):
        pass
    direct = "https://www.logitech.com/en-us/shop/p/" + quote(model.replace(" ", "-"), safe="-")
    if direct not in candidates:
        candidates.append(direct)
    for page in candidates[:2]:
        try:
            payload, final_page = _read_url(page, hosts=PAGE_HOSTS, limit=MAX_DOCUMENT_BYTES, deadline=deadline)
            match = _primary_image(payload, final_page, model)
            if match is None:
                continue
            image_url, official_name = match
            data, final_image = _read_url(image_url, hosts=IMAGE_HOSTS, limit=MAX_IMAGE_BYTES, deadline=deadline)
            if not data:
                return None
            return DownloadedImage(data, final_image, final_page, official_name)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, HTTPError, URLError):
            continue
    return None
