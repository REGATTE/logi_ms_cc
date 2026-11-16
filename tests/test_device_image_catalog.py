"""Exact public-catalog selection and bounded, official-only artwork fetching."""

import json
import time

import pytest

from mxcc import device_image_catalog as catalog


PAGE = "https://www.logitech.com/en-us/shop/p/mx-master-3s"
IMAGE = "https://resource.logitech.com/content/dam/logitech/en/products/mice/mx-master-3s/top.png"


def document(**overrides):
    product = {"@type": "Product", "brand": "Logitech", "name": "MX Master 3S",
               "productID": "mx-master-3s", "url": PAGE, "image": [IMAGE]}
    product.update(overrides)
    return ('<meta property="og:image" content="https://unrelated.invalid/brand.png">'
            '<script type="application/ld+json">' + json.dumps([product]) + '</script>').encode()


def search_payload(*products):
    # Svelte/devalue uses integer references into one per-node value array.
    data = [{"initialApiResults": 1}, {"products": 2}, {"hits": 3}, []]
    for values in products:
        data[3].append(len(data))
        hit = {}
        data.append(hit)
        for key, value in values.items():
            hit[key] = len(data)
            data.append(value)
    return json.dumps({"nodes": [None, {"data": data}]}).encode()


@pytest.mark.parametrize("name,expected", [
    ("Logitech MX Master 3S", "mx master 3s"),
    ("  Logi   MX MASTER 4  ", "mx master 4"),
    ("G502 HERO Gaming Mouse", "g502 hero"),
    ("g502-hero-gaming-mouse", "g502 hero"),
    ("MX Keys S", "mx keys s"),
    ("MX Keys Mini for Mac", "mx keys mini for mac"),
    ("PRO X SUPERLIGHT 2 SE", "pro x superlight 2 se"),
    ("MX Master 3S Bluetooth Edition", "mx master 3s bluetooth edition"),
])
def test_normalization_preserves_model_generations_and_variants(name, expected):
    assert catalog.normalize_model_name(name) == expected


def test_primary_product_requires_exact_identity_not_open_graph_or_recommendations():
    result = catalog._primary_image(document(), PAGE, "mx master 3s")
    assert result == ("https://resource.logitech.com/w_800,c_limit,f_png/d_transparent.gif"
                      "/content/dam/logitech/en/products/mice/mx-master-3s/top.png", "MX Master 3S")
    assert catalog._primary_image(document(), PAGE, "mx master 3") is None
    assert catalog._primary_image(document(), PAGE, "mx master 4") is None
    assert catalog._primary_image(document(), PAGE, "mx master 30s") is None
    assert catalog._primary_image(document(), PAGE + "-other", "mx master 3s") is None
    assert catalog._primary_image(document(brand="Unrelated"), PAGE, "mx master 3s") is None
    assert catalog._primary_image(b'<meta property="og:image" content="' + IMAGE.encode() + b'">', PAGE, "mx master 3s") is None


def test_product_identifier_matches_when_marketing_title_adds_edition():
    assert catalog._primary_image(document(name="MX Master 3S Bluetooth Edition"), PAGE,
                                  "mx master 3s") is not None
    assert catalog._primary_image(document(name="MX Master 3S Bluetooth Edition"), PAGE,
                                  "mx master 3s for mac") is None


def test_short_marketing_name_does_not_erase_real_product_variant():
    page = "https://www.logitechg.com/en-us/shop/p/g502-hero-gaming-mouse"
    payload = document(name="G502", productID="g502-hero-gaming-mouse", url=page)
    assert catalog._primary_image(payload, page, "g502") is None
    assert catalog._primary_image(payload, page, "g502 hero") is not None
    lift = "https://www.logitech.com/en-us/shop/p/lift-vertical-ergonomic-mouse"
    assert catalog._primary_image(document(name="Lift", productID="lift-vertical-ergonomic-mouse", url=lift),
                                  lift, "lift") is not None


def test_ambiguous_display_name_does_not_choose_first_search_hit(monkeypatch):
    hits = [{"title": "Example", "productIdentifier": identifier,
             "brand": "Logitech", "c_url": "https://www.logitech.com/en-us/shop/p/" + identifier}
            for identifier in ("example-ergonomic", "example-compact")]
    calls = []

    def read(url, **kwargs):
        calls.append(url)
        return search_payload(*hits), url

    monkeypatch.setattr(catalog, "_read_url", read)
    assert catalog.fetch_model_image("Example") is None
    assert len(calls) == 1


