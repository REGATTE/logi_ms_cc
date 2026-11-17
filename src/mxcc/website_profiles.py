"""Portable website overrides, matched by exact canonical hostname only."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from .actions import compile_device_action, validate_device_actions
from .ring_configuration import compile_ring, validate_ring


MAX_WEBSITE_PROFILES = 128
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_LAYOUT_PREFIX = "website:"


def validate_domain(value: object) -> str:
    """Reject noncanonical hosts so saved rules match the worker identically."""
    if (not isinstance(value, str) or not value or len(value) > 253
            or not value.isascii() or any(not _LABEL.fullmatch(label) for label in value.split("."))):
        raise ValueError("Website domains must be lowercase hostnames, without a path, port or wildcard")
    return value


def normalize_domain(value: str) -> str:
    """Normalize user input; URLs select their hostname, never a path or port."""
    if (not isinstance(value, str) or not value.strip() or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("Enter a website hostname or an http(s) URL")
    value = value.strip()
    if "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme.lower() not in ("http", "https") or parsed.username is not None or parsed.password is not None:
                raise ValueError
            host = parsed.hostname
            # Validate any explicit port rather than silently accepting broken URLs.
            _ = parsed.port
        except ValueError as error:
            raise ValueError("Enter an http(s) URL without a username or password") from error
        if host is None:
            raise ValueError("The website URL needs a hostname")
    else:
        host = value
    try:
        host = unicodedata.normalize("NFC", host.lower())
        canonical = host.encode("idna").decode("ascii")
        # The standard-library codec uses IDNA2003. Do not silently turn a
        # modern-browser hostname such as faß.de into the different fass.de.
        if not host.isascii() and canonical.encode("ascii").decode("idna") != host:
            raise ValueError("Use this international website's ASCII (xn--) hostname")
    except UnicodeError as error:
        raise ValueError("Enter a valid website hostname") from error
    return validate_domain(canonical)


def website_layout_id(domain: str) -> str:
    return _LAYOUT_PREFIX + validate_domain(domain)


def domain_from_layout_id(identifier: object) -> str | None:
    """Return None for application/default IDs; reject malformed website IDs."""
    if not isinstance(identifier, str) or not identifier.startswith(_LAYOUT_PREFIX):
        return None
    return validate_domain(identifier[len(_LAYOUT_PREFIX):])


def validate_website_profiles(values: object) -> list[dict]:
    if not isinstance(values, list) or len(values) > MAX_WEBSITE_PROFILES:
        raise ValueError("At most 128 website profiles are supported")
    result = []
    seen = set()
    for profile in values:
        if not isinstance(profile, dict) or set(profile) != {"domain", "device_actions", "ring"}:
            raise ValueError("Website profiles require domain, device_actions and ring")
        domain = validate_domain(profile["domain"])
        if domain in seen:
            raise ValueError("Duplicate website domain")
        seen.add(domain)
        assignments = validate_device_actions(profile["device_actions"])
        ring = profile["ring"]
        if ring is not None:
            # Reuse the action schema: ring actions cannot recursively open rings.
            ring = validate_ring({"layouts": {"default": ring}, "bindings": []})["layouts"]["default"]
        result.append({"domain": domain, "device_actions": assignments, "ring": ring})
    return result


def compile_website_profiles(values, apps, selections) -> list[dict]:
    """Resolve local app actions while retaining exact-domain override scopes."""
    apps = list(apps)
    indexed = {app.id: app for app in apps}
    result = []
    for profile in validate_website_profiles(values):
        assignments = []
        for item in profile["device_actions"]:
            compiled = compile_device_action(item, indexed, selections)
            if compiled is not None:
                assignments.append(compiled)
        ring = None
        if profile["ring"] is not None:
            ring = compile_ring({"layouts": {"default": profile["ring"]}, "bindings": []}, apps, selections)["layouts"][0]
            ring["id"] = website_layout_id(profile["domain"])
        result.append({"domain": profile["domain"], "device_actions": assignments, "ring": ring})
    return result