@pytest.mark.parametrize("image", [
    "https://resource.logitech.com.attacker.invalid/content/dam/wrong.png",
    "http://resource.logitech.com/content/dam/wrong.png",
    "https://resource.logitech.com@attacker.invalid/content/dam/wrong.png",
    "https://resource.logitech.com:9999/content/dam/wrong.png",
    "https://resource.logitech.com/brand.png",
])
def test_image_must_be_an_official_https_product_asset(image):
    assert catalog._primary_image(document(image=[image]), PAGE, "mx master 3s") is None


def test_search_graph_is_read_without_javascript_evaluation_and_bad_refs_are_ignored():
    product = {"title": "MX Master 3S", "productIdentifier": "mx-master-3s",
               "brand": "Logitech", "c_url": PAGE}
    assert list(catalog._search_products(search_payload(product))) == [product]
    malformed = b'{"nodes":[{"data":[{"initialApiResults":-1}]}]}'
    assert list(catalog._search_products(malformed)) == []
    assert list(catalog._search_products(b"")) == []
    assert list(catalog._search_products(b"[]")) == []


def test_fetch_revalidates_search_hit_on_the_product_page(monkeypatch):
    product = {"title": "MX Master 3S", "productIdentifier": "mx-master-3s",
               "brand": "Logitech", "c_url": PAGE}
    calls = []

    def read(url, **limits):
        calls.append((url, limits))
        if "__data.json" in url:
            return search_payload(product), url
        if url == PAGE:
            return document(), url
        assert url.startswith("https://resource.logitech.com/")
        assert limits["limit"] == catalog.MAX_IMAGE_BYTES
        return b"image bytes", url

    monkeypatch.setattr(catalog, "_read_url", read)
    result = catalog.fetch_model_image("Logitech MX Master 3S")
    assert result.data == b"image bytes" and result.product_url == PAGE
    assert result.model_name == "MX Master 3S"
    assert len(calls) == 3
    assert all(item[1]["deadline"] == calls[0][1]["deadline"] for item in calls)


def test_search_cannot_substitute_nearby_generation_or_variant(monkeypatch):
    product = {"title": "MX Master 4", "productIdentifier": "mx-master-4",
               "brand": "Logitech", "c_url": "https://www.logitech.com/en-us/shop/p/mx-master-4"}
    calls = []

    def read(url, **limits):
        calls.append(url)
        if "__data.json" in url:
            return search_payload(product), url
        return document(name="MX Master 4", productID="mx-master-4"), url

    monkeypatch.setattr(catalog, "_read_url", read)
    assert catalog.fetch_model_image("MX Master 3S") is None
    assert calls[1] == PAGE
    assert all("resource.logitech" not in url for url in calls)


def test_offline_and_malformed_catalog_leave_no_image(monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(catalog, "_read_url", offline)
    assert catalog.fetch_model_image("Brio 500") is None
    monkeypatch.setattr(catalog, "_read_url", lambda url, **kw: (b"[]", url))
    assert catalog.fetch_model_image("Brio 500") is None


def test_generic_device_names_do_not_make_network_requests(monkeypatch):
    monkeypatch.setattr(catalog, "_read_url", lambda *a, **kw: pytest.fail("unexpected lookup"))
    for name in ("", "Logitech USB Receiver", "Mouse", "Device", "12345", "a" * 161):
        assert catalog.fetch_model_image(name) is None


def test_redirects_reject_untrusted_hosts_and_excessive_hops():
    handler = catalog._OfficialRedirects(catalog.PAGE_HOSTS)
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "", {}, "http://www.logitech.com/en-us")
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "", {}, "https://attacker.invalid/image")
    handler.count = 3
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "", {}, PAGE)


def test_response_reader_enforces_byte_limit_without_content_length(monkeypatch):
    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def geturl(self):
            return PAGE

        def read(self, size):
            return b"x" * size

    class Opener:
        def open(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(catalog, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="size limit"):
        catalog._read_url(PAGE, hosts=catalog.PAGE_HOSTS, limit=10, deadline=time.monotonic() + 10)
    with pytest.raises(TimeoutError):
        catalog._read_url(PAGE, hosts=catalog.PAGE_HOSTS, limit=10, deadline=time.monotonic() - 1)
